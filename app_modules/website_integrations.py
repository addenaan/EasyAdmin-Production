"""Generic, tenant-bound website integrations for Easy Admin.

The integration API is mounted beside the normal Easy Admin Flask application.
Every request is authenticated with a per-integration HMAC credential. The
credential record supplies the tenant ``company_id`` and permitted scopes; the
caller cannot select or override a tenant in the request payload.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from cryptography.fernet import Fernet, InvalidToken
from flask import Flask, g, jsonify, request

INTEGRATION_PREFIX = "/api/integrations/v1"
KEY_ID_RE = re.compile(r"^[A-Za-z0-9._-]{8,100}$")
NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{16,96}$")
SIGNATURE_RE = re.compile(r"^[0-9a-fA-F]{64}$")
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
PHONE_RE = re.compile(r"^[0-9+()\-\s]{7,30}$")
ALLOWED_PRICE_MODES = {"exclusive_vat", "inclusive_vat", "no_vat"}
DEFAULT_SCOPES = {"catalog:read", "quote:create"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _utcnow()).isoformat(timespec="seconds")


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _clean(value: Any, max_length: int) -> str:
    return " ".join(str(value or "").strip().split())[:max_length]


def _money(value: Any) -> float:
    try:
        return round(max(float(value or 0), 0.0), 2)
    except (TypeError, ValueError):
        return 0.0


def _quantity(value: Any) -> float:
    try:
        return round(float(value), 4)
    except (TypeError, ValueError):
        return 0.0


def _row_dict(row: Any) -> dict[str, Any]:
    return dict(row) if row is not None else {}


def _canonical_request_path() -> str:
    script_root = (request.script_root or "").rstrip("/")
    path = request.path if request.path.startswith("/") else f"/{request.path}"
    return f"{script_root}{path}" or path


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_scopes(value: Any) -> set[str]:
    if isinstance(value, (list, tuple, set)):
        return {str(item).strip() for item in value if str(item).strip()}
    text = str(value or "").strip()
    if not text:
        return set()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return {str(item).strip() for item in parsed if str(item).strip()}
    except (TypeError, ValueError):
        pass
    return {item.strip() for item in text.split(",") if item.strip()}


def _price_values(client_price: Any, mode: str, vat_rate: float) -> dict[str, float | bool]:
    stored = _money(client_price)
    if mode == "inclusive_vat":
        gross = stored
        net = round(gross / (1 + vat_rate), 2) if vat_rate else gross
        vat = round(gross - net, 2)
        return {"net": net, "vat": vat, "gross": gross, "vat_applies": bool(vat_rate)}
    if mode == "no_vat":
        return {"net": stored, "vat": 0.0, "gross": stored, "vat_applies": False}
    net = stored
    vat = round(net * vat_rate, 2)
    return {"net": net, "vat": vat, "gross": round(net + vat, 2), "vat_applies": bool(vat_rate)}


def _infer_category(name: str) -> str:
    text = (name or "").lower()
    rules = [
        ("vibracrete", "Vibracrete"),
        ("paver", "Paving"),
        ("paving", "Paving"),
        ("brick", "Bricks"),
        ("block", "Blocks"),
        ("sand", "Sand"),
        ("stone", "Stone"),
        ("cement", "Cement"),
    ]
    for token, category in rules:
        if token in text:
            return category
    return "Products"


def _client_display_name(row: Any) -> str:
    data = _row_dict(row)
    person = " ".join(part for part in [data.get("name"), data.get("surname")] if part).strip()
    return (data.get("company_name") or person or "Website Customer").strip()


def _quote_reference(conn: Any, company_id: int, quote_id: int) -> str:
    try:
        row = conn.execute(
            "SELECT quote_number FROM quotes WHERE id=? AND company_id=?",
            (quote_id, company_id),
        ).fetchone()
        reference = str(_row_dict(row).get("quote_number") or "").strip()
        if reference:
            return reference
    except Exception:
        pass
    return f"QT-{int(quote_id):04d}"


class IntegrationConfigurationError(RuntimeError):
    """Raised when the generic integration framework is not configured safely."""


class SecretCipher:
    def __init__(self, key: str | bytes) -> None:
        raw = key.encode("utf-8") if isinstance(key, str) else key
        try:
            self._fernet = Fernet(raw)
        except Exception as exc:
            raise IntegrationConfigurationError(
                "EASYADMIN_INTEGRATION_MASTER_KEY must be a valid Fernet key."
            ) from exc

    @staticmethod
    def generate_master_key() -> str:
        return Fernet.generate_key().decode("ascii")

    def encrypt(self, secret: str) -> str:
        if len(secret) < 32:
            raise ValueError("Integration secrets must be at least 32 characters.")
        return self._fernet.encrypt(secret.encode("utf-8")).decode("ascii")

    def decrypt(self, ciphertext: str) -> str:
        try:
            return self._fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError, UnicodeError) as exc:
            raise IntegrationConfigurationError("Stored integration secret could not be decrypted.") from exc


class _RateLimiter:
    def __init__(self) -> None:
        self._buckets: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str, limit: int, window: int) -> tuple[bool, int]:
        now = time.time()
        cutoff = now - window
        with self._lock:
            bucket = self._buckets[key]
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= limit:
                return False, max(1, int(window - (now - bucket[0])))
            bucket.append(now)
            return True, 0


_RATE_LIMITER = _RateLimiter()
_SCHEMA_LOCK = threading.Lock()


def _execute_schema_statement(easyadmin: Any, statement: str) -> None:
    """Execute one DDL statement in its own transaction.

    PostgreSQL marks a transaction as failed after duplicate-column errors, so
    each schema statement is committed or rolled back independently.
    """
    conn = easyadmin.get_db_connection()
    try:
        try:
            conn.execute(statement)
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
    finally:
        conn.close()


def ensure_integration_schema(easyadmin: Any) -> None:
    """Create the generic integration tables and quote metadata columns."""
    with _SCHEMA_LOCK:
        statements = [
            """CREATE TABLE IF NOT EXISTS website_integrations (
                key_id TEXT PRIMARY KEY,
                company_id INTEGER NOT NULL,
                integration_name TEXT NOT NULL,
                secret_ciphertext TEXT NOT NULL,
                previous_secret_ciphertext TEXT,
                previous_secret_expires_at TIMESTAMP,
                enabled INTEGER DEFAULT 1,
                scopes TEXT NOT NULL,
                price_mode TEXT DEFAULT 'exclusive_vat',
                vat_rate REAL DEFAULT 0.15,
                quote_valid_days INTEGER DEFAULT 14,
                quote_status TEXT DEFAULT 'Pending',
                source_label TEXT DEFAULT 'Website Integration',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_used_at TIMESTAMP,
                expires_at TIMESTAMP,
                secret_rotated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""",
            """CREATE TABLE IF NOT EXISTS website_integration_products (
                integration_key_id TEXT NOT NULL,
                service_id INTEGER NOT NULL,
                public_id TEXT NOT NULL,
                enabled INTEGER DEFAULT 1,
                display_name TEXT,
                website_description TEXT,
                website_category TEXT,
                website_unit TEXT DEFAULT 'each',
                minimum_quantity REAL DEFAULT 1,
                sort_order INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (integration_key_id, service_id),
                UNIQUE (integration_key_id, public_id)
            )""",
            """CREATE TABLE IF NOT EXISTS website_api_nonces (
                integration_key_id TEXT NOT NULL,
                nonce TEXT NOT NULL,
                used_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (integration_key_id, nonce)
            )""",
            """CREATE TABLE IF NOT EXISTS website_quote_requests (
                integration_key_id TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                request_hash TEXT,
                quote_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (integration_key_id, idempotency_key)
            )""",
            "ALTER TABLE quotes ADD COLUMN client_id INTEGER",
            "ALTER TABLE quotes ADD COLUMN source TEXT",
            "ALTER TABLE quotes ADD COLUMN external_request_id TEXT",
            "ALTER TABLE quotes ADD COLUMN website_notes TEXT",
            "ALTER TABLE quotes ADD COLUMN website_customer_email TEXT",
            "ALTER TABLE quotes ADD COLUMN website_customer_phone TEXT",
            "ALTER TABLE quotes ADD COLUMN website_delivery_address TEXT",
            "ALTER TABLE quotes ADD COLUMN website_delivery_area TEXT",
            "ALTER TABLE website_integrations ADD COLUMN is_default INTEGER DEFAULT 0",
            "CREATE INDEX IF NOT EXISTS idx_website_integrations_company ON website_integrations(company_id)",
            "CREATE INDEX IF NOT EXISTS idx_website_integrations_company_default ON website_integrations(company_id, is_default, enabled)",
            "CREATE INDEX IF NOT EXISTS idx_website_products_key_enabled ON website_integration_products(integration_key_id, enabled)",
            "CREATE INDEX IF NOT EXISTS idx_website_nonces_used_at ON website_api_nonces(used_at)",
            "CREATE INDEX IF NOT EXISTS idx_website_quote_requests_quote ON website_quote_requests(quote_id)",
        ]
        for statement in statements:
            _execute_schema_statement(easyadmin, statement)

        # Backfill one default integration per tenant without replacing any
        # existing credential. This keeps CLI-created integrations compatible
        # with the company-screen workflow introduced later.
        conn = easyadmin.get_db_connection()
        try:
            company_rows = conn.execute(
                "SELECT DISTINCT company_id FROM website_integrations WHERE company_id IS NOT NULL"
            ).fetchall()
            for company_row in company_rows:
                company_id = int(_row_dict(company_row).get("company_id") or 0)
                if not company_id:
                    continue
                existing_default = conn.execute(
                    "SELECT key_id FROM website_integrations WHERE company_id=? AND COALESCE(is_default,0)=1 LIMIT 1",
                    (company_id,),
                ).fetchone()
                if existing_default:
                    continue
                selected = conn.execute(
                    """SELECT key_id FROM website_integrations
                       WHERE company_id=?
                       ORDER BY COALESCE(enabled,0) DESC, key_id ASC
                       LIMIT 1""",
                    (company_id,),
                ).fetchone()
                if selected:
                    conn.execute(
                        "UPDATE website_integrations SET is_default=1 WHERE key_id=?",
                        (_row_dict(selected).get("key_id"),),
                    )
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
        finally:
            conn.close()


def _slug_key_component(value: Any, max_length: int = 40) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-")
    return (text or "tenant")[:max_length].rstrip("-") or "tenant"


def get_default_website_integration(
    conn: Any, company_id: int, *, enabled_only: bool = False
) -> dict[str, Any] | None:
    """Return the tenant's default website integration, if configured."""
    where = "company_id=?"
    params: list[Any] = [int(company_id)]
    if enabled_only:
        where += " AND COALESCE(enabled,0)=1"
    row = conn.execute(
        f"""SELECT * FROM website_integrations
            WHERE {where}
            ORDER BY COALESCE(is_default,0) DESC, COALESCE(enabled,0) DESC, key_id ASC
            LIMIT 1""",
        tuple(params),
    ).fetchone()
    return _row_dict(row) or None


def configure_company_website_integration(
    conn: Any,
    company_id: int,
    company_name: str,
    enabled: bool,
) -> dict[str, Any]:
    """Create, enable or disable the tenant's default website integration.

    The returned plaintext secret is present only when a new credential is
    generated. Callers must display it once and never persist it unencrypted.
    """
    company_id = int(company_id)
    existing = get_default_website_integration(conn, company_id, enabled_only=False)
    now = _iso()
    if existing:
        conn.execute(
            "UPDATE website_integrations SET is_default=0 WHERE company_id=? AND key_id<>?",
            (company_id, existing["key_id"]),
        )
        conn.execute(
            "UPDATE website_integrations SET enabled=?, is_default=1, updated_at=? WHERE key_id=? AND company_id=?",
            (1 if enabled else 0, now, existing["key_id"], company_id),
        )
        existing.update({"enabled": 1 if enabled else 0, "is_default": 1, "updated_at": now})
        return {"integration": existing, "secret": None, "created": False}

    if not enabled:
        return {"integration": None, "secret": None, "created": False}

    master_key = os.getenv("EASYADMIN_INTEGRATION_MASTER_KEY", "").strip()
    if not master_key:
        raise IntegrationConfigurationError(
            "EASYADMIN_INTEGRATION_MASTER_KEY is required before website integration can be enabled."
        )
    cipher = SecretCipher(master_key)
    secret = secrets.token_urlsafe(48)
    key_id = f"web-{company_id}-{_slug_key_component(company_name, 32)}-{secrets.token_hex(4)}"[:100]
    if not KEY_ID_RE.fullmatch(key_id):
        key_id = f"web-{company_id}-{secrets.token_hex(8)}"
    integration_name = f"{_clean(company_name, 100)} Website"
    source_label = f"{_clean(company_name, 90)} Website"
    conn.execute(
        "UPDATE website_integrations SET is_default=0 WHERE company_id=?",
        (company_id,),
    )
    conn.execute(
        """INSERT INTO website_integrations
           (key_id, company_id, integration_name, secret_ciphertext, enabled, scopes,
            price_mode, vat_rate, quote_valid_days, quote_status, source_label,
            created_at, updated_at, secret_rotated_at, is_default)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            key_id,
            company_id,
            integration_name,
            cipher.encrypt(secret),
            1,
            json.dumps(sorted(DEFAULT_SCOPES)),
            "exclusive_vat",
            0.15,
            14,
            "Pending",
            source_label,
            now,
            now,
            now,
            1,
        ),
    )
    integration = get_default_website_integration(conn, company_id, enabled_only=False)
    return {"integration": integration, "secret": secret, "created": True}


def rotate_company_website_integration_secret(
    conn: Any, company_id: int, *, grace_minutes: int = 15
) -> dict[str, Any]:
    integration = get_default_website_integration(conn, int(company_id), enabled_only=False)
    if not integration:
        raise ValueError("Website integration has not been enabled for this company.")
    master_key = os.getenv("EASYADMIN_INTEGRATION_MASTER_KEY", "").strip()
    if not master_key:
        raise IntegrationConfigurationError(
            "EASYADMIN_INTEGRATION_MASTER_KEY is required before credentials can be rotated."
        )
    cipher = SecretCipher(master_key)
    secret = secrets.token_urlsafe(48)
    now = _utcnow()
    previous_expires = now + timedelta(minutes=max(0, int(grace_minutes or 0)))
    conn.execute(
        """UPDATE website_integrations
           SET previous_secret_ciphertext=?, previous_secret_expires_at=?,
               secret_ciphertext=?, secret_rotated_at=?, updated_at=?
           WHERE key_id=? AND company_id=?""",
        (
            integration.get("secret_ciphertext"),
            _iso(previous_expires),
            cipher.encrypt(secret),
            _iso(now),
            _iso(now),
            integration["key_id"],
            int(company_id),
        ),
    )
    integration.update({"secret_rotated_at": _iso(now), "updated_at": _iso(now)})
    return {"integration": integration, "secret": secret, "grace_minutes": max(0, int(grace_minutes or 0))}


def set_service_website_publication(
    conn: Any,
    integration: dict[str, Any],
    service_id: int,
    service_name: str,
    enabled: bool,
    *,
    display_name: str = "",
    description: str = "",
    category: str = "",
    unit: str = "each",
    minimum_quantity: float = 1,
    sort_order: int = 0,
) -> dict[str, Any] | None:
    """Create or update one public product mapping for the default integration."""
    key_id = str(integration.get("key_id") or "")
    if not key_id:
        raise ValueError("Website integration key is missing.")
    company_id = int(integration.get("company_id") or 0)
    tenant_service = conn.execute(
        "SELECT id FROM services WHERE id=? AND company_id=?",
        (int(service_id), company_id),
    ).fetchone()
    if not tenant_service:
        raise ValueError("The service does not belong to the website integration tenant.")
    existing = conn.execute(
        "SELECT * FROM website_integration_products WHERE integration_key_id=? AND service_id=?",
        (key_id, int(service_id)),
    ).fetchone()
    existing_data = _row_dict(existing)
    if not enabled and not existing_data:
        return None
    now = _iso()
    public_id = existing_data.get("public_id") or f"prd_{uuid.uuid4().hex}"
    resolved_display = _clean(display_name or service_name, 160)
    resolved_description = _clean(description, 1000)
    resolved_category = _clean(category or _infer_category(service_name), 80)
    resolved_unit = _clean(unit or "each", 40) or "each"
    resolved_minimum = max(_quantity(minimum_quantity) or 1, 0.0001)
    try:
        resolved_sort = max(0, min(999999, int(sort_order or 0)))
    except (TypeError, ValueError):
        resolved_sort = 0
    if existing_data:
        conn.execute(
            """UPDATE website_integration_products
               SET enabled=?, display_name=?, website_description=?, website_category=?,
                   website_unit=?, minimum_quantity=?, sort_order=?, updated_at=?
               WHERE integration_key_id=? AND service_id=?""",
            (
                1 if enabled else 0,
                resolved_display,
                resolved_description,
                resolved_category,
                resolved_unit,
                resolved_minimum,
                resolved_sort,
                now,
                key_id,
                int(service_id),
            ),
        )
    else:
        conn.execute(
            """INSERT INTO website_integration_products
               (integration_key_id, service_id, public_id, enabled, display_name,
                website_description, website_category, website_unit, minimum_quantity,
                sort_order, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                key_id,
                int(service_id),
                public_id,
                1 if enabled else 0,
                resolved_display,
                resolved_description,
                resolved_category,
                resolved_unit,
                resolved_minimum,
                resolved_sort,
                now,
                now,
            ),
        )
    row = conn.execute(
        "SELECT * FROM website_integration_products WHERE integration_key_id=? AND service_id=?",
        (key_id, int(service_id)),
    ).fetchone()
    return _row_dict(row) or None


def _audit(
    easyadmin: Any,
    integration: dict[str, Any] | None,
    action: str,
    details: Any,
    *,
    result: str = "success",
    record_type: str | None = None,
    record_id: Any = None,
) -> None:
    integration = integration or {}
    company_id = integration.get("company_id")
    key_id = integration.get("key_id") or "unknown"
    safe_details = details if isinstance(details, dict) else {"message": _clean(details, 1000)}
    safe_details = {**safe_details, "integration_key_id": key_id}
    try:
        easyadmin.log_action(
            "Website Integration",
            action,
            safe_details,
            record_type=record_type,
            record_id=record_id,
            result=result,
            event_type="security" if result != "success" else "application",
            company_id=int(company_id) if company_id else None,
            username=f"website:{key_id}",
        )
    except Exception:
        pass


def _load_integration(easyadmin: Any, key_id: str) -> dict[str, Any] | None:
    conn = easyadmin.get_db_connection()
    try:
        row = conn.execute(
            "SELECT * FROM website_integrations WHERE key_id=?",
            (key_id,),
        ).fetchone()
        return _row_dict(row) if row else None
    finally:
        conn.close()


def _active_secrets(integration: dict[str, Any], cipher: SecretCipher) -> list[str]:
    values = [cipher.decrypt(str(integration["secret_ciphertext"]))]
    previous_ciphertext = integration.get("previous_secret_ciphertext")
    previous_expires = _parse_datetime(integration.get("previous_secret_expires_at"))
    if previous_ciphertext and previous_expires and previous_expires > _utcnow():
        values.append(cipher.decrypt(str(previous_ciphertext)))
    return values


def _verify_hmac(secret_values: Iterable[str]) -> bool:
    timestamp = request.headers.get("X-EasyAdmin-Timestamp", "")
    nonce = request.headers.get("X-EasyAdmin-Nonce", "")
    supplied_signature = request.headers.get("X-EasyAdmin-Signature", "")
    if not NONCE_RE.fullmatch(nonce) or not SIGNATURE_RE.fullmatch(supplied_signature or ""):
        return False
    raw_body = request.get_data(cache=True) or b""
    body_hash = hashlib.sha256(raw_body).hexdigest()
    canonical = "\n".join(
        [request.method.upper(), _canonical_request_path(), timestamp, nonce, body_hash]
    ).encode("utf-8")
    for secret_value in secret_values:
        expected = hmac.new(secret_value.encode("utf-8"), canonical, hashlib.sha256).hexdigest()
        if secrets.compare_digest(expected, supplied_signature.lower()):
            return True
    return False


def _register_nonce(easyadmin: Any, integration: dict[str, Any]) -> bool:
    key_id = str(integration["key_id"])
    nonce = request.headers.get("X-EasyAdmin-Nonce", "")
    cutoff = _iso(_utcnow() - timedelta(days=1))
    conn = easyadmin.get_db_connection()
    try:
        try:
            conn.execute("DELETE FROM website_api_nonces WHERE used_at < ?", (cutoff,))
            conn.execute(
                "INSERT INTO website_api_nonces (integration_key_id, nonce, used_at) VALUES (?, ?, ?)",
                (key_id, nonce, _iso()),
            )
            conn.execute(
                "UPDATE website_integrations SET last_used_at=?, updated_at=? WHERE key_id=?",
                (_iso(), _iso(), key_id),
            )
            conn.commit()
            return True
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            return False
    finally:
        conn.close()


def _required_scope() -> str | None:
    if request.path == "/catalog" and request.method == "GET":
        return "catalog:read"
    if request.path == "/quotes" and request.method == "POST":
        return "quote:create"
    if request.path == "/health" and request.method == "GET":
        return None
    return None


def _validate_customer_payload(data: dict[str, Any]) -> tuple[dict[str, str] | None, str | None]:
    customer = data.get("customer") if isinstance(data.get("customer"), dict) else {}
    cleaned = {
        "first_name": _clean(customer.get("first_name"), 80),
        "surname": _clean(customer.get("surname"), 80),
        "company_name": _clean(customer.get("company_name"), 120),
        "email": _clean(customer.get("email"), 160).lower(),
        "phone": _clean(customer.get("phone"), 30),
        "address": _clean(customer.get("address"), 300),
        "suburb": _clean(customer.get("suburb"), 100),
        "postal_code": _clean(customer.get("postal_code"), 12),
        "delivery_area": _clean(customer.get("delivery_area"), 100),
    }
    if not cleaned["first_name"]:
        return None, "Customer first name is required."
    if not EMAIL_RE.fullmatch(cleaned["email"]):
        return None, "A valid customer email address is required."
    if not PHONE_RE.fullmatch(cleaned["phone"]):
        return None, "A valid customer contact number is required."
    if not cleaned["address"] or not cleaned["suburb"]:
        return None, "Delivery address and suburb are required."
    return cleaned, None


def _find_or_create_client(conn: Any, company_id: int, customer: dict[str, str]) -> Any:
    matches = conn.execute(
        "SELECT * FROM clients WHERE company_id=? AND LOWER(TRIM(COALESCE(email,'')))=LOWER(?) ORDER BY id ASC LIMIT 2",
        (company_id, customer["email"]),
    ).fetchall()
    if len(matches) == 1:
        return matches[0]

    address = ", ".join(
        part for part in [customer["address"], customer["suburb"], customer["postal_code"]] if part
    )
    cursor = conn.cursor()
    cursor.execute(
        """INSERT INTO clients
           (company_id, name, surname, company_name, address, suburb, postal_code, phone, email, client_type, discount_percent)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            company_id,
            customer["first_name"],
            customer["surname"],
            customer["company_name"],
            address,
            customer["suburb"],
            customer["postal_code"],
            customer["phone"],
            customer["email"],
            "Ad hoc",
            0,
        ),
    )
    client_id = cursor.lastrowid
    return conn.execute(
        "SELECT * FROM clients WHERE id=? AND company_id=?",
        (client_id, company_id),
    ).fetchone()


def _catalog_rows(conn: Any, integration: dict[str, Any]) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT p.integration_key_id, p.service_id, p.public_id, p.enabled,
                  p.display_name, p.website_description, p.website_category,
                  p.website_unit, p.minimum_quantity, p.sort_order,
                  s.name AS service_name, s.client_price
           FROM website_integration_products p
           JOIN services s ON s.id=p.service_id
           WHERE p.integration_key_id=? AND s.company_id=? AND COALESCE(p.enabled,1)=1
           ORDER BY COALESCE(p.sort_order,0), COALESCE(p.display_name,s.name) ASC""",
        (integration["key_id"], integration["company_id"]),
    ).fetchall()
    return [_row_dict(row) for row in rows]


def create_integration_app(easyadmin: Any) -> Flask:
    """Return the generic integration Flask app backed by Easy Admin DB helpers."""
    ensure_integration_schema(easyadmin)
    integration_app = Flask("easyadmin_website_integrations")
    integration_app.config["MAX_CONTENT_LENGTH"] = int(
        os.getenv("EASYADMIN_WEBSITE_INTEGRATIONS_MAX_CONTENT_LENGTH", str(1024 * 1024))
    )
    enabled = _env_bool("EASYADMIN_WEBSITE_INTEGRATIONS_ENABLED", False)
    max_age = max(
        30,
        min(900, int(os.getenv("EASYADMIN_INTEGRATION_SIGNATURE_MAX_AGE_SECONDS", "300"))),
    )
    master_key = os.getenv("EASYADMIN_INTEGRATION_MASTER_KEY", "").strip()
    cipher = SecretCipher(master_key) if master_key else None

    @integration_app.before_request
    def protect_integration():
        if not enabled:
            return jsonify({"status": "error", "message": "Integration is disabled."}), 404
        if cipher is None:
            return jsonify({"status": "error", "message": "Integration framework is not configured."}), 503

        key_id = _clean(request.headers.get("X-EasyAdmin-Key-ID"), 100)
        if not KEY_ID_RE.fullmatch(key_id):
            return jsonify({"status": "error", "message": "Invalid integration authentication."}), 401
        integration_record = _load_integration(easyadmin, key_id)
        if not integration_record or not bool(integration_record.get("enabled")):
            return jsonify({"status": "error", "message": "Invalid integration authentication."}), 401

        expires_at = _parse_datetime(integration_record.get("expires_at"))
        if expires_at and expires_at <= _utcnow():
            _audit(easyadmin, integration_record, "Expired Integration Credential Used", {}, result="blocked")
            return jsonify({"status": "error", "message": "Invalid integration authentication."}), 401

        timestamp = request.headers.get("X-EasyAdmin-Timestamp", "")
        try:
            request_time = int(timestamp)
        except (TypeError, ValueError):
            return jsonify({"status": "error", "message": "Invalid integration authentication."}), 401
        if abs(int(time.time()) - request_time) > max_age:
            return jsonify({"status": "error", "message": "Integration request expired."}), 401

        remote = request.remote_addr or "unknown"
        limit = 60 if request.method != "GET" else 180
        allowed, retry_after = _RATE_LIMITER.check(
            f"{key_id}:{remote}:{request.endpoint or request.path}", limit, 300
        )
        if not allowed:
            _audit(easyadmin, integration_record, "Integration Rate Limit Reached", {"path": request.path}, result="blocked")
            response = jsonify({"status": "error", "message": "Too many integration requests."})
            response.status_code = 429
            response.headers["Retry-After"] = str(retry_after)
            return response

        try:
            secret_values = _active_secrets(integration_record, cipher)
        except IntegrationConfigurationError:
            _audit(easyadmin, integration_record, "Integration Secret Decryption Failed", {}, result="failure")
            return jsonify({"status": "error", "message": "Integration framework is not configured."}), 503

        if not _verify_hmac(secret_values):
            _audit(easyadmin, integration_record, "Integration Authentication Failed", {"path": _canonical_request_path()}, result="blocked")
            return jsonify({"status": "error", "message": "Invalid integration authentication."}), 401
        if not _register_nonce(easyadmin, integration_record):
            _audit(easyadmin, integration_record, "Integration Replay Blocked", {}, result="blocked")
            return jsonify({"status": "error", "message": "Duplicate integration request."}), 409

        required_scope = _required_scope()
        scopes = _parse_scopes(integration_record.get("scopes"))
        if required_scope and required_scope not in scopes:
            _audit(
                easyadmin,
                integration_record,
                "Integration Scope Blocked",
                {"required_scope": required_scope, "path": request.path},
                result="blocked",
            )
            return jsonify({"status": "error", "message": "Integration permission denied."}), 403

        g.website_integration = integration_record
        return None

    @integration_app.after_request
    def integration_headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    @integration_app.get("/catalog")
    def catalog():
        integration_record = dict(g.website_integration)
        company_id = int(integration_record["company_id"])
        price_mode = str(integration_record.get("price_mode") or "exclusive_vat").lower()
        if price_mode not in ALLOWED_PRICE_MODES:
            price_mode = "exclusive_vat"
        vat_rate = max(0.0, min(1.0, float(integration_record.get("vat_rate") or 0)))
        conn = easyadmin.get_db_connection()
        try:
            company = conn.execute(
                "SELECT id, name FROM companies WHERE id=?",
                (company_id,),
            ).fetchone()
            if not company:
                return jsonify({"status": "error", "message": "Integration tenant was not found."}), 503
            products = []
            for row in _catalog_rows(conn, integration_record):
                prices = _price_values(row.get("client_price"), price_mode, vat_rate)
                if float(prices["gross"]) <= 0:
                    continue
                service_name = row.get("display_name") or row.get("service_name") or "Product"
                products.append(
                    {
                        "id": row["public_id"],
                        "name": service_name,
                        "description": row.get("website_description") or "Contact us for product details and availability.",
                        "category": row.get("website_category") or _infer_category(service_name),
                        "unit": row.get("website_unit") or "each",
                        "minimum_quantity": max(_quantity(row.get("minimum_quantity")) or 1, 0.0001),
                        "price": prices["gross"],
                        "price_ex_vat": prices["net"],
                        "vat_included": bool(prices["vat_applies"]),
                    }
                )
            return jsonify(
                {
                    "status": "success",
                    "currency": "ZAR",
                    "vat_rate": vat_rate,
                    "prices_include_vat": price_mode != "no_vat",
                    "company": _row_dict(company).get("name"),
                    "integration": integration_record.get("integration_name"),
                    "products": products,
                }
            )
        finally:
            conn.close()

    @integration_app.post("/quotes")
    def create_quote():
        integration_record = dict(g.website_integration)
        company_id = int(integration_record["company_id"])
        key_id = str(integration_record["key_id"])
        idempotency_key = _clean(request.headers.get("Idempotency-Key"), 100)
        if len(idempotency_key) < 16:
            return jsonify({"status": "error", "message": "A valid idempotency key is required."}), 400
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"status": "error", "message": "Invalid JSON request."}), 400
        customer, error = _validate_customer_payload(data)
        if error:
            return jsonify({"status": "error", "message": error}), 400
        raw_items = data.get("items")
        if not isinstance(raw_items, list) or not raw_items or len(raw_items) > 40:
            return jsonify({"status": "error", "message": "One to forty product lines are required."}), 400
        notes = _clean(data.get("notes"), 1000)
        request_hash = hashlib.sha256(request.get_data(cache=True) or b"").hexdigest()
        price_mode = str(integration_record.get("price_mode") or "exclusive_vat").lower()
        if price_mode not in ALLOWED_PRICE_MODES:
            price_mode = "exclusive_vat"
        vat_rate = max(0.0, min(1.0, float(integration_record.get("vat_rate") or 0)))
        valid_days = max(1, min(90, int(integration_record.get("quote_valid_days") or 14)))
        quote_status = _clean(integration_record.get("quote_status") or "Pending", 40)
        source_label = _clean(integration_record.get("source_label") or "Website Integration", 120)

        conn = easyadmin.get_db_connection()
        try:
            existing = conn.execute(
                "SELECT quote_id, request_hash FROM website_quote_requests WHERE integration_key_id=? AND idempotency_key=?",
                (key_id, idempotency_key),
            ).fetchone()
            if existing:
                existing_data = _row_dict(existing)
                if existing_data.get("request_hash") and existing_data["request_hash"] != request_hash:
                    return jsonify({"status": "error", "message": "Idempotency key was already used for another request."}), 409
                quote = conn.execute(
                    "SELECT id, total FROM quotes WHERE id=? AND company_id=?",
                    (existing_data.get("quote_id"), company_id),
                ).fetchone()
                if quote:
                    quote_data = _row_dict(quote)
                    return jsonify(
                        {
                            "status": "success",
                            "duplicate": True,
                            "quote_id": quote_data["id"],
                            "quote_reference": _quote_reference(conn, company_id, quote_data["id"]),
                            "total": _money(quote_data.get("total")),
                            "message": "The quotation request was already received.",
                        }
                    )

            product_lines: list[dict[str, Any]] = []
            seen: set[str] = set()
            for raw in raw_items:
                if not isinstance(raw, dict):
                    return jsonify({"status": "error", "message": "Invalid product line."}), 400
                product_id = _clean(raw.get("product_id"), 100)
                quantity = _quantity(raw.get("quantity"))
                if not product_id or product_id in seen or quantity <= 0 or quantity > 1_000_000:
                    return jsonify({"status": "error", "message": "Invalid or duplicate product quantity."}), 400
                seen.add(product_id)
                row = conn.execute(
                    """SELECT p.public_id, p.display_name, p.website_unit, p.minimum_quantity,
                              s.id AS service_id, s.name AS service_name, s.client_price
                       FROM website_integration_products p
                       JOIN services s ON s.id=p.service_id
                       WHERE p.integration_key_id=? AND p.public_id=?
                         AND COALESCE(p.enabled,1)=1 AND s.company_id=?""",
                    (key_id, product_id, company_id),
                ).fetchone()
                product = _row_dict(row)
                if not product:
                    return jsonify({"status": "error", "message": "A selected product is no longer available online."}), 409
                minimum = max(_quantity(product.get("minimum_quantity")) or 1, 0.0001)
                if quantity < minimum:
                    product_name = product.get("display_name") or product.get("service_name") or "Product"
                    return jsonify({"status": "error", "message": f"{product_name} has a minimum quantity of {minimum:g}."}), 400
                prices = _price_values(product.get("client_price"), price_mode, vat_rate)
                if float(prices["gross"]) <= 0:
                    return jsonify({"status": "error", "message": "A selected product does not have a valid selling price."}), 409
                product_lines.append(
                    {
                        "product": product,
                        "quantity": quantity,
                        "unit_price": float(prices["net"]),
                        "amount": round(float(prices["net"]) * quantity, 2),
                        "vat": round(float(prices["vat"]) * quantity, 2),
                    }
                )

            subtotal = round(sum(line["amount"] for line in product_lines), 2)
            vat_amount = round(sum(line["vat"] for line in product_lines), 2)
            total = round(subtotal + vat_amount, 2)
            client = _find_or_create_client(conn, company_id, customer)
            client_id = _row_dict(client).get("id")
            client_name = _client_display_name(client)
            today = datetime.now().date()
            valid_until = today + timedelta(days=valid_days)
            delivery_address = ", ".join(
                part for part in [customer["address"], customer["suburb"], customer["postal_code"]] if part
            )

            quote_number = easyadmin.allocate_billing_document_number(conn, company_id, "quote")
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO quotes
                   (company_id, client_id, client_name, date, valid_until, subtotal, vat_amount, total, status,
                    source, external_request_id, website_notes, website_customer_email, website_customer_phone,
                    website_delivery_address, website_delivery_area, quote_number)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    company_id,
                    client_id,
                    client_name,
                    today.isoformat(),
                    valid_until.isoformat(),
                    subtotal,
                    vat_amount,
                    total,
                    quote_status,
                    source_label,
                    idempotency_key,
                    notes,
                    customer["email"],
                    customer["phone"],
                    delivery_address,
                    customer["delivery_area"] or customer["suburb"],
                    quote_number,
                ),
            )
            quote_id = cursor.lastrowid
            for line in product_lines:
                product = line["product"]
                name = product.get("display_name") or product.get("service_name") or "Product"
                unit = product.get("website_unit") or "each"
                conn.execute(
                    """INSERT INTO quote_items
                       (quote_id, service_date, description, quantity, unit_price, amount)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        quote_id,
                        today.isoformat(),
                        f"{name} ({unit})",
                        line["quantity"],
                        line["unit_price"],
                        line["amount"],
                    ),
                )
            conn.execute(
                """INSERT INTO website_quote_requests
                   (integration_key_id, idempotency_key, request_hash, quote_id, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (key_id, idempotency_key, request_hash, quote_id, _iso()),
            )
            conn.commit()
            reference = _quote_reference(conn, company_id, quote_id)
            _audit(
                easyadmin,
                integration_record,
                "Created Website Quote",
                {
                    "quote_reference": reference,
                    "customer_email": customer["email"],
                    "product_lines": len(product_lines),
                    "total": total,
                    "delivery_area": customer["delivery_area"] or customer["suburb"],
                },
                record_type="quote",
                record_id=quote_id,
            )
            return jsonify(
                {
                    "status": "success",
                    "quote_id": quote_id,
                    "quote_reference": reference,
                    "total": total,
                    "message": "Quotation request created in Easy Admin for review.",
                }
            ), 201
        except Exception as exc:
            try:
                conn.rollback()
            except Exception:
                pass
            _audit(easyadmin, integration_record, "Website Quote Creation Failed", str(exc), result="failure")
            return jsonify({"status": "error", "message": "Easy Admin could not create the quotation."}), 500
        finally:
            conn.close()

    @integration_app.get("/health")
    def health():
        integration_record = dict(g.website_integration)
        return jsonify(
            {
                "status": "ok",
                "framework": "easyadmin-website-integrations-v1",
                "integration": integration_record.get("integration_name"),
                "tenant_bound": True,
            }
        )

    return integration_app
