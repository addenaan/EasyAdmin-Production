"""Versioned, declarative bank-payment export templates for Easy Admin.

The module deliberately treats a bank export format as data rather than code.
Only the source fields, formatting operations and file settings declared below
are accepted.  A template therefore cannot execute Python, SQL, shell commands,
network requests or uploaded Sage/bank report files.

The public helpers accept the application's sqlite-compatible connection.  The
DDL and SQL used here work with both sqlite3 and ``app_modules.db_compat``'s
PostgreSQL adapter.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from contextlib import contextmanager
from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from string import Formatter
from typing import Any, Iterable, Mapping, Sequence


SCHEMA_VERSION = 1
MAX_COLUMNS = 40
MAX_TEMPLATE_KEY_LENGTH = 64
MAX_FILENAME_LENGTH = 180


class BankExportError(ValueError):
    """Base class for safe, user-displayable bank export errors."""


class BankExportDefinitionError(BankExportError):
    """Raised when a declarative template is not safe or well formed."""

    def __init__(self, errors: Sequence[str]):
        self.errors = list(errors)
        super().__init__(" ".join(self.errors) or "The bank export template is invalid.")


class BankExportValidationError(BankExportError):
    """Raised when one or more payment rows fail a bank's rules."""

    def __init__(self, issues: Sequence[Mapping[str, Any]]):
        self.issues = [dict(issue) for issue in issues]
        summary = "; ".join(str(issue.get("message") or "Invalid payment row") for issue in self.issues[:5])
        if len(self.issues) > 5:
            summary += f"; and {len(self.issues) - 5} more error(s)"
        super().__init__(summary or "The payment rows are invalid.")


# These are the only row/context values an administrator may map into a file.
# Aliases used by the existing payroll query are intentionally retained.
SOURCE_FIELD_OPTIONS = {
    "emp_number": "Employee number",
    "employee_number": "Employee number (alias)",
    "name": "Employee name",
    "employee_name": "Employee name (alias)",
    "full_name": "Full name",
    "surname": "Surname",
    "initials": "Initials",
    "surname_initials": "Surname and initials",
    "id_passport": "ID / passport number",
    "bank_name": "Bank name",
    "account_holder": "Account holder",
    "account_number": "Account number",
    "branch_code": "Branch code",
    "account_type": "Account type",
    "net_salary": "Net salary",
    "amount": "Payment amount (alias)",
    "payment_reference": "Employee / payment reference",
    "beneficiary_reference": "Beneficiary / employer reference (requires a default)",
    "payslip_id": "Payslip ID",
    "date": "Payslip date",
    "pay_date": "Payment date",
    "company_id": "Company ID",
    "company_name": "Company name",
    "month_str": "Payroll month",
    "period": "Payroll period",
    "payroll_period": "Payroll period (alias)",
}
ALLOWED_SOURCE_FIELDS = frozenset(SOURCE_FIELD_OPTIONS)

# The Super Admin editor uses the first five names.  The additional aliases
# keep existing seed definitions and older saved drafts backwards compatible.
FORMAT_OPTIONS = {
    "trim": "Trimmed text",
    "uppercase": "Upper-case text",
    "digits_only": "Digits only",
    "fixed_2": "Amount with two decimals",
    "alphanumeric": "Letters and digits only",
    "account_type": "Normalised account type",
    "lowercase": "Lower-case text",
    "date_yyyymmdd": "Date as YYYYMMDD",
    "text": "Text (legacy alias)",
    "digits": "Digits only (legacy alias)",
    "decimal_2": "Amount with two decimals (legacy alias)",
}
ALLOWED_FORMATS = frozenset(FORMAT_OPTIONS) | frozenset(("",))

ALLOWED_DELIMITERS = frozenset((",", ";", "\t", "|"))
ALLOWED_QUOTE_MODES = frozenset(("minimal", "all", "none", "nonnumeric"))
ALLOWED_ENCODINGS = frozenset(("utf-8", "utf-8-sig", "ascii", "windows-1252"))
ALLOWED_LINE_ENDINGS = frozenset(("CRLF", "LF", "\r\n", "\n"))
ALLOWED_DEFAULT_PLACEHOLDERS = ALLOWED_SOURCE_FIELDS | frozenset(
    (
        "company",
        "month",
        "period_compact",
        "company_name_compact",
        "current_date",
        "template_key",
    )
)
ALLOWED_FILENAME_PLACEHOLDERS = frozenset(
    (
        "company",
        "company_id",
        "company_name",
        "company_name_compact",
        "month",
        "period",
        "period_compact",
        "date",
        "current_date",
        "template_key",
    )
)

_DEFINITION_KEYS = frozenset(
    (
        "schema_version",
        "delimiter",
        "quote_mode",
        "encoding",
        "line_ending",
        "include_header",
        "filename_pattern",
        "file_extension",
        "columns",
    )
)
_COLUMN_KEYS = frozenset(
    (
        "header",
        "source",
        "format",
        "required",
        "exact_length",
        "max_length",
        "pad_left",
        "pad_character",
        "truncate",
        "default",
        "literal",
        "min_value",
        "max_value",
    )
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _actor(value: Any) -> str:
    return str(value or "system")[:120]


def _row_dict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return dict(row)
    if isinstance(row, Mapping):
        return {key: row[key] for key in row.keys()}
    try:
        return dict(row)
    except Exception:
        return {}


def _json_load(value: Any, fallback: Any = None) -> Any:
    if isinstance(value, (dict, list)):
        return deepcopy(value)
    try:
        return json.loads(str(value or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return deepcopy(fallback)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _normalise_key(value: Any) -> str:
    key = str(value or "").strip().lower()
    if not re.fullmatch(r"[a-z][a-z0-9_]{1,63}", key):
        raise BankExportError("Template key must use 2-64 lower-case letters, numbers or underscores.")
    return key


def _safe_note(value: Any, *, maximum: int = 1000) -> str:
    return str(value or "").strip()[:maximum]


@contextmanager
def _atomic_write(conn: Any):
    """Run one lifecycle mutation atomically on SQLite or PostgreSQL.

    Easy Admin's PostgreSQL compatibility connection normally runs in
    autocommit mode.  Lifecycle operations update a template, a version and an
    audit event together, so they must temporarily use an explicit transaction.
    A savepoint is used when a caller has already opened a transaction.
    """
    raw_conn = getattr(conn, "_conn", None)
    savepoint = "easyadmin_bank_export_lifecycle"
    owns_transaction = False
    uses_savepoint = False
    if raw_conn is not None:
        if raw_conn.autocommit:
            raw_conn.autocommit = False
            owns_transaction = True
        else:
            conn.execute(f"SAVEPOINT {savepoint}")
            uses_savepoint = True
    else:
        if bool(getattr(conn, "in_transaction", False)):
            conn.execute(f"SAVEPOINT {savepoint}")
            uses_savepoint = True
        else:
            conn.execute("BEGIN IMMEDIATE")
            owns_transaction = True
    try:
        yield
        if uses_savepoint:
            conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        elif raw_conn is not None:
            raw_conn.commit()
        else:
            conn.commit()
    except Exception:
        try:
            if uses_savepoint:
                conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                conn.execute(f"RELEASE SAVEPOINT {savepoint}")
            elif raw_conn is not None:
                raw_conn.rollback()
            else:
                conn.rollback()
        finally:
            if raw_conn is not None and owns_transaction:
                try:
                    raw_conn.autocommit = True
                except Exception:
                    pass
        raise
    else:
        if raw_conn is not None and owns_transaction:
            try:
                raw_conn.autocommit = True
            except Exception:
                pass


def _normalise_account_type(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"cheque", "current", "transmission"}:
        return "Current"
    if text in {"savings", "save"}:
        return "Savings"
    if text == "credit":
        return "Credit"
    return text.title() if text else ""


def _placeholder_names(value: str) -> tuple[set[str], str | None]:
    names: set[str] = set()
    try:
        for _literal, field_name, format_spec, conversion in Formatter().parse(value):
            if field_name is None:
                continue
            if not field_name or "." in field_name or "[" in field_name or "]" in field_name:
                return names, "Nested or indexed placeholders are not permitted."
            if format_spec or conversion:
                return names, "Placeholder format and conversion expressions are not permitted."
            names.add(field_name)
    except ValueError:
        return names, "The placeholder syntax is invalid."
    return names, None


def blank_definition() -> dict[str, Any]:
    """Return a safe starting definition for the Super Admin editor."""
    return {
        "schema_version": SCHEMA_VERSION,
        "delimiter": ",",
        "quote_mode": "minimal",
        "encoding": "utf-8",
        "line_ending": "CRLF",
        "include_header": True,
        "filename_pattern": "Payroll_{company_name}_{period}",
        "file_extension": ".csv",
        "columns": [
            {
                "header": "Employee Name",
                "source": "name",
                "format": "trim",
                "required": True,
            },
            {
                "header": "Amount",
                "source": "net_salary",
                "format": "fixed_2",
                "required": True,
                "min_value": "0.01",
            },
        ],
    }


def validate_definition(definition: Any) -> list[str]:
    """Return all safety/schema errors in a declarative template definition."""
    errors: list[str] = []
    if not isinstance(definition, dict):
        return ["The template definition must be a JSON object."]

    unknown = sorted(set(definition) - _DEFINITION_KEYS)
    if unknown:
        errors.append("Unsupported template setting(s): " + ", ".join(unknown) + ".")
    try:
        # Definitions created by the structured browser editor omit this
        # internal marker; absence means the current schema, while any explicit
        # incompatible value is rejected.
        schema_version = int(definition.get("schema_version", SCHEMA_VERSION))
    except (TypeError, ValueError):
        schema_version = 0
    if schema_version != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}.")

    delimiter = definition.get("delimiter")
    if delimiter not in ALLOWED_DELIMITERS:
        errors.append("Delimiter must be comma, semicolon, tab or pipe.")
    if definition.get("quote_mode") not in ALLOWED_QUOTE_MODES:
        errors.append("quote_mode is not supported.")
    if definition.get("encoding") not in ALLOWED_ENCODINGS:
        errors.append("Encoding must be utf-8, utf-8-sig, ascii or windows-1252.")
    if definition.get("line_ending") not in ALLOWED_LINE_ENDINGS:
        errors.append("Line ending must be CRLF or LF.")
    if not isinstance(definition.get("include_header"), bool):
        errors.append("include_header must be true or false.")

    extension = str(definition.get("file_extension") or "")
    if not re.fullmatch(r"\.[A-Za-z0-9]{1,8}", extension):
        errors.append("file_extension must be a short extension such as .csv.")

    filename_pattern = definition.get("filename_pattern")
    if not isinstance(filename_pattern, str) or not filename_pattern.strip() or len(filename_pattern) > 160:
        errors.append("filename_pattern is required and may not exceed 160 characters.")
    elif any(character in filename_pattern for character in ("/", "\\", "\0")):
        errors.append("filename_pattern may not contain folders or path separators.")
    else:
        fields, placeholder_error = _placeholder_names(filename_pattern)
        if placeholder_error:
            errors.append("filename_pattern: " + placeholder_error)
        unexpected = sorted(fields - ALLOWED_FILENAME_PLACEHOLDERS)
        if unexpected:
            errors.append("filename_pattern uses unsupported placeholder(s): " + ", ".join(unexpected) + ".")

    columns = definition.get("columns")
    if not isinstance(columns, list) or not columns:
        errors.append("At least one export column is required.")
        return errors
    if len(columns) > MAX_COLUMNS:
        errors.append(f"No more than {MAX_COLUMNS} columns are permitted.")

    for index, column in enumerate(columns, start=1):
        prefix = f"Column {index}"
        if not isinstance(column, dict):
            errors.append(f"{prefix} must be an object.")
            continue
        unknown_column_keys = sorted(set(column) - _COLUMN_KEYS)
        if unknown_column_keys:
            errors.append(f"{prefix} has unsupported setting(s): " + ", ".join(unknown_column_keys) + ".")
        header = column.get("header")
        if not isinstance(header, str) or len(header) > 100:
            errors.append(f"{prefix} header must be text no longer than 100 characters.")
        elif definition.get("include_header") and not header.strip():
            errors.append(f"{prefix} requires a header because this file includes a header row.")

        source = column.get("source")
        literal_value = column.get("literal")
        has_literal = literal_value is not None and (not isinstance(literal_value, str) or literal_value != "")
        if source and has_literal:
            errors.append(f"{prefix} cannot use both source and literal.")
        elif not source and not has_literal:
            errors.append(f"{prefix} must use an approved source field or a literal value.")
        elif source and source not in ALLOWED_SOURCE_FIELDS:
            errors.append(f"{prefix} source field is not permitted.")
        if has_literal and (not isinstance(column.get("literal"), (str, int, float)) or len(str(column.get("literal"))) > 200):
            errors.append(f"{prefix} literal must be a simple value no longer than 200 characters.")

        value_format = column.get("format", "trim")
        if value_format not in ALLOWED_FORMATS:
            errors.append(f"{prefix} format is not permitted.")
        for boolean_key in ("required", "truncate"):
            if boolean_key in column and not isinstance(column[boolean_key], bool):
                errors.append(f"{prefix} {boolean_key} must be true or false.")

        numeric_lengths: dict[str, int] = {}
        for length_key in ("exact_length", "max_length"):
            if length_key not in column or column[length_key] in (None, ""):
                continue
            try:
                length_value = int(column[length_key])
                numeric_lengths[length_key] = length_value
                if not 1 <= length_value <= 500:
                    raise ValueError
            except (TypeError, ValueError):
                errors.append(f"{prefix} {length_key} must be between 1 and 500.")
        if "exact_length" in numeric_lengths and "max_length" in numeric_lengths:
            if numeric_lengths["max_length"] < numeric_lengths["exact_length"]:
                errors.append(f"{prefix} max_length may not be shorter than exact_length.")
        if "pad_left" in column and not isinstance(column.get("pad_left"), bool):
            errors.append(f"{prefix} pad_left must be true or false.")
        if column.get("pad_left") and not (numeric_lengths.get("exact_length") or numeric_lengths.get("max_length")):
            errors.append(f"{prefix} pad_left requires exact_length or max_length.")
        if column.get("pad_left"):
            pad_character = str(column.get("pad_character", "0"))
            if len(pad_character) != 1 or ord(pad_character) < 32 or ord(pad_character) > 126:
                errors.append(f"{prefix} pad_character must be one printable character.")
        # The structured editor always submits its padding-character control;
        # it is inert when pad_left is false.

        if "default" in column:
            default = column.get("default")
            if not isinstance(default, (str, int, float)) or len(str(default)) > 300:
                errors.append(f"{prefix} default must be a simple value no longer than 300 characters.")
            elif isinstance(default, str):
                fields, placeholder_error = _placeholder_names(default)
                if placeholder_error:
                    errors.append(f"{prefix} default: {placeholder_error}")
                unexpected = sorted(fields - ALLOWED_DEFAULT_PLACEHOLDERS)
                if unexpected:
                    errors.append(f"{prefix} default uses unsupported placeholder(s): " + ", ".join(unexpected) + ".")
        if source == "beneficiary_reference" and column.get("default") in (None, ""):
            errors.append(f"{prefix} beneficiary_reference requires a default value for live payroll exports.")

        for amount_key in ("min_value", "max_value"):
            if amount_key not in column or column[amount_key] in (None, ""):
                continue
            try:
                Decimal(str(column[amount_key]))
            except (InvalidOperation, TypeError, ValueError):
                errors.append(f"{prefix} {amount_key} must be numeric.")
        if column.get("min_value") not in (None, "") and column.get("max_value") not in (None, ""):
            try:
                if Decimal(str(column["min_value"])) > Decimal(str(column["max_value"])):
                    errors.append(f"{prefix} min_value may not exceed max_value.")
            except (InvalidOperation, TypeError, ValueError):
                pass
    return errors


def _definition_or_raise(definition: Any) -> dict[str, Any]:
    errors = validate_definition(definition)
    if errors:
        raise BankExportDefinitionError(errors)
    return deepcopy(definition)


def _context_values(context: Mapping[str, Any] | None = None) -> dict[str, str]:
    raw = dict(context or {})
    period = str(raw.get("period") or raw.get("month_str") or raw.get("payroll_period") or "").strip()
    company_name = str(raw.get("company_name") or "EasyAdmin").strip()
    values = {key: str(raw.get(key) or "") for key in ALLOWED_SOURCE_FIELDS}
    values.update(
        {
            "company": company_name,
            "period": period,
            "month": period,
            "month_str": period,
            "payroll_period": period,
            "company_name": company_name,
            "period_compact": re.sub(r"[^A-Za-z0-9]", "", period),
            "company_name_compact": re.sub(r"[^A-Za-z0-9]", "", company_name),
            "current_date": str(raw.get("current_date") or raw.get("date") or date.today().isoformat()),
            "template_key": str(raw.get("template_key") or "bank_export"),
        }
    )
    values["date"] = values["current_date"]
    values["pay_date"] = str(raw.get("pay_date") or raw.get("date") or values["current_date"])
    return values


def _surname_initials(row: Mapping[str, Any]) -> str:
    explicit = str(row.get("surname_initials") or "").strip()
    if explicit:
        return explicit
    surname = str(row.get("surname") or "").strip()
    initials = str(row.get("initials") or "").strip()
    if surname:
        return surname + re.sub(r"[^A-Za-z0-9]", "", initials)
    name = str(row.get("name") or row.get("employee_name") or row.get("full_name") or "").strip()
    parts = re.findall(r"[A-Za-z0-9]+", name)
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return parts[-1] + "".join(part[0] for part in parts[:-1] if part)


def _derived_name_part(row: Mapping[str, Any], part: str) -> str:
    explicit = str(row.get(part) or "").strip()
    if explicit:
        return explicit
    name = str(row.get("name") or row.get("employee_name") or row.get("full_name") or "").strip()
    pieces = re.findall(r"[A-Za-z0-9]+", name)
    if not pieces:
        return ""
    if part == "surname":
        return pieces[-1]
    given_names = pieces[:-1] if len(pieces) > 1 else pieces
    return "".join(piece[0] for piece in given_names if piece)


def _source_value(row: Mapping[str, Any], source: str, context: Mapping[str, str]) -> Any:
    if source == "surname_initials":
        return _surname_initials(row)
    if source in {"surname", "initials"}:
        return _derived_name_part(row, source)
    aliases = {
        "employee_number": ("employee_number", "emp_number"),
        "emp_number": ("emp_number", "employee_number"),
        "employee_name": ("employee_name", "name", "full_name"),
        "full_name": ("full_name", "name", "employee_name"),
        "name": ("name", "employee_name", "full_name"),
        "amount": ("amount", "net_salary"),
        "net_salary": ("net_salary", "amount"),
        "period": ("period", "month_str", "payroll_period"),
        "month_str": ("month_str", "period", "payroll_period"),
        "payroll_period": ("payroll_period", "period", "month_str"),
        "pay_date": ("pay_date", "date"),
    }
    for key in aliases.get(source, (source,)):
        value = row.get(key)
        if value is not None and str(value).strip() != "":
            return value
    return context.get(source, "")


def _render_safe_pattern(pattern: Any, row: Mapping[str, Any], context: Mapping[str, str]) -> str:
    text = str(pattern)
    values = dict(context)
    for source in ALLOWED_SOURCE_FIELDS:
        value = _source_value(row, source, context)
        values[source] = "" if value is None else str(value)
    return text.format_map(values)


def _format_value(raw_value: Any, column: Mapping[str, Any]) -> tuple[str, Decimal | None]:
    value_format = str(column.get("format") or "trim")
    value_format = {
        "text": "trim",
        "digits": "digits_only",
        "decimal_2": "fixed_2",
    }.get(value_format, value_format)
    text = str(raw_value if raw_value is not None else "").strip()
    decimal_value: Decimal | None = None
    if value_format == "uppercase":
        text = text.upper()
    elif value_format == "lowercase":
        text = text.lower()
    elif value_format == "digits_only":
        if text and not re.fullmatch(r"[0-9]+", text):
            raise ValueError("must contain digits only")
    elif value_format == "alphanumeric":
        if text and not re.fullmatch(r"[A-Za-z0-9]+", text):
            raise ValueError("must contain letters and digits only (no spaces or special characters)")
    elif value_format == "fixed_2":
        try:
            decimal_value = Decimal(text).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        except (InvalidOperation, TypeError, ValueError):
            raise ValueError("must be a valid amount") from None
        if not decimal_value.is_finite():
            raise ValueError("must be a finite amount")
        text = f"{decimal_value:.2f}"
    elif value_format == "account_type":
        text = _normalise_account_type(text)
    elif value_format == "date_yyyymmdd":
        parsed: date | None = None
        if isinstance(raw_value, datetime):
            parsed = raw_value.date()
        elif isinstance(raw_value, date):
            parsed = raw_value
        else:
            for candidate in ("%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y"):
                try:
                    parsed = datetime.strptime(text[:10], candidate).date()
                    break
                except (TypeError, ValueError):
                    continue
        if parsed is None:
            raise ValueError("must be a valid date")
        text = parsed.strftime("%Y%m%d")
    return text, decimal_value


def _render_column(
    column: Mapping[str, Any],
    row: Mapping[str, Any],
    context: Mapping[str, str],
) -> str:
    literal_value = column.get("literal")
    has_literal = literal_value is not None and (not isinstance(literal_value, str) or literal_value != "")
    if has_literal:
        raw_value: Any = column.get("literal")
    else:
        raw_value = _source_value(row, str(column.get("source") or ""), context)
    if raw_value is None or str(raw_value).strip() == "":
        if "default" in column:
            raw_value = _render_safe_pattern(column.get("default"), row, context)
    if raw_value is None or str(raw_value).strip() == "":
        if column.get("required"):
            raise ValueError("is required")
        return ""

    text, decimal_value = _format_value(raw_value, column)
    if decimal_value is None and column.get("format") in {"decimal_2", "fixed_2"}:
        decimal_value = Decimal(text)
    if decimal_value is not None:
        if column.get("min_value") not in (None, "") and decimal_value < Decimal(str(column["min_value"])):
            raise ValueError(f"must be at least {column['min_value']}")
        if column.get("max_value") not in (None, "") and decimal_value > Decimal(str(column["max_value"])):
            raise ValueError(f"must not exceed {column['max_value']}")

    if column.get("pad_left"):
        target = int(column.get("exact_length") or column.get("max_length"))
        if len(text) < target:
            text = text.rjust(target, str(column.get("pad_character", "0")))
    if column.get("truncate") and column.get("max_length") not in (None, ""):
        text = text[: int(column["max_length"])]
    if column.get("exact_length") not in (None, "") and len(text) != int(column["exact_length"]):
        raise ValueError(f"must be exactly {int(column['exact_length'])} characters")
    if column.get("max_length") not in (None, "") and len(text) > int(column["max_length"]):
        raise ValueError(f"must not exceed {int(column['max_length'])} characters")
    return text


def validate_rows(
    definition: Mapping[str, Any],
    rows: Iterable[Mapping[str, Any]],
    *,
    context: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Validate every payment row without exposing bank values in errors."""
    definition_errors = validate_definition(definition)
    if definition_errors:
        return [
            {"row_number": 0, "employee": "Template", "field": "definition", "message": message}
            for message in definition_errors
        ]
    values = _context_values(context)
    issues: list[dict[str, Any]] = []
    for row_number, input_row in enumerate(rows, start=1):
        row = _row_dict(input_row)
        employee = str(row.get("name") or row.get("employee_name") or row.get("emp_number") or f"Row {row_number}")
        for column_number, column in enumerate(definition["columns"], start=1):
            try:
                _render_column(column, row, values)
            except (InvalidOperation, KeyError, TypeError, ValueError) as exc:
                field = str(column.get("header") or column.get("source") or f"Column {column_number}")
                issues.append(
                    {
                        "row_number": row_number,
                        "employee": employee[:160],
                        "field": field[:100],
                        "message": f"{employee}: {field} {exc}",
                    }
                )
    return issues


def _csv_quoting(name: str) -> int:
    return {
        "minimal": csv.QUOTE_MINIMAL,
        "all": csv.QUOTE_ALL,
        "none": csv.QUOTE_NONE,
        "nonnumeric": csv.QUOTE_NONNUMERIC,
    }[name]


def _line_ending(value: str) -> str:
    return {"CRLF": "\r\n", "LF": "\n"}.get(value, value)


def render_export(
    definition: Mapping[str, Any],
    rows: Iterable[Mapping[str, Any]],
    *,
    context: Mapping[str, Any] | None = None,
) -> str:
    """Validate and render an entire bank CSV; partial files are never returned."""
    safe_definition = _definition_or_raise(definition)
    row_list = [_row_dict(row) for row in rows]
    issues = validate_rows(safe_definition, row_list, context=context)
    if issues:
        raise BankExportValidationError(issues)
    values = _context_values(context)
    output = io.StringIO(newline="")
    writer = csv.writer(
        output,
        delimiter=safe_definition["delimiter"],
        lineterminator=_line_ending(safe_definition["line_ending"]),
        quoting=_csv_quoting(safe_definition["quote_mode"]),
        escapechar="\\" if safe_definition["quote_mode"] == "none" else None,
    )
    if safe_definition["include_header"]:
        writer.writerow([column.get("header", "") for column in safe_definition["columns"]])
    for row in row_list:
        writer.writerow([_render_column(column, row, values) for column in safe_definition["columns"]])
    content = output.getvalue()
    try:
        content.encode(safe_definition["encoding"])
    except UnicodeEncodeError as exc:
        issue = {
            "row_number": 0,
            "employee": "Export",
            "field": "encoding",
            "message": f"The file contains text that cannot be represented using {safe_definition['encoding']}.",
        }
        raise BankExportValidationError([issue]) from exc
    return content


def render_export_bytes(
    definition: Mapping[str, Any],
    rows: Iterable[Mapping[str, Any]],
    *,
    context: Mapping[str, Any] | None = None,
) -> bytes:
    content = render_export(definition, rows, context=context)
    return content.encode(str(definition["encoding"]))


def build_filename(template_or_definition: Mapping[str, Any], context: Mapping[str, Any] | None = None) -> str:
    """Build a safe basename from an approved template filename pattern."""
    definition: Mapping[str, Any] = template_or_definition.get("definition") or template_or_definition
    safe_definition = _definition_or_raise(definition)
    raw_context = dict(context or {})
    if template_or_definition.get("template_key") and not raw_context.get("template_key"):
        raw_context["template_key"] = template_or_definition["template_key"]
    values = _context_values(raw_context)
    pattern = str(safe_definition["filename_pattern"])
    filename = pattern.format_map({key: values.get(key, "") for key in ALLOWED_FILENAME_PLACEHOLDERS})
    filename = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", "_", filename)
    filename = re.sub(r"\s+", "_", filename).strip(" ._") or "payroll_bank_export"
    extension = str(safe_definition["file_extension"]).lower()
    if not filename.lower().endswith(extension):
        filename += extension
    if len(filename) > MAX_FILENAME_LENGTH:
        stem = filename[: MAX_FILENAME_LENGTH - len(extension)].rstrip(" ._")
        filename = (stem or "payroll_bank_export") + extension
    return filename


def ensure_schema(conn: Any, *, seed: bool = True, actor: Any = "system") -> None:
    """Create portable global template/version/event tables and optionally seed formats."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS bank_export_templates (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               template_key TEXT NOT NULL UNIQUE,
               display_name TEXT NOT NULL,
               bank_name TEXT NOT NULL,
               description TEXT DEFAULT '',
               is_active INTEGER NOT NULL DEFAULT 1,
               is_system INTEGER NOT NULL DEFAULT 0,
               active_version_id INTEGER,
               created_by TEXT,
               created_at TEXT DEFAULT CURRENT_TIMESTAMP,
               updated_by TEXT,
               updated_at TEXT DEFAULT CURRENT_TIMESTAMP
           )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS bank_export_template_versions (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               template_id INTEGER NOT NULL,
               version_number INTEGER NOT NULL,
               display_name TEXT,
               bank_name TEXT,
               description TEXT DEFAULT '',
               definition_json TEXT NOT NULL,
               status TEXT NOT NULL DEFAULT 'draft',
               change_note TEXT DEFAULT '',
               source_reference_url TEXT DEFAULT '',
               source_document_name TEXT DEFAULT '',
               definition_checksum TEXT NOT NULL,
               test_status TEXT DEFAULT 'not_tested',
               test_result_json TEXT DEFAULT '',
               tested_at TEXT,
               created_by TEXT,
               created_at TEXT DEFAULT CURRENT_TIMESTAMP,
               activated_by TEXT,
               activated_at TEXT,
               UNIQUE(template_id, version_number)
           )"""
    )
    # Add versioned display metadata to databases created by the first release
    # candidate. PostgreSQL's compatibility layer adds IF NOT EXISTS; SQLite
    # reports an already-existing column, which is safe to ignore here.
    for column_sql in (
        "ALTER TABLE bank_export_template_versions ADD COLUMN display_name TEXT",
        "ALTER TABLE bank_export_template_versions ADD COLUMN bank_name TEXT",
        "ALTER TABLE bank_export_template_versions ADD COLUMN description TEXT DEFAULT ''",
    ):
        try:
            conn.execute(column_sql)
        except Exception:
            pass
    conn.execute(
        """CREATE TABLE IF NOT EXISTS bank_export_template_events (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               template_id INTEGER,
               version_id INTEGER,
               action TEXT NOT NULL,
               actor TEXT,
               details_json TEXT DEFAULT '',
               created_at TEXT DEFAULT CURRENT_TIMESTAMP
           )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_bank_export_versions_template ON bank_export_template_versions(template_id, version_number)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_bank_export_events_template ON bank_export_template_events(template_id, created_at)")
    conn.execute(
        """UPDATE bank_export_template_versions
           SET display_name=(SELECT display_name FROM bank_export_templates
                             WHERE bank_export_templates.id=bank_export_template_versions.template_id)
           WHERE display_name IS NULL OR TRIM(display_name)=''"""
    )
    conn.execute(
        """UPDATE bank_export_template_versions
           SET bank_name=(SELECT bank_name FROM bank_export_templates
                          WHERE bank_export_templates.id=bank_export_template_versions.template_id)
           WHERE bank_name IS NULL OR TRIM(bank_name)=''"""
    )
    conn.execute(
        """UPDATE bank_export_template_versions
           SET description=COALESCE((SELECT description FROM bank_export_templates
                                     WHERE bank_export_templates.id=bank_export_template_versions.template_id), '')
           WHERE description IS NULL"""
    )
    if seed:
        _seed_templates(conn, actor=actor)
    conn.commit()


def _column(
    header: str,
    source: str | None = None,
    *,
    literal: Any = None,
    value_format: str = "trim",
    required: bool = True,
    default: Any = None,
    **rules: Any,
) -> dict[str, Any]:
    result: dict[str, Any] = {"header": header, "format": value_format, "required": required}
    if source:
        result["source"] = source
    else:
        result["literal"] = literal
    if default is not None:
        result["default"] = default
    result.update(rules)
    return result


def _base_definition(filename_pattern: str, columns: list[dict[str, Any]], *, include_header: bool = True, encoding: str = "utf-8") -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "delimiter": ",",
        "quote_mode": "minimal",
        "encoding": encoding,
        "line_ending": "CRLF",
        "include_header": include_header,
        "filename_pattern": filename_pattern,
        "file_extension": ".csv",
        "columns": columns,
    }


def _seed_specs() -> tuple[dict[str, Any], ...]:
    amount = lambda header="Amount": _column(header, "net_salary", value_format="fixed_2", min_value="0.01")
    account_holder = lambda header: _column(header, "account_holder", default="{name}")
    payment_reference = lambda header: _column(header, "payment_reference", default="Salary{period_compact}")
    return (
        {
            "template_key": "generic_csv",
            "display_name": "Generic CSV",
            "bank_name": "Generic",
            "description": "General payroll bank payment CSV.",
            "definition": _base_definition(
                "Payroll_{company_name}_{period}_Generic",
                [
                    _column("Employee Number", "emp_number"),
                    _column("Employee Name", "name"),
                    _column("Bank Name", "bank_name"),
                    account_holder("Account Holder"),
                    _column("Account Number", "account_number"),
                    _column("Branch Code", "branch_code"),
                    _column("Account Type", "account_type", value_format="account_type"),
                    amount(),
                    payment_reference("Payment Reference"),
                ],
            ),
        },
        {
            "template_key": "fnb_enterprise_csv",
            "display_name": "FNB Enterprise CSV",
            "bank_name": "FNB",
            "description": "FNB Enterprise payroll payment CSV.",
            "definition": _base_definition(
                "Payroll_{company_name}_{period}_FNB",
                [
                    account_holder("Recipient Name"),
                    _column("Bank Name", "bank_name"),
                    _column("Account Number", "account_number"),
                    _column("Branch Code", "branch_code"),
                    _column("Account Type", "account_type", value_format="account_type"),
                    amount(),
                    payment_reference("Recipient Reference"),
                    _column("Own Reference", "beneficiary_reference", default="{company_name} Payroll"),
                ],
            ),
        },
        {
            "template_key": "absa_bio_csv",
            "display_name": "ABSA BIO CSV",
            "bank_name": "ABSA",
            "description": "ABSA Business Integrator Online payroll payment CSV.",
            "definition": _base_definition(
                "Payroll_{company_name}_{period}_ABSA",
                [
                    account_holder("Account Holder"),
                    _column("Bank", "bank_name"),
                    _column("Branch Code", "branch_code"),
                    _column("Account Number", "account_number"),
                    _column("Account Type", "account_type", value_format="account_type"),
                    amount(),
                    payment_reference("Statement Reference"),
                    _column("Employee Number", "emp_number"),
                ],
            ),
        },
        {
            "template_key": "standard_bank_bol_csv",
            "display_name": "Standard Bank Business Online CSV",
            "bank_name": "Standard Bank",
            "description": "Standard Bank Business Online payroll payment CSV.",
            "definition": _base_definition(
                "Payroll_{company_name}_{period}_StandardBank",
                [
                    account_holder("Beneficiary Name"),
                    _column("Beneficiary Bank", "bank_name"),
                    _column("Branch Code", "branch_code"),
                    _column("Account Number", "account_number"),
                    _column("Account Type", "account_type", value_format="account_type"),
                    amount("Payment Amount"),
                    payment_reference("Reference"),
                ],
            ),
        },
        {
            "template_key": "nedbank_acb_csv",
            "display_name": "Nedbank / ACB-style CSV",
            "bank_name": "Nedbank",
            "description": "Nedbank / ACB-style payroll payment CSV.",
            "definition": _base_definition(
                "Payroll_{company_name}_{period}_Nedbank",
                [
                    _column("Record Type", literal="PAYMENT"),
                    account_holder("Account Name"),
                    _column("Bank", "bank_name"),
                    _column("Branch Code", "branch_code"),
                    _column("Account Number", "account_number"),
                    _column("Account Type", "account_type", value_format="account_type"),
                    amount(),
                    payment_reference("Reference"),
                ],
            ),
        },
        {
            "template_key": "capitec_business_acb_csv",
            "display_name": "Capitec Business ACB CSV",
            "bank_name": "Capitec Bank",
            "description": "Capitec Business payroll payment CSV based on the Sage Capitec ACB R67 specification.",
            "source_reference_url": "https://za-kb.sage.com/portal/app/portlets/results/viewsolution.jsp?solutionid=230825145254287",
            "source_document_name": "CapitecBankACB_R67",
            "definition": _base_definition(
                "Payroll_{company_name}_{period}_Capitec",
                [
                    _column("Branch Code", "branch_code", value_format="digits_only", exact_length=6),
                    _column(
                        "Account Number",
                        "account_number",
                        value_format="digits_only",
                        exact_length=16,
                        pad_left=True,
                        pad_character="0",
                    ),
                    _column(
                        "Net Salary",
                        "net_salary",
                        value_format="fixed_2",
                        min_value="0.01",
                        max_value="999999.99",
                    ),
                    _column(
                        "Payment Reference",
                        "payment_reference",
                        value_format="alphanumeric",
                        max_length=16,
                        truncate=True,
                        default="Salary{period_compact}",
                    ),
                    _column(
                        "Beneficiary Reference",
                        "beneficiary_reference",
                        value_format="alphanumeric",
                        max_length=16,
                        truncate=True,
                        default="{company_name_compact}",
                    ),
                    _column(
                        "Surname and Initials",
                        "surname_initials",
                        value_format="alphanumeric",
                        max_length=16,
                        truncate=True,
                    ),
                ],
                include_header=False,
                encoding="ascii",
            ),
        },
    )


def _event(conn: Any, template_id: Any, version_id: Any, action: str, actor: Any, details: Mapping[str, Any] | None = None) -> None:
    conn.execute(
        """INSERT INTO bank_export_template_events
               (template_id, version_id, action, actor, details_json, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (template_id, version_id, action[:60], _actor(actor), _canonical_json(dict(details or {})), _utc_now()),
    )


def _seed_templates(conn: Any, *, actor: Any = "system") -> None:
    now = _utc_now()
    for spec in _seed_specs():
        definition = _definition_or_raise(spec["definition"])
        conn.execute(
            """INSERT OR IGNORE INTO bank_export_templates
                   (template_key, display_name, bank_name, description, is_active, is_system,
                    created_by, created_at, updated_by, updated_at)
               VALUES (?, ?, ?, ?, 1, 1, ?, ?, ?, ?)""",
            (
                spec["template_key"],
                spec["display_name"],
                spec["bank_name"],
                spec["description"],
                _actor(actor),
                now,
                _actor(actor),
                now,
            ),
        )
        template = conn.execute(
            "SELECT id, active_version_id FROM bank_export_templates WHERE template_key=?",
            (spec["template_key"],),
        ).fetchone()
        if not template or template["active_version_id"]:
            continue
        checksum = hashlib.sha256(_canonical_json(definition).encode("utf-8")).hexdigest()
        conn.execute(
            """INSERT OR IGNORE INTO bank_export_template_versions
                   (template_id, version_number, display_name, bank_name, description,
                    definition_json, status, change_note,
                    source_reference_url, source_document_name, definition_checksum,
                    test_status, test_result_json, tested_at, created_by, created_at,
                    activated_by, activated_at)
               VALUES (?, 1, ?, ?, ?, ?, 'active', ?, ?, ?, ?, 'passed', ?, ?, ?, ?, ?, ?)""",
            (
                template["id"],
                spec["display_name"],
                spec["bank_name"],
                spec["description"],
                _canonical_json(definition),
                "Initial Easy Admin system template.",
                spec.get("source_reference_url", ""),
                spec.get("source_document_name", ""),
                checksum,
                _canonical_json({"seeded": True}),
                now,
                _actor(actor),
                now,
                _actor(actor),
                now,
            ),
        )
        version = conn.execute(
            """SELECT id FROM bank_export_template_versions
               WHERE template_id=? AND version_number=1""",
            (template["id"],),
        ).fetchone()
        if version:
            conn.execute(
                """UPDATE bank_export_templates
                   SET active_version_id=?, updated_by=?, updated_at=? WHERE id=?""",
                (version["id"], _actor(actor), now, template["id"]),
            )
            _event(conn, template["id"], version["id"], "seeded", actor, {"version_number": 1})


def _version_dict(row: Any) -> dict[str, Any] | None:
    if not row:
        return None
    data = _row_dict(row)
    data["definition"] = _json_load(data.pop("definition_json", ""), {})
    data["test_result"] = _json_load(data.pop("test_result_json", ""), {})
    return data


def _template_dict(conn: Any, row: Any) -> dict[str, Any] | None:
    if not row:
        return None
    data = _row_dict(row)
    data["is_active"] = bool(data.get("is_active"))
    data["is_system"] = bool(data.get("is_system"))
    active_version = get_version(conn, data.get("active_version_id")) if data.get("active_version_id") else None
    data["active_version"] = active_version
    data["definition"] = deepcopy(active_version.get("definition")) if active_version else None
    data["version_number"] = active_version.get("version_number") if active_version else None
    return data


def get_version(conn: Any, version_id: Any) -> dict[str, Any] | None:
    if version_id in (None, ""):
        return None
    row = conn.execute("SELECT * FROM bank_export_template_versions WHERE id=?", (version_id,)).fetchone()
    return _version_dict(row)


def get_versions(conn: Any, template_key: Any) -> list[dict[str, Any]]:
    key = _normalise_key(template_key)
    template = conn.execute("SELECT id FROM bank_export_templates WHERE template_key=?", (key,)).fetchone()
    if not template:
        return []
    rows = conn.execute(
        """SELECT * FROM bank_export_template_versions
           WHERE template_id=? ORDER BY version_number DESC""",
        (template["id"],),
    ).fetchall()
    return [_version_dict(row) for row in rows if row]


def get_export_template(conn: Any, template_key: Any, *, include_inactive: bool = False) -> dict[str, Any] | None:
    key = _normalise_key(template_key)
    row = conn.execute("SELECT * FROM bank_export_templates WHERE template_key=?", (key,)).fetchone()
    if not row or (not include_inactive and not bool(row["is_active"])):
        return None
    result = _template_dict(conn, row)
    if not result or not result.get("active_version"):
        return None
    return result


def get_active_templates(conn: Any) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT * FROM bank_export_templates
           WHERE is_active=1 AND active_version_id IS NOT NULL
           ORDER BY display_name, template_key"""
    ).fetchall()
    return [_template_dict(conn, row) for row in rows if row]


def list_templates(conn: Any, *, include_inactive: bool = True) -> list[dict[str, Any]]:
    sql = "SELECT * FROM bank_export_templates"
    if not include_inactive:
        sql += " WHERE is_active=1"
    sql += " ORDER BY display_name, template_key"
    results: list[dict[str, Any]] = []
    for row in conn.execute(sql).fetchall():
        item = _template_dict(conn, row)
        if not item:
            continue
        counts = conn.execute(
            """SELECT COUNT(*) AS version_count,
                      SUM(CASE WHEN status='draft' THEN 1 ELSE 0 END) AS draft_count
               FROM bank_export_template_versions WHERE template_id=?""",
            (item["id"],),
        ).fetchone()
        item["version_count"] = int((counts["version_count"] if counts else 0) or 0)
        item["draft_count"] = int((counts["draft_count"] if counts else 0) or 0)
        results.append(item)
    return results


def save_draft(
    conn: Any,
    template_key: Any,
    definition: Mapping[str, Any],
    *,
    version_id: Any = None,
    display_name: Any = None,
    bank_name: Any = None,
    description: Any = None,
    change_note: Any = "",
    source_reference_url: Any = "",
    source_document_name: Any = "",
    actor: Any = None,
) -> dict[str, Any]:
    """Create a draft version or update the explicitly selected draft version."""
    key = _normalise_key(template_key)
    safe_definition = _definition_or_raise(definition)
    display = str(display_name or "").strip()
    bank = str(bank_name or "").strip()
    note = _safe_note(change_note)
    if not note:
        raise BankExportError("Enter a version update note before saving the draft.")
    if len(display) > 120 or len(bank) > 120:
        raise BankExportError("Display name and bank name may not exceed 120 characters.")
    source_url = str(source_reference_url or "").strip()
    if source_url and (len(source_url) > 500 or not re.match(r"^https?://", source_url, re.I)):
        raise BankExportError("The source reference must be an http or https URL.")
    now = _utc_now()
    user = _actor(actor)
    checksum = hashlib.sha256(_canonical_json(safe_definition).encode("utf-8")).hexdigest()
    with _atomic_write(conn):
        template_sql = "SELECT * FROM bank_export_templates WHERE template_key=?"
        if getattr(conn, "_conn", None) is not None:
            template_sql += " FOR UPDATE"
        template = conn.execute(template_sql, (key,)).fetchone()
        if not template:
            if not display or not bank:
                raise BankExportError("Display name and bank name are required for a new template.")
            conn.execute(
                """INSERT INTO bank_export_templates
                       (template_key, display_name, bank_name, description, is_active, is_system,
                        created_by, created_at, updated_by, updated_at)
                   VALUES (?, ?, ?, ?, 0, 0, ?, ?, ?, ?)""",
                (key, display, bank, _safe_note(description, maximum=500), user, now, user, now),
            )
            template = conn.execute(
                "SELECT * FROM bank_export_templates WHERE template_key=?", (key,)
            ).fetchone()
        else:
            # Draft metadata belongs to the version. Keep the currently active
            # display details unchanged until this version is activated.
            conn.execute(
                "UPDATE bank_export_templates SET updated_by=?, updated_at=? WHERE id=?",
                (user, now, template["id"]),
            )
        template_id = template["id"]
        if version_id not in (None, ""):
            version_sql = "SELECT * FROM bank_export_template_versions WHERE id=? AND template_id=?"
            if getattr(conn, "_conn", None) is not None:
                version_sql += " FOR UPDATE"
            version = conn.execute(version_sql, (version_id, template_id)).fetchone()
            if not version:
                raise BankExportError("Draft version was not found for this template.")
            if version["status"] != "draft":
                raise BankExportError("Only a draft version can be edited. Create a new version instead.")
            version_display = display or version["display_name"] or template["display_name"]
            version_bank = bank or version["bank_name"] or template["bank_name"]
            version_description = (
                _safe_note(description, maximum=500)
                if description is not None
                else (version["description"] or template["description"] or "")
            )
            conn.execute(
                """UPDATE bank_export_template_versions
                   SET display_name=?, bank_name=?, description=?, definition_json=?, change_note=?,
                       source_reference_url=?, source_document_name=?, definition_checksum=?,
                       test_status='not_tested', test_result_json='', tested_at=NULL
                   WHERE id=?""",
                (
                    version_display,
                    version_bank,
                    version_description,
                    _canonical_json(safe_definition),
                    note,
                    source_url,
                    _safe_note(source_document_name, maximum=200),
                    checksum,
                    version_id,
                ),
            )
            saved_id = version_id
            action = "draft_updated"
        else:
            max_row = conn.execute(
                "SELECT MAX(version_number) AS max_version FROM bank_export_template_versions WHERE template_id=?",
                (template_id,),
            ).fetchone()
            next_version = int((max_row["max_version"] if max_row else 0) or 0) + 1
            version_display = display or template["display_name"]
            version_bank = bank or template["bank_name"]
            version_description = (
                _safe_note(description, maximum=500)
                if description is not None
                else (template["description"] or "")
            )
            conn.execute(
                """INSERT INTO bank_export_template_versions
                       (template_id, version_number, display_name, bank_name, description,
                        definition_json, status, change_note, source_reference_url,
                        source_document_name, definition_checksum, test_status, created_by, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?, ?, 'not_tested', ?, ?)""",
                (
                    template_id,
                    next_version,
                    version_display,
                    version_bank,
                    version_description,
                    _canonical_json(safe_definition),
                    note,
                    source_url,
                    _safe_note(source_document_name, maximum=200),
                    checksum,
                    user,
                    now,
                ),
            )
            saved = conn.execute(
                """SELECT id FROM bank_export_template_versions
                   WHERE template_id=? AND version_number=?""",
                (template_id, next_version),
            ).fetchone()
            saved_id = saved["id"]
            action = "draft_created"
        _event(conn, template_id, saved_id, action, user, {"checksum": checksum})
    return get_version(conn, saved_id) or {}


def _synthetic_candidates(column: Mapping[str, Any], baseline: Any) -> list[str]:
    """Build fictional values likely to satisfy one declarative column."""
    value_format = {
        "text": "trim",
        "digits": "digits_only",
        "decimal_2": "fixed_2",
    }.get(str(column.get("format") or "trim"), str(column.get("format") or "trim"))
    try:
        exact_length = int(column.get("exact_length")) if column.get("exact_length") not in (None, "") else None
    except (TypeError, ValueError):
        exact_length = None
    try:
        max_length = int(column.get("max_length")) if column.get("max_length") not in (None, "") else None
    except (TypeError, ValueError):
        max_length = None
    candidates = [str(baseline or "")]
    if value_format == "fixed_2":
        decimal_candidates: list[Decimal] = [
            Decimal("1.00"),
            Decimal("9.99"),
            Decimal("10.00"),
            Decimal("100.00"),
            Decimal("1000.00"),
            Decimal("1234.56"),
        ]
        for key in ("min_value", "max_value"):
            if column.get(key) not in (None, ""):
                try:
                    decimal_candidates.append(Decimal(str(column[key])))
                except (InvalidOperation, TypeError, ValueError):
                    pass
        if exact_length and exact_length >= 4:
            integer_digits = exact_length - 3
            decimal_candidates.append(Decimal("1" + ("0" * max(0, integer_digits - 1)) + ".00"))
        candidates.extend(f"{value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):.2f}" for value in decimal_candidates)
    elif value_format == "date_yyyymmdd":
        candidates.extend(("2026-10-08", "2026/10/08", "08/10/2026"))
    elif value_format == "account_type":
        candidates.extend(("Current", "Savings", "Credit", "A" * exact_length if exact_length else "Current"))
    else:
        fill = "1" if value_format == "digits_only" else "A"
        if exact_length:
            candidates.append(fill * exact_length)
        if max_length:
            candidates.append(fill * max(1, min(max_length, 8)))
    # Keep order deterministic while avoiding redundant preview attempts.
    return list(dict.fromkeys(candidates))


def synthetic_rows(definition: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """Return definition-aware fictional preview data; never use employee data."""
    row: dict[str, Any] = {
        "emp_number": "TEST001",
        "employee_number": "TEST001",
        "name": "Jane Example",
        "employee_name": "Jane Example",
        "full_name": "Jane Example",
        "surname": "Example",
        "initials": "J",
        "surname_initials": "ExampleJ",
        "id_passport": "9001015009087",
        "bank_name": "ExampleBank",
        "account_holder": "JaneExample",
        "account_number": "1234567890",
        "branch_code": "470010",
        "account_type": "Current",
        "net_salary": "1234.56",
        "amount": "1234.56",
        "payment_reference": "Salary202610",
        "payslip_id": "1001",
        "date": "2026-10-31",
        "pay_date": "2026-10-31",
        "company_id": "TEST",
        "company_name": "EasyAdminTest",
        "month_str": "2026-10",
        "period": "2026-10",
        "payroll_period": "2026-10",
    }
    if not definition or not isinstance(definition.get("columns"), list):
        return [row]

    context = _context_values(
        {"company_id": "TEST", "company_name": "EasyAdminTest", "period": "2026-10", "current_date": "2026-10-08"}
    )
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for column in definition["columns"]:
        if isinstance(column, Mapping) and column.get("source"):
            grouped.setdefault(str(column["source"]), []).append(column)
    for source, columns in grouped.items():
        baseline = _source_value(row, source, context)
        candidates: list[str] = [str(baseline or "")]
        for column in columns:
            candidates.extend(_synthetic_candidates(column, baseline))
        for candidate in dict.fromkeys(candidates):
            trial = dict(row)
            trial[source] = candidate
            try:
                for column in columns:
                    _render_column(column, trial, context)
            except (InvalidOperation, KeyError, TypeError, ValueError):
                continue
            row[source] = candidate
            break
    return [row]


def test_version(
    conn: Any,
    version_id: Any,
    *,
    rows: Iterable[Mapping[str, Any]] | None = None,
    context: Mapping[str, Any] | None = None,
    actor: Any = None,
) -> dict[str, Any]:
    """Test a draft using fictional data by default and persist only safe metadata."""
    version = get_version(conn, version_id)
    if not version:
        raise BankExportError("Template version was not found.")
    row_list = list(rows) if rows is not None else synthetic_rows(version["definition"])
    test_context = {
        "company_id": "TEST",
        "company_name": "EasyAdminTest",
        "period": "2026-10",
        "current_date": "2026-10-08",
    }
    test_context.update(dict(context or {}))
    issues = validate_rows(version["definition"], row_list, context=test_context)
    content = ""
    encoded = b""
    if not issues:
        try:
            content = render_export(version["definition"], row_list, context=test_context)
            encoded = content.encode(version["definition"]["encoding"])
        except (BankExportDefinitionError, BankExportValidationError) as exc:
            if isinstance(exc, BankExportValidationError):
                issues = exc.issues
            else:
                issues = [
                    {"row_number": 0, "employee": "Template", "field": "definition", "message": message}
                    for message in exc.errors
                ]
    result = {
        "valid": not issues,
        "errors": issues,
        "content": content,
        "row_count": len(row_list),
        "byte_length": len(encoded),
        "sha256": hashlib.sha256(encoded).hexdigest() if encoded else "",
        "encoding": version["definition"].get("encoding"),
        "filename": build_filename(version["definition"], test_context) if not issues else "",
    }
    safe_result = {key: value for key, value in result.items() if key != "content"}
    with _atomic_write(conn):
        current_sql = "SELECT template_id, definition_checksum FROM bank_export_template_versions WHERE id=?"
        if getattr(conn, "_conn", None) is not None:
            current_sql += " FOR UPDATE"
        current = conn.execute(current_sql, (version_id,)).fetchone()
        if not current or current["definition_checksum"] != version["definition_checksum"]:
            raise BankExportError("The draft changed while it was being tested. Test the latest version again.")
        cursor = conn.execute(
            """UPDATE bank_export_template_versions
               SET test_status=?, test_result_json=?, tested_at=?
               WHERE id=? AND definition_checksum=?""",
            (
                "passed" if result["valid"] else "failed",
                _canonical_json(safe_result),
                _utc_now(),
                version_id,
                version["definition_checksum"],
            ),
        )
        if getattr(cursor, "rowcount", 1) != 1:
            raise BankExportError("The draft changed while it was being tested. Test the latest version again.")
        _event(
            conn,
            version["template_id"],
            version_id,
            "test_passed" if result["valid"] else "test_failed",
            actor,
            {"row_count": len(row_list)},
        )
    return result


def activate_version(
    conn: Any,
    template_key: Any,
    version_id: Any,
    *,
    actor: Any = None,
    change_note: Any = "",
) -> dict[str, Any]:
    key = _normalise_key(template_key)
    now = _utc_now()
    user = _actor(actor)
    with _atomic_write(conn):
        template_sql = "SELECT * FROM bank_export_templates WHERE template_key=?"
        if getattr(conn, "_conn", None) is not None:
            template_sql += " FOR UPDATE"
        template = conn.execute(template_sql, (key,)).fetchone()
        if not template:
            raise BankExportError("Bank export template was not found.")
        version_sql = "SELECT * FROM bank_export_template_versions WHERE id=? AND template_id=?"
        if getattr(conn, "_conn", None) is not None:
            version_sql += " FOR UPDATE"
        version = conn.execute(version_sql, (version_id, template["id"])).fetchone()
        if not version:
            raise BankExportError("Template version was not found.")
        if version["status"] == "active" and template["active_version_id"] == version_id:
            return get_export_template(conn, key, include_inactive=True) or {}
        if version["test_status"] != "passed":
            raise BankExportError("Test this template version successfully before activating it.")
        conn.execute(
            "UPDATE bank_export_template_versions SET status='retired' WHERE template_id=? AND status='active'",
            (template["id"],),
        )
        conn.execute(
            """UPDATE bank_export_template_versions
               SET status='active', activated_by=?, activated_at=?, change_note=? WHERE id=?""",
            (user, now, _safe_note(change_note) or (version["change_note"] or ""), version_id),
        )
        conn.execute(
            """UPDATE bank_export_templates
               SET active_version_id=?, is_active=1, display_name=?, bank_name=?, description=?,
                   updated_by=?, updated_at=? WHERE id=?""",
            (
                version_id,
                version["display_name"] or template["display_name"],
                version["bank_name"] or template["bank_name"],
                version["description"] if version["description"] is not None else (template["description"] or ""),
                user,
                now,
                template["id"],
            ),
        )
        _event(conn, template["id"], version_id, "activated", user, {"version_number": version["version_number"]})
    return get_export_template(conn, key, include_inactive=True) or {}


def rollback_template(
    conn: Any,
    template_key: Any,
    *,
    version_id: Any = None,
    actor: Any = None,
    change_note: Any = "",
) -> dict[str, Any]:
    key = _normalise_key(template_key)
    now = _utc_now()
    user = _actor(actor)
    note = _safe_note(change_note)
    if not note:
        raise BankExportError("Enter a reason before rolling back a bank export template.")
    with _atomic_write(conn):
        template_sql = "SELECT * FROM bank_export_templates WHERE template_key=?"
        if getattr(conn, "_conn", None) is not None:
            template_sql += " FOR UPDATE"
        template = conn.execute(template_sql, (key,)).fetchone()
        if not template:
            raise BankExportError("Bank export template was not found.")
        if version_id in (None, ""):
            target_sql = (
                "SELECT * FROM bank_export_template_versions "
                "WHERE template_id=? AND status='retired' ORDER BY version_number DESC LIMIT 1"
            )
            target_params = (template["id"],)
        else:
            target_sql = (
                "SELECT * FROM bank_export_template_versions "
                "WHERE id=? AND template_id=? AND status='retired'"
            )
            target_params = (version_id, template["id"])
        if getattr(conn, "_conn", None) is not None:
            target_sql += " FOR UPDATE"
        target = conn.execute(target_sql, target_params).fetchone()
        if not target:
            raise BankExportError("No previously active version is available for rollback.")
        conn.execute(
            "UPDATE bank_export_template_versions SET status='retired' WHERE template_id=? AND status='active'",
            (template["id"],),
        )
        conn.execute(
            "UPDATE bank_export_template_versions SET status='active', activated_by=?, activated_at=? WHERE id=?",
            (user, now, target["id"]),
        )
        conn.execute(
            """UPDATE bank_export_templates
               SET active_version_id=?, is_active=1, display_name=?, bank_name=?, description=?,
                   updated_by=?, updated_at=? WHERE id=?""",
            (
                target["id"],
                target["display_name"] or template["display_name"],
                target["bank_name"] or template["bank_name"],
                target["description"] if target["description"] is not None else (template["description"] or ""),
                user,
                now,
                template["id"],
            ),
        )
        _event(
            conn,
            template["id"],
            target["id"],
            "rolled_back",
            user,
            {"change_note": note, "version_number": target["version_number"]},
        )
    return get_export_template(conn, key, include_inactive=True) or {}


def set_template_active(conn: Any, template_key: Any, is_active: Any, *, actor: Any = None) -> dict[str, Any]:
    key = _normalise_key(template_key)
    enabled = bool(is_active)
    now = _utc_now()
    user = _actor(actor)
    with _atomic_write(conn):
        template_sql = "SELECT * FROM bank_export_templates WHERE template_key=?"
        if getattr(conn, "_conn", None) is not None:
            template_sql += " FOR UPDATE"
        template = conn.execute(template_sql, (key,)).fetchone()
        if not template:
            raise BankExportError("Bank export template was not found.")
        if enabled and not template["active_version_id"]:
            raise BankExportError("Activate a tested version before enabling this template.")
        conn.execute(
            "UPDATE bank_export_templates SET is_active=?, updated_by=?, updated_at=? WHERE id=?",
            (1 if enabled else 0, user, now, template["id"]),
        )
        _event(conn, template["id"], template["active_version_id"], "enabled" if enabled else "disabled", user)
    return get_export_template(conn, key, include_inactive=True) or {}


__all__ = [
    "ALLOWED_FORMATS",
    "ALLOWED_SOURCE_FIELDS",
    "BankExportDefinitionError",
    "BankExportError",
    "BankExportValidationError",
    "FORMAT_OPTIONS",
    "SOURCE_FIELD_OPTIONS",
    "activate_version",
    "blank_definition",
    "build_filename",
    "ensure_schema",
    "get_active_templates",
    "get_export_template",
    "get_version",
    "get_versions",
    "list_templates",
    "render_export",
    "render_export_bytes",
    "rollback_template",
    "save_draft",
    "set_template_active",
    "synthetic_rows",
    "test_version",
    "validate_definition",
    "validate_rows",
]
