"""Review-only SARS compliance artefact helpers.

This module deliberately does not submit data to SARS.  It contains the small,
deterministic building blocks used by Easy Admin's Phase 1 compliance workflow:
return-type validation, immutable snapshot fingerprints and a human-review ZIP
pack.  It has no Flask or database dependency so it can be tested in isolation.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import zipfile
from collections.abc import Mapping, Sequence
from typing import Any


EMP201 = "EMP201"
EMP501 = "EMP501"
VAT201 = "VAT201"

SUPPORTED_RETURN_TYPES = (EMP201, EMP501, VAT201)
REVIEW_ONLY_NOTICE = "REVIEW ONLY - NOT FOR DIRECT SARS SUBMISSION"

_RETURN_TYPE_ALIASES = {
    "EMP201": EMP201,
    "EMP501": EMP501,
    "VAT201": VAT201,
}
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r", "\n")
_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


def normalize_return_type(value: Any) -> str:
    """Return a supported canonical SARS return name or raise ``ValueError``.

    Harmless presentation separators are ignored so values such as ``emp 201``
    and ``VAT-201`` normalize consistently.  No unknown return type is allowed
    through because downstream workflow and validation rules are type-specific.
    """

    if not isinstance(value, str) or not value.strip():
        raise ValueError("A SARS return type is required.")
    normalized = re.sub(r"[\s_-]+", "", value.strip().upper())
    try:
        return _RETURN_TYPE_ALIASES[normalized]
    except KeyError as exc:
        supported = ", ".join(SUPPORTED_RETURN_TYPES)
        raise ValueError(f"Unsupported SARS return type. Supported types: {supported}.") from exc


def validate_return_type(value: Any) -> str:
    """Validate and return the canonical return type.

    This named alias makes call sites that are performing input validation read
    naturally while retaining a single normalization implementation.
    """

    return normalize_return_type(value)


def canonical_json(value: Any) -> str:
    """Serialize JSON data in a stable UTF-8-friendly representation.

    ``allow_nan=False`` is intentional: NaN and infinity are not valid JSON and
    would make cross-system fingerprints unreliable.
    """

    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def snapshot_sha256(snapshot: Any) -> str:
    """Return a deterministic SHA-256 fingerprint for a JSON snapshot."""

    return hashlib.sha256(canonical_json(snapshot).encode("utf-8")).hexdigest()


def snapshot_hash(snapshot: Any) -> str:
    """Backward-friendly name for :func:`snapshot_sha256`."""

    return snapshot_sha256(snapshot)


def _normalized_issue_severity(issue: Any) -> str:
    if not isinstance(issue, Mapping):
        return "unknown"
    if issue.get("blocking") is True:
        return "error"
    raw = str(issue.get("severity") or "").strip().lower()
    if raw in {"error", "critical", "fatal", "blocker", "blocking"}:
        return "error"
    if raw in {"warning", "warn"}:
        return "warning"
    if raw in {"info", "information", "notice"}:
        return "info"
    return "unknown"


def summarize_validation_issues(issues: Any) -> dict[str, Any]:
    """Count validation severities and identify fail-closed blocking issues.

    Unrecognised or malformed issue records count as ``unknown`` and are treated
    as blocking.  This prevents a misspelled severity from accidentally marking
    a compliance review as valid.
    """

    if issues is None:
        issue_list: list[Any] = []
    elif isinstance(issues, Mapping) or isinstance(issues, (str, bytes, bytearray)):
        issue_list = [issues]
    else:
        try:
            issue_list = list(issues)
        except TypeError:
            issue_list = [issues]

    counts = {"error": 0, "warning": 0, "info": 0, "unknown": 0}
    for issue in issue_list:
        counts[_normalized_issue_severity(issue)] += 1

    blocking_count = counts["error"] + counts["unknown"]
    if counts["error"]:
        highest = "error"
    elif counts["unknown"]:
        highest = "unknown"
    elif counts["warning"]:
        highest = "warning"
    elif counts["info"]:
        highest = "info"
    else:
        highest = "none"

    return {
        "total": len(issue_list),
        "error": counts["error"],
        "warning": counts["warning"],
        "info": counts["info"],
        "unknown": counts["unknown"],
        "blocking": blocking_count,
        "has_blocking": bool(blocking_count),
        "is_valid": blocking_count == 0,
        "highest_severity": highest,
        "counts": counts,
    }


def summarize_issues(issues: Any) -> dict[str, int]:
    """Return the compact issue totals consumed by the application layer.

    Unknown severities are counted as errors so a malformed validation result
    cannot accidentally pass a compliance gate.
    """

    summary = summarize_validation_issues(issues)
    return {
        "errors": int(summary["error"]) + int(summary["unknown"]),
        "warnings": int(summary["warning"]),
        "info": int(summary["info"]),
    }


def safe_csv_cell(value: Any) -> str:
    """Convert a value to text and neutralize spreadsheet formula prefixes.

    Numeric Python values remain ordinary numeric text.  User-supplied strings
    beginning with ``=``, ``+``, ``-``, ``@`` or control whitespace are prefixed
    with an apostrophe, including when the dangerous character follows spaces.
    CSV quoting alone does not prevent spreadsheet formula execution.
    """

    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)

    if isinstance(value, (Mapping, list, tuple)):
        text = canonical_json(value)
    else:
        text = str(value)

    first_non_space = text.lstrip(" ")[:1]
    if text[:1] in _FORMULA_PREFIXES or first_non_space in _FORMULA_PREFIXES:
        return "'" + text
    return text


def _json_value(value: Any, field_name: str) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{field_name} does not contain valid JSON.") from exc
    return value


def _stored_snapshot(batch: Mapping[str, Any]) -> dict[str, Any]:
    raw = batch.get("snapshot")
    if raw is None:
        raw = batch.get("snapshot_json")
    if raw is None:
        raw = batch.get("payload_snapshot")
    if raw is None:
        # This compact form is useful when callers already loaded only the
        # persisted snapshot fields from storage.
        raw = {
            key: batch[key]
            for key in ("return_values", "validation_results", "validation_issues", "detail_rows")
            if key in batch
        }
    raw = _json_value(raw, "snapshot")
    if not isinstance(raw, Mapping):
        raise ValueError("The stored compliance snapshot must be a JSON object.")
    # Round-tripping guarantees that the returned object is detached from the
    # caller and composed only of JSON-compatible data.
    return json.loads(canonical_json(raw))


def _sequence_of_rows(value: Any, field_name: str) -> list[dict[str, Any]]:
    value = _json_value(value, field_name)
    if value is None:
        return []
    if isinstance(value, Mapping):
        return [dict(value)]
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise ValueError(f"{field_name} must be a JSON array of row objects.")

    rows: list[dict[str, Any]] = []
    for index, row in enumerate(value, start=1):
        if isinstance(row, Mapping):
            rows.append(dict(row))
        else:
            rows.append({"message": str(row), "row_number": index})
    return rows


def _preferred_headers(rows: Sequence[Mapping[str, Any]], preferred: Sequence[str]) -> list[str]:
    available: set[str] = set()
    for row in rows:
        for key in row.keys():
            available.add(str(key))
    headers = [name for name in preferred if name in available]
    headers.extend(sorted(available.difference(headers)))
    return headers


def _csv_bytes(headers: Sequence[str], rows: Sequence[Mapping[str, Any]]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow([REVIEW_ONLY_NOTICE])
    writer.writerow([])
    writer.writerow([safe_csv_cell(header) for header in headers])
    for row in rows:
        writer.writerow([safe_csv_cell(row.get(header)) for header in headers])
    return output.getvalue().encode("utf-8-sig")


def _return_value_rows(value: Any) -> tuple[list[str], list[dict[str, Any]]]:
    value = _json_value(value, "return_values")
    if value is None:
        return ["field", "value"], []
    if isinstance(value, Mapping):
        rows = [{"field": str(key), "value": value[key]} for key in sorted(value, key=str)]
        return ["field", "value"], rows
    rows = _sequence_of_rows(value, "return_values")
    headers = _preferred_headers(rows, ("field", "value", "code", "description", "amount"))
    return headers or ["field", "value"], rows


def _validation_rows(value: Any) -> tuple[list[str], list[dict[str, Any]]]:
    rows = _sequence_of_rows(value, "validation_results")
    normalized_rows = []
    for row in rows:
        normalized = dict(row)
        normalized["severity"] = _normalized_issue_severity(row)
        normalized_rows.append(normalized)
    preferred = (
        "severity",
        "code",
        "field",
        "message",
        "record_type",
        "record_id",
        "employee_id",
        "reference",
    )
    headers = _preferred_headers(normalized_rows, preferred)
    return headers or ["severity", "code", "field", "message"], normalized_rows


def _detail_rows(value: Any) -> tuple[list[str], list[dict[str, Any]]]:
    rows = _sequence_of_rows(value, "detail_rows")
    headers = _preferred_headers(rows, ())
    return headers or ["detail"], rows


def _key_value_rows(value: Any, field_name: str) -> tuple[list[str], list[dict[str, Any]]]:
    value = _json_value(value, field_name)
    if value is None:
        return ["field", "value"], []
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a JSON object.")
    rows = [
        {"field": str(key), "value": value[key]}
        for key in sorted(value, key=str)
    ]
    return ["field", "value"], rows


def _zip_write(archive: zipfile.ZipFile, filename: str, data: str | bytes) -> None:
    payload = data.encode("utf-8") if isinstance(data, str) else data
    info = zipfile.ZipInfo(filename, date_time=_ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 0
    info.external_attr = 0o600 << 16
    info.flag_bits |= 0x800  # UTF-8 filenames/content metadata.
    archive.writestr(info, payload)


def build_review_pack(batch: Mapping[str, Any], company_name: str = "") -> bytes:
    """Build an in-memory, review-only ZIP from a stored compliance batch.

    Recognised snapshot keys are ``return_values``, ``validation_results`` (or
    ``validation_issues``) and ``detail_rows``.  ``snapshot`` may be a mapping or
    stored JSON string.  The resulting archive is deterministic for identical
    batch data and contains no executable content or direct-submission claim.
    """

    if not isinstance(batch, Mapping):
        raise TypeError("The compliance batch must be a mapping.")

    snapshot = _stored_snapshot(batch)
    return_type = normalize_return_type(batch.get("return_type") or snapshot.get("return_type"))
    return_values = snapshot.get("return_values", snapshot.get("values", {}))
    validation_results = snapshot.get("validation_results")
    if validation_results is None:
        validation_results = snapshot.get("validation_issues")
    if validation_results is None:
        validation_results = batch.get("validation_json", [])
    detail_rows = snapshot.get("detail_rows", snapshot.get("rows", []))
    employer = snapshot.get("employer", {})
    monthly_reconciliation = snapshot.get("monthly_reconciliation", [])

    validation_list = _sequence_of_rows(validation_results, "validation_results")
    validation_summary = summarize_validation_issues(validation_list)
    calculated_hash = snapshot_sha256(snapshot)
    stored_hash = str(
        batch.get("snapshot_sha256")
        or batch.get("snapshot_hash")
        or batch.get("source_hash")
        or ""
    ).strip().lower()

    period = batch.get("period") or batch.get("period_label") or snapshot.get("period") or ""
    if not period and (batch.get("period_start") or batch.get("period_end")):
        period = f"{batch.get('period_start') or ''} to {batch.get('period_end') or ''}".strip()

    manifest = {
        "classification": "REVIEW_ONLY",
        "notice": REVIEW_ONLY_NOTICE,
        "direct_sars_submission": False,
        "return_type": return_type,
        "period": period,
        "period_start": batch.get("period_start") or snapshot.get("period_start") or "",
        "period_end": batch.get("period_end") or snapshot.get("period_end") or "",
        "batch_id": batch.get("id") if batch.get("id") is not None else batch.get("batch_id"),
        "company_id": batch.get("company_id"),
        "company_name": str(company_name or batch.get("company_name") or ""),
        "revision": batch.get("revision") if batch.get("revision") is not None else batch.get("version_no"),
        "status": batch.get("status") or "",
        "brs_version": batch.get("brs_version") or snapshot.get("brs_version") or "",
        "prepared_by": batch.get("prepared_by") or "",
        "prepared_at": batch.get("prepared_at") or "",
        "reviewed_by": batch.get("reviewed_by") or "",
        "reviewed_at": batch.get("reviewed_at") or "",
        "approved_by": batch.get("approved_by") or "",
        "approved_at": batch.get("approved_at") or "",
        "snapshot_sha256": calculated_hash,
        "stored_snapshot_sha256": stored_hash or None,
        "snapshot_hash_matches_stored": (calculated_hash == stored_hash) if stored_hash else None,
        "validation_summary": validation_summary,
        "files": [
            "README.txt",
            "manifest.json",
            "snapshot.json",
            "employer_details.csv",
            "return_values.csv",
            "validation_results.csv",
            "detail_rows.csv",
            "monthly_reconciliation.csv",
        ],
    }

    return_headers, return_rows = _return_value_rows(return_values)
    validation_headers, validation_rows = _validation_rows(validation_list)
    detail_headers, normalized_details = _detail_rows(detail_rows)
    employer_headers, employer_rows = _key_value_rows(employer, "employer")
    monthly_rows = _sequence_of_rows(monthly_reconciliation, "monthly_reconciliation")
    monthly_headers = _preferred_headers(
        monthly_rows,
        ("month", "paye", "uif_total", "sdl", "derived_liability"),
    ) or ["month", "paye", "uif_total", "sdl", "derived_liability"]
    snapshot_document = {
        "classification": "REVIEW_ONLY",
        "notice": REVIEW_ONLY_NOTICE,
        "snapshot_sha256": calculated_hash,
        "snapshot": snapshot,
    }

    readme_lines = [
        REVIEW_ONLY_NOTICE,
        "",
        "This Easy Admin archive is an internal preparation and review pack.",
        "It is not a SARS return, an eFiling/e@syFile import file, or evidence of submission.",
        "Verify all values and validation results before using an authorised SARS channel.",
        "This archive contains sensitive payroll, identity and tax data; keep it access-controlled.",
        "",
        f"Return type: {return_type}",
        f"Company: {manifest['company_name']}",
        f"Period: {manifest['period']}",
        f"Batch ID: {manifest['batch_id'] if manifest['batch_id'] is not None else ''}",
        f"Workflow status: {manifest['status']}",
        f"Snapshot SHA-256: {calculated_hash}",
        f"Validation issues: {validation_summary['total']}",
        f"Blocking issues: {validation_summary['blocking']}",
        "",
        "Archive contents:",
        "- manifest.json: batch metadata and immutable snapshot fingerprint",
        "- snapshot.json: exact stored review snapshot used for the fingerprint",
        "- employer_details.csv: employer registration and responsible-person review fields",
        "- return_values.csv: prepared return values for manual review",
        "- validation_results.csv: errors, warnings and information for review",
        "- detail_rows.csv: supporting transaction or employee-level detail",
        "- monthly_reconciliation.csv: month-by-month payroll liability working values where available",
        "",
    ]

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        _zip_write(archive, "README.txt", "\r\n".join(readme_lines))
        _zip_write(
            archive,
            "manifest.json",
            json.dumps(manifest, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2) + "\n",
        )
        _zip_write(
            archive,
            "snapshot.json",
            json.dumps(snapshot_document, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2) + "\n",
        )
        _zip_write(archive, "employer_details.csv", _csv_bytes(employer_headers, employer_rows))
        _zip_write(archive, "return_values.csv", _csv_bytes(return_headers, return_rows))
        _zip_write(archive, "validation_results.csv", _csv_bytes(validation_headers, validation_rows))
        _zip_write(archive, "detail_rows.csv", _csv_bytes(detail_headers, normalized_details))
        _zip_write(archive, "monthly_reconciliation.csv", _csv_bytes(monthly_headers, monthly_rows))
    return buffer.getvalue()


def build_review_pack_zip(batch: Mapping[str, Any], company_name: str = "") -> bytes:
    """Explicit alias for callers that prefer the archive format in the name."""

    return build_review_pack(batch, company_name=company_name)


__all__ = [
    "EMP201",
    "EMP501",
    "VAT201",
    "SUPPORTED_RETURN_TYPES",
    "REVIEW_ONLY_NOTICE",
    "normalize_return_type",
    "validate_return_type",
    "canonical_json",
    "snapshot_sha256",
    "snapshot_hash",
    "summarize_validation_issues",
    "summarize_issues",
    "safe_csv_cell",
    "build_review_pack",
    "build_review_pack_zip",
]
