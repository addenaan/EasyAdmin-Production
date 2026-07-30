from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import tempfile
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

from app_modules.website_integrations import (
    INTEGRATION_PREFIX,
    SecretCipher,
    create_integration_app,
    ensure_integration_schema,
)


class EasyAdminStub(SimpleNamespace):
    def __init__(self, db_path: str):
        super().__init__()
        self.db_path = db_path
        self.audit = []

    def get_db_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def log_action(self, *args, **kwargs):
        self.audit.append((args, kwargs))


def initialise_database(path: str) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE companies (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE services (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER,
            name TEXT,
            client_price REAL,
            company_cost REAL
        );
        CREATE TABLE clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER,
            name TEXT,
            surname TEXT,
            company_name TEXT,
            address TEXT,
            suburb TEXT,
            postal_code TEXT,
            phone TEXT,
            email TEXT,
            client_type TEXT,
            discount_percent REAL
        );
        CREATE TABLE quotes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER,
            client_name TEXT,
            date TEXT,
            valid_until TEXT,
            subtotal REAL,
            vat_amount REAL,
            total REAL,
            status TEXT
        );
        CREATE TABLE quote_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            quote_id INTEGER,
            service_date TEXT,
            description TEXT,
            amount REAL,
            quantity REAL DEFAULT 1,
            unit_price REAL
        );
        CREATE TABLE settings (company_id INTEGER, key TEXT, value TEXT);
        INSERT INTO companies (id, name) VALUES (7, 'Retreat Block and Brick');
        INSERT INTO companies (id, name) VALUES (8, 'Other Tenant');
        INSERT INTO services (company_id, name, client_price, company_cost)
          VALUES (7, 'M140 Concrete Block', 13.00, 8.00);
        INSERT INTO services (company_id, name, client_price, company_cost)
          VALUES (8, 'Other Tenant Product', 999.00, 1.00);
        INSERT INTO settings (company_id, key, value) VALUES (7, 'quote_prefix', 'QT-');
        INSERT INTO settings (company_id, key, value) VALUES (7, 'quote_start', '1000');
        """
    )
    conn.commit()
    conn.close()


def signed_headers(key_id: str, secret: str, method: str, path: str, body: bytes, idempotency_key: str | None = None):
    timestamp = str(int(time.time()))
    nonce = uuid.uuid4().hex
    body_hash = hashlib.sha256(body).hexdigest()
    canonical = "\n".join([method, path, timestamp, nonce, body_hash]).encode()
    signature = hmac.new(secret.encode(), canonical, hashlib.sha256).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "X-EasyAdmin-Key-ID": key_id,
        "X-EasyAdmin-Timestamp": timestamp,
        "X-EasyAdmin-Nonce": nonce,
        "X-EasyAdmin-Signature": signature,
    }
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    return headers


def build_app():
    temp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    temp.close()
    initialise_database(temp.name)
    stub = EasyAdminStub(temp.name)
    master_key = SecretCipher.generate_master_key()
    secret = "a" * 64
    key_id = "retreat-production-01"
    os.environ["EASYADMIN_WEBSITE_INTEGRATIONS_ENABLED"] = "true"
    os.environ["EASYADMIN_INTEGRATION_MASTER_KEY"] = master_key
    ensure_integration_schema(stub)
    cipher = SecretCipher(master_key)
    conn = stub.get_db_connection()
    conn.execute(
        """INSERT INTO website_integrations
           (key_id, company_id, integration_name, secret_ciphertext, enabled, scopes,
            price_mode, vat_rate, quote_valid_days, quote_status, source_label)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (key_id, 7, "Retreat Website", cipher.encrypt(secret), 1,
         json.dumps(["catalog:read", "quote:create"]), "exclusive_vat", 0.15, 14, "Pending", "Retreat Website"),
    )
    service_id = conn.execute("SELECT id FROM services WHERE company_id=7").fetchone()[0]
    other_service_id = conn.execute("SELECT id FROM services WHERE company_id=8").fetchone()[0]
    conn.execute(
        """INSERT INTO website_integration_products
           (integration_key_id, service_id, public_id, enabled, display_name, website_category, website_unit, minimum_quantity)
           VALUES (?, ?, ?, 1, ?, ?, ?, ?)""",
        (key_id, service_id, "prd_retreat_block", "M140 Concrete Block", "Blocks", "each", 50),
    )
    # Deliberately malformed cross-tenant mapping: API must still reject it via s.company_id.
    conn.execute(
        """INSERT INTO website_integration_products
           (integration_key_id, service_id, public_id, enabled, display_name, website_category, website_unit, minimum_quantity)
           VALUES (?, ?, ?, 1, ?, ?, ?, ?)""",
        (key_id, other_service_id, "prd_other_tenant", "Other Tenant Product", "Other", "each", 1),
    )
    conn.commit()
    conn.close()
    app = create_integration_app(stub)
    app.config["TESTING"] = True
    return app, stub, secret, key_id, Path(temp.name)


def test_catalog_is_tenant_bound_and_quote_is_created():
    app, stub, secret, key_id, db_path = build_app()
    try:
        client = app.test_client()
        path = f"{INTEGRATION_PREFIX}/catalog"
        response = client.get(
            "/catalog",
            headers=signed_headers(key_id, secret, "GET", path, b""),
            environ_overrides={"SCRIPT_NAME": INTEGRATION_PREFIX},
        )
        payload = response.get_json()
        assert response.status_code == 200
        assert [product["id"] for product in payload["products"]] == ["prd_retreat_block"]

        quote_payload = {
            "company_id": 8,
            "customer": {
                "first_name": "Test",
                "surname": "Customer",
                "company_name": "Test Builders",
                "email": "test@example.com",
                "phone": "021 555 0100",
                "address": "1 Test Street",
                "suburb": "Retreat",
                "postal_code": "7965",
                "delivery_area": "Retreat",
            },
            "items": [{"product_id": "prd_retreat_block", "quantity": 100}],
            "notes": "Test integration quote",
        }
        body = json.dumps(quote_payload, sort_keys=True, separators=(",", ":")).encode()
        quote_path = f"{INTEGRATION_PREFIX}/quotes"
        request_id = str(uuid.uuid4())
        response = client.post(
            "/quotes",
            data=body,
            headers=signed_headers(key_id, secret, "POST", quote_path, body, request_id),
            environ_overrides={"SCRIPT_NAME": INTEGRATION_PREFIX},
        )
        result = response.get_json()
        assert response.status_code == 201
        assert result["quote_reference"] == "QT-1000"
        assert result["total"] == 1495.0
        conn = stub.get_db_connection()
        quote = conn.execute("SELECT * FROM quotes WHERE id=?", (result["quote_id"],)).fetchone()
        conn.close()
        assert quote["company_id"] == 7
        assert quote["source"] == "Retreat Website"
    finally:
        db_path.unlink(missing_ok=True)


def test_cross_tenant_product_and_invalid_signature_are_rejected():
    app, _stub, secret, key_id, db_path = build_app()
    try:
        client = app.test_client()
        payload = {
            "customer": {
                "first_name": "Test",
                "email": "test@example.com",
                "phone": "021 555 0100",
                "address": "1 Test Street",
                "suburb": "Retreat",
            },
            "items": [{"product_id": "prd_other_tenant", "quantity": 1}],
        }
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        path = f"{INTEGRATION_PREFIX}/quotes"
        response = client.post(
            "/quotes",
            data=body,
            headers=signed_headers(key_id, secret, "POST", path, body, str(uuid.uuid4())),
            environ_overrides={"SCRIPT_NAME": INTEGRATION_PREFIX},
        )
        assert response.status_code == 409

        response = client.get(
            "/catalog",
            headers={
                "X-EasyAdmin-Key-ID": key_id,
                "X-EasyAdmin-Timestamp": str(int(time.time())),
                "X-EasyAdmin-Nonce": uuid.uuid4().hex,
                "X-EasyAdmin-Signature": "0" * 64,
            },
            environ_overrides={"SCRIPT_NAME": INTEGRATION_PREFIX},
        )
        assert response.status_code == 401
    finally:
        db_path.unlink(missing_ok=True)
