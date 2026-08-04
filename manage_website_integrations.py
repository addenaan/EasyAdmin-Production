"""Render Shell CLI for managing generic Easy Admin website integrations."""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import sys
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import app as easyadmin
from app_modules.website_integrations import (
    ALLOWED_PRICE_MODES,
    DEFAULT_SCOPES,
    KEY_ID_RE,
    SecretCipher,
    ensure_integration_schema,
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def row_dict(row: Any) -> dict[str, Any]:
    return dict(row) if row is not None else {}


def get_cipher() -> SecretCipher:
    key = os.getenv("EASYADMIN_INTEGRATION_MASTER_KEY", "").strip()
    if not key:
        raise SystemExit("EASYADMIN_INTEGRATION_MASTER_KEY is not set.")
    return SecretCipher(key)


def get_integration(conn: Any, key_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM website_integrations WHERE key_id=?", (key_id,)).fetchone()
    if not row:
        raise SystemExit(f"Integration '{key_id}' was not found.")
    return row_dict(row)


def company_exists(conn: Any, company_id: int) -> bool:
    return bool(conn.execute("SELECT id FROM companies WHERE id=?", (company_id,)).fetchone())


def infer_category(name: str) -> str:
    text = (name or "").lower()
    for token, category in [
        ("vibracrete", "Vibracrete"),
        ("paver", "Paving"),
        ("paving", "Paving"),
        ("brick", "Bricks"),
        ("block", "Blocks"),
        ("sand", "Sand"),
        ("stone", "Stone"),
        ("cement", "Cement"),
    ]:
        if token in text:
            return category
    return "Products"


def map_service(conn: Any, key_id: str, company_id: int, service_id: int, *, enabled: bool = True) -> bool:
    service = conn.execute(
        "SELECT id, name FROM services WHERE id=? AND company_id=?",
        (service_id, company_id),
    ).fetchone()
    if not service:
        return False
    service_data = row_dict(service)
    existing = conn.execute(
        "SELECT public_id FROM website_integration_products WHERE integration_key_id=? AND service_id=?",
        (key_id, service_id),
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE website_integration_products SET enabled=?, updated_at=? WHERE integration_key_id=? AND service_id=?",
            (1 if enabled else 0, now_iso(), key_id, service_id),
        )
    else:
        conn.execute(
            """INSERT INTO website_integration_products
               (integration_key_id, service_id, public_id, enabled, display_name,
                website_description, website_category, website_unit,
                minimum_quantity, sort_order, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                key_id,
                service_id,
                f"prd_{uuid.uuid4().hex}",
                1 if enabled else 0,
                service_data.get("name") or "Product",
                "",
                infer_category(service_data.get("name") or ""),
                "each",
                1,
                0,
                now_iso(),
                now_iso(),
            ),
        )
    return True


def command_generate_master_key(_args: argparse.Namespace) -> None:
    print(SecretCipher.generate_master_key())


def command_list_companies(_args: argparse.Namespace) -> None:
    ensure_integration_schema(easyadmin)
    conn = easyadmin.get_db_connection()
    try:
        rows = conn.execute("SELECT id, name FROM companies ORDER BY id").fetchall()
        if not rows:
            print("No Easy Admin companies were found in the configured database.")
            return
        print("ID\tCompany")
        for row in rows:
            data = row_dict(row)
            print(f"{data.get('id')}\t{data.get('name')}")
    finally:
        conn.close()


def command_create(args: argparse.Namespace) -> None:
    ensure_integration_schema(easyadmin)
    cipher = get_cipher()
    key_id = args.key_id or f"web-{args.company_id}-{secrets.token_hex(5)}"
    if not KEY_ID_RE.fullmatch(key_id):
        raise SystemExit("Key ID must be 8-100 characters using letters, numbers, dot, underscore or hyphen.")
    if args.price_mode not in ALLOWED_PRICE_MODES:
        raise SystemExit(f"Invalid price mode: {args.price_mode}")
    scopes = sorted(set(args.scope or DEFAULT_SCOPES))
    secret = args.secret or secrets.token_urlsafe(48)
    if len(secret) < 32:
        raise SystemExit("Integration secret must be at least 32 characters.")
    conn = easyadmin.get_db_connection()
    try:
        if not company_exists(conn, args.company_id):
            raise SystemExit(f"Company ID {args.company_id} was not found.")
        if conn.execute("SELECT key_id FROM website_integrations WHERE key_id=?", (key_id,)).fetchone():
            raise SystemExit(f"Integration key '{key_id}' already exists.")
        has_default = conn.execute(
            "SELECT key_id FROM website_integrations WHERE company_id=? AND COALESCE(is_default,0)=1 LIMIT 1",
            (args.company_id,),
        ).fetchone()
        conn.execute(
            """INSERT INTO website_integrations
               (key_id, company_id, integration_name, secret_ciphertext, enabled, scopes,
                price_mode, vat_rate, quote_valid_days, quote_status, source_label,
                created_at, updated_at, secret_rotated_at, is_default)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                key_id,
                args.company_id,
                args.name,
                cipher.encrypt(secret),
                1,
                json.dumps(scopes),
                args.price_mode,
                args.vat_rate,
                args.quote_valid_days,
                args.quote_status,
                args.source_label,
                now_iso(),
                now_iso(),
                now_iso(),
                0 if has_default else 1,
            ),
        )
        mapped = 0
        if args.map_all_services:
            services = conn.execute(
                "SELECT id FROM services WHERE company_id=? ORDER BY id",
                (args.company_id,),
            ).fetchall()
            for service in services:
                if map_service(conn, key_id, args.company_id, int(row_dict(service)["id"])):
                    mapped += 1
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()
    print("Integration created. Store the following website variables securely:")
    print(f"EASYADMIN_INTEGRATION_KEY_ID={key_id}")
    print(f"EASYADMIN_INTEGRATION_SECRET={secret}")
    print(f"Mapped products: {mapped}")
    print("The plaintext secret is shown only now and is not stored unencrypted.")


def command_list(args: argparse.Namespace) -> None:
    ensure_integration_schema(easyadmin)
    conn = easyadmin.get_db_connection()
    try:
        sql = """SELECT i.key_id, i.company_id, i.integration_name, i.enabled, i.scopes,
                        i.price_mode, i.vat_rate, i.last_used_at, i.expires_at,
                        COUNT(p.service_id) AS product_count
                 FROM website_integrations i
                 LEFT JOIN website_integration_products p ON p.integration_key_id=i.key_id
              """
        params: tuple[Any, ...] = ()
        if args.company_id:
            sql += " WHERE i.company_id=?"
            params = (args.company_id,)
        sql += " GROUP BY i.key_id, i.company_id, i.integration_name, i.enabled, i.scopes, i.price_mode, i.vat_rate, i.last_used_at, i.expires_at ORDER BY i.company_id, i.key_id"
        for row in conn.execute(sql, params).fetchall():
            print(json.dumps(row_dict(row), default=str, sort_keys=True))
    finally:
        conn.close()


def command_set_enabled(args: argparse.Namespace) -> None:
    ensure_integration_schema(easyadmin)
    conn = easyadmin.get_db_connection()
    try:
        get_integration(conn, args.key_id)
        conn.execute(
            "UPDATE website_integrations SET enabled=?, updated_at=? WHERE key_id=?",
            (1 if args.enabled else 0, now_iso(), args.key_id),
        )
        conn.commit()
    finally:
        conn.close()
    print(f"Integration {args.key_id} {'enabled' if args.enabled else 'disabled'}.")


def command_rotate(args: argparse.Namespace) -> None:
    ensure_integration_schema(easyadmin)
    cipher = get_cipher()
    secret = args.secret or secrets.token_urlsafe(48)
    if len(secret) < 32:
        raise SystemExit("Integration secret must be at least 32 characters.")
    conn = easyadmin.get_db_connection()
    try:
        current = get_integration(conn, args.key_id)
        previous_expires = datetime.now(timezone.utc) + timedelta(minutes=max(0, args.grace_minutes))
        conn.execute(
            """UPDATE website_integrations
               SET previous_secret_ciphertext=?, previous_secret_expires_at=?,
                   secret_ciphertext=?, secret_rotated_at=?, updated_at=?
               WHERE key_id=?""",
            (
                current["secret_ciphertext"],
                previous_expires.isoformat(timespec="seconds") if args.grace_minutes > 0 else now_iso(),
                cipher.encrypt(secret),
                now_iso(),
                now_iso(),
                args.key_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    print(f"EASYADMIN_INTEGRATION_SECRET={secret}")
    print(f"Previous secret grace period: {max(0, args.grace_minutes)} minutes")


def command_map_all(args: argparse.Namespace) -> None:
    ensure_integration_schema(easyadmin)
    conn = easyadmin.get_db_connection()
    try:
        integration = get_integration(conn, args.key_id)
        services = conn.execute(
            "SELECT id FROM services WHERE company_id=? ORDER BY id",
            (integration["company_id"],),
        ).fetchall()
        count = 0
        for service in services:
            if map_service(conn, args.key_id, int(integration["company_id"]), int(row_dict(service)["id"]), enabled=not args.disabled):
                count += 1
        conn.commit()
    finally:
        conn.close()
    print(f"Mapped or refreshed {count} service(s) for {args.key_id}.")


def command_map_service(args: argparse.Namespace) -> None:
    ensure_integration_schema(easyadmin)
    conn = easyadmin.get_db_connection()
    try:
        integration = get_integration(conn, args.key_id)
        if not map_service(conn, args.key_id, int(integration["company_id"]), args.service_id, enabled=True):
            raise SystemExit("Service was not found in the integration tenant.")
        updates = []
        values: list[Any] = []
        for column, value in [
            ("display_name", args.display_name),
            ("website_description", args.description),
            ("website_category", args.category),
            ("website_unit", args.unit),
            ("minimum_quantity", args.minimum_quantity),
            ("sort_order", args.sort_order),
        ]:
            if value is not None:
                updates.append(f"{column}=?")
                values.append(value)
        if updates:
            updates.append("updated_at=?")
            values.append(now_iso())
            values.extend([args.key_id, args.service_id])
            conn.execute(
                f"UPDATE website_integration_products SET {', '.join(updates)} WHERE integration_key_id=? AND service_id=?",
                tuple(values),
            )
        conn.commit()
    finally:
        conn.close()
    print(f"Service {args.service_id} mapped to {args.key_id}.")


def command_product_enabled(args: argparse.Namespace) -> None:
    ensure_integration_schema(easyadmin)
    conn = easyadmin.get_db_connection()
    try:
        integration = get_integration(conn, args.key_id)
        if not map_service(conn, args.key_id, int(integration["company_id"]), args.service_id, enabled=args.enabled):
            raise SystemExit("Service was not found in the integration tenant.")
        conn.commit()
    finally:
        conn.close()
    print(f"Product {args.service_id} {'enabled' if args.enabled else 'disabled'} for {args.key_id}.")


def command_list_products(args: argparse.Namespace) -> None:
    ensure_integration_schema(easyadmin)
    conn = easyadmin.get_db_connection()
    try:
        integration = get_integration(conn, args.key_id)
        rows = conn.execute(
            """SELECT p.service_id, p.public_id, p.enabled, p.display_name,
                      p.website_category, p.website_unit, p.minimum_quantity, p.sort_order,
                      s.name AS service_name, s.client_price
               FROM website_integration_products p
               JOIN services s ON s.id=p.service_id
               WHERE p.integration_key_id=? AND s.company_id=?
               ORDER BY COALESCE(p.sort_order,0), COALESCE(p.display_name,s.name)""",
            (args.key_id, integration["company_id"]),
        ).fetchall()
        for row in rows:
            print(json.dumps(row_dict(row), default=str, sort_keys=True))
    finally:
        conn.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage tenant-bound Easy Admin website integrations.")
    sub = parser.add_subparsers(dest="command", required=True)

    command = sub.add_parser("generate-master-key", help="Generate EASYADMIN_INTEGRATION_MASTER_KEY.")
    command.set_defaults(func=command_generate_master_key)

    command = sub.add_parser("list-companies", help="List Easy Admin company IDs.")
    command.set_defaults(func=command_list_companies)

    command = sub.add_parser("create", help="Create a tenant-bound integration credential.")
    command.add_argument("--company-id", type=int, required=True)
    command.add_argument("--name", required=True)
    command.add_argument("--key-id")
    command.add_argument("--secret")
    command.add_argument("--scope", action="append", choices=sorted(DEFAULT_SCOPES))
    command.add_argument("--price-mode", default="exclusive_vat", choices=sorted(ALLOWED_PRICE_MODES))
    command.add_argument("--vat-rate", type=float, default=0.15)
    command.add_argument("--quote-valid-days", type=int, default=14)
    command.add_argument("--quote-status", default="Pending")
    command.add_argument("--source-label", default="Website Integration")
    command.add_argument("--map-all-services", action="store_true")
    command.set_defaults(func=command_create)

    command = sub.add_parser("list", help="List integrations.")
    command.add_argument("--company-id", type=int)
    command.set_defaults(func=command_list)

    for name, enabled in [("enable", True), ("disable", False)]:
        command = sub.add_parser(name, help=f"{name.title()} an integration.")
        command.add_argument("--key-id", required=True)
        command.set_defaults(func=command_set_enabled, enabled=enabled)

    command = sub.add_parser("rotate-secret", help="Rotate an integration secret with an optional grace period.")
    command.add_argument("--key-id", required=True)
    command.add_argument("--secret")
    command.add_argument("--grace-minutes", type=int, default=15)
    command.set_defaults(func=command_rotate)

    command = sub.add_parser("map-all-services", help="Create product mappings for all tenant services.")
    command.add_argument("--key-id", required=True)
    command.add_argument("--disabled", action="store_true", help="Create mappings disabled by default.")
    command.set_defaults(func=command_map_all)

    command = sub.add_parser("map-service", help="Map and optionally customise one tenant service.")
    command.add_argument("--key-id", required=True)
    command.add_argument("--service-id", type=int, required=True)
    command.add_argument("--display-name")
    command.add_argument("--description")
    command.add_argument("--category")
    command.add_argument("--unit")
    command.add_argument("--minimum-quantity", type=float)
    command.add_argument("--sort-order", type=int)
    command.set_defaults(func=command_map_service)

    for name, enabled in [("enable-product", True), ("disable-product", False)]:
        command = sub.add_parser(name, help=f"{name.replace('-', ' ').title()}.")
        command.add_argument("--key-id", required=True)
        command.add_argument("--service-id", type=int, required=True)
        command.set_defaults(func=command_product_enabled, enabled=enabled)

    command = sub.add_parser("list-products", help="List mapped products for an integration.")
    command.add_argument("--key-id", required=True)
    command.set_defaults(func=command_list_products)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        args.func(args)
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
