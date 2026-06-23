"""Database compatibility helpers for Easy Admin.

This keeps the existing sqlite3-style app code working while allowing Render to
connect to Supabase PostgreSQL when DATABASE_URL is configured.
"""
import os
import re
import sqlite3
from collections.abc import Mapping
from datetime import date, datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


POSTGRES_ENV_KEYS = ("DATABASE_URL", "SUPABASE_DATABASE_URL", "POSTGRES_URL")


def get_database_url():
    for key in POSTGRES_ENV_KEYS:
        value = (os.environ.get(key) or "").strip()
        if value:
            return value
    return ""


def is_postgres_enabled():
    return bool(get_database_url())


def _normalise_postgres_url(url):
    """Ensure Supabase/PostgreSQL URLs use SSL unless explicitly configured."""
    if not url:
        return url
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    if "sslmode" not in {k.lower(): v for k, v in query.items()}:
        query["sslmode"] = "require"
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


class CompatRow(Mapping):
    def __init__(self, columns, values):
        self._columns = list(columns or [])
        self._values = [self._clean_value(v) for v in (values or [])]
        self._map = {c: self._values[i] for i, c in enumerate(self._columns)}

    @staticmethod
    def _clean_value(value):
        if isinstance(value, (datetime, date)):
            return value.isoformat(sep=" ") if isinstance(value, datetime) else value.isoformat()
        return value

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        return self._map[key]

    def __iter__(self):
        return iter(self._columns)

    def __len__(self):
        return len(self._columns)

    def keys(self):
        return self._columns

    def get(self, key, default=None):
        return self._map.get(key, default)

    def __repr__(self):
        return repr(self._map)


def _quote_sql_string_literal(match):
    value = match.group(1).replace("'", "''")
    return f"'{value}'"


def translate_sql(sql):
    """Translate the subset of SQLite SQL used by Easy Admin to PostgreSQL."""
    if not isinstance(sql, str):
        return sql
    s = sql.strip()

    # sqlite schema introspection used by export/helpers.
    m = re.match(r"SELECT\s+name\s+FROM\s+sqlite_master\s+WHERE\s+type\s*=\s*'table'\s+AND\s+name\s*=\s*\?", s, re.I)
    if m:
        return "SELECT table_name AS name FROM information_schema.tables WHERE table_schema='public' AND table_name=%s"

    m = re.match(r"PRAGMA\s+table_info\(([^)]+)\)", s, re.I)
    if m:
        table = m.group(1).strip().strip('"').strip("'")
        return ("SELECT column_name AS name FROM information_schema.columns "
                f"WHERE table_schema='public' AND table_name='{table}' ORDER BY ordinal_position")

    # SQLite conflict syntax.
    s = re.sub(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", "INSERT INTO", s, flags=re.I)
    insert_was_ignore = bool(re.search(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", sql, flags=re.I))

    # SQLite current date/time functions must be translated before type-name rewrites.
    s = re.sub(r"datetime\('now'\s*,\s*'-([0-9]+) days'\)", r"(CURRENT_TIMESTAMP - INTERVAL '\1 days')", s, flags=re.I)
    s = re.sub(r"datetime\('now'\s*,\s*'localtime'\)", "CURRENT_TIMESTAMP", s, flags=re.I)
    s = re.sub(r"datetime\('now'\)", "CURRENT_TIMESTAMP", s, flags=re.I)
    s = re.sub(r"date\('now'\)", "CURRENT_DATE", s, flags=re.I)

    # SQLite auto-increment and type names.
    s = re.sub(r"\bINTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b", "SERIAL PRIMARY KEY", s, flags=re.I)
    s = re.sub(r"\bAUTOINCREMENT\b", "", s, flags=re.I)
    s = re.sub(r"\bDATETIME\b", "TIMESTAMP", s, flags=re.I)
    s = re.sub(r"\bBLOB\b", "BYTEA", s, flags=re.I)
    s = re.sub(r"\bTEXT\s+DEFAULT\s+CURRENT_TIMESTAMP\b", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP", s, flags=re.I)

    # ALTER ADD COLUMN should be idempotent on PostgreSQL too.
    s = re.sub(r"\bALTER\s+TABLE\s+([A-Za-z_][\w]*)\s+ADD\s+COLUMN\s+(?!IF\s+NOT\s+EXISTS)",
               r"ALTER TABLE \1 ADD COLUMN IF NOT EXISTS ", s, flags=re.I)

    # SQLite often used double quotes as string literals. PostgreSQL reserves
    # double quotes for identifiers, so convert those app SQL literals.
    s = re.sub(r'"([^"\\]*(?:\\.[^"\\]*)*)"', _quote_sql_string_literal, s)

    # Placeholders.
    s = s.replace("?", "%s")

    if insert_was_ignore and " ON CONFLICT" not in s.upper():
        s = s.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"

    return s


class PgCursor:
    def __init__(self, connection):
        self.connection = connection
        self._cursor = connection._conn.cursor()
        self.lastrowid = None
        self.rowcount = -1
        self.description = None

    def execute(self, sql, params=None):
        translated = translate_sql(sql)
        params = tuple(params or [])
        try:
            self._cursor.execute(translated, params)
            self.description = self._cursor.description
            self.rowcount = self._cursor.rowcount
            self.lastrowid = None
            if translated.lstrip().upper().startswith("INSERT"):
                try:
                    with self.connection._conn.cursor() as id_cur:
                        id_cur.execute("SELECT LASTVAL()")
                        self.lastrowid = id_cur.fetchone()[0]
                except Exception:
                    self.lastrowid = None
            return self
        except Exception as exc:
            raise sqlite3.OperationalError(str(exc)) from exc

    def executemany(self, sql, seq_of_params):
        for params in seq_of_params:
            self.execute(sql, params)
        return self

    def fetchone(self):
        row = self._cursor.fetchone()
        if row is None:
            return None
        columns = [d[0] for d in (self._cursor.description or [])]
        return CompatRow(columns, row)

    def fetchall(self):
        rows = self._cursor.fetchall()
        columns = [d[0] for d in (self._cursor.description or [])]
        return [CompatRow(columns, row) for row in rows]

    def close(self):
        try:
            self._cursor.close()
        except Exception:
            pass


class PgConnection:
    def __init__(self, dsn):
        try:
            import psycopg2
        except ImportError as exc:
            raise RuntimeError("psycopg2-binary is required when DATABASE_URL is set.") from exc
        self._conn = psycopg2.connect(_normalise_postgres_url(dsn))
        # The legacy app catches migration/introspection errors in many places.
        # Autocommit avoids leaving PostgreSQL transactions in an aborted state.
        self._conn.autocommit = True
        self.row_factory = None

    def execute(self, sql, params=None):
        cur = self.cursor()
        return cur.execute(sql, params)

    def executemany(self, sql, seq_of_params):
        cur = self.cursor()
        return cur.executemany(sql, seq_of_params)

    def cursor(self):
        return PgCursor(self)

    def commit(self):
        try:
            self._conn.commit()
        except Exception:
            pass

    def rollback(self):
        try:
            self._conn.rollback()
        except Exception:
            pass

    def close(self):
        self._conn.close()


def connect_sqlite(database_path):
    parent = os.path.dirname(os.path.abspath(database_path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(database_path, timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL;')
    return conn


def connect_database(database_path=None):
    url = get_database_url()
    if url:
        return PgConnection(url)
    return connect_sqlite(database_path or os.environ.get('DATABASE_PATH') or os.environ.get('DB_PATH') or 'database.db')


def table_exists(conn, table_name):
    try:
        row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,)).fetchone()
        return bool(row)
    except Exception:
        return False


def table_columns(conn, table_name):
    if not table_exists(conn, table_name):
        return []
    try:
        return [r['name'] for r in conn.execute(f"PRAGMA table_info({table_name})").fetchall()]
    except Exception:
        return []
