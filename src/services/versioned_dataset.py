"""Canonical, dataset-agnostic source/version helpers.

The legacy dashboard stores taxi rows in a typed ORM table.  New uploads do
not use that shape.  This module is intentionally independent from the taxi
models and provides the small contract shared by upload, profiling, Graph 2,
and the versioned explorer.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Any

from src.config import get_settings

MAX_UPLOAD_BYTES = 100 * 1024 * 1024
SUPPORTED_FORMATS = {".csv", ".parquet"}
SOURCE_ADAPTER_VERSION = "versioned-source-adapter-v1"
#: How many failing row ids a result shows a steward. Deliberately small: this is a
#: human-readable illustration, not evidence.
FAILURE_SAMPLE_LIMIT = 20
#: How many failing row ids a result records as machine-readable evidence.
#:
#: Kept separate from FAILURE_SAMPLE_LIMIT because the two answer different
#: questions, and collapsing them silently capped a measurement. Detection recall is
#: computed as |flagged rows ∩ known-defective rows| / |known-defective rows|, so
#: when the only record of what a rule flagged was the 20-row illustration, recall
#: could not exceed 20/|defects| no matter how well the rule performed -- roughly
#: 0.8% on the evaluation fixture, against a gate that requires 80%.
#:
#: Only row identifiers are recorded, never row contents: a list of scalar ids
#: carries no column values and so moves no raw data across the boundary.
#:
#: 500 is chosen against both consumers rather than as a round number. The
#: evaluation fixture carries a few dozen defects per class, so the cap is far from
#: binding on recall; and the steward UI renders every id once the list is expanded,
#: which stays responsive at this size and would not at ten thousand.
EVIDENCE_ROW_ID_LIMIT = 500


class DatasetContractError(ValueError):
    """A source/version/rule violates the canonical dataset contract."""


class SourceIntegrityError(DatasetContractError):
    """A source object cannot be trusted against its recorded metadata."""


class UploadTooLargeError(DatasetContractError):
    """The multipart wire payload exceeded the configured limit."""


@dataclass(frozen=True)
class InspectedUpload:
    filename: str
    format: str
    size_bytes: int
    checksum: str
    row_count: int
    schema: list[dict[str, Any]]


@dataclass(frozen=True)
class SourceArtifactRef:
    bucket: str | None
    object_key: str
    checksum: str
    size_bytes: int
    format: str
    filename: str
    storage_locator: str
    created_by_request: bool = True
    version_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "bucket": self.bucket,
            "object_key": self.object_key,
            "checksum": self.checksum,
            "size_bytes": self.size_bytes,
            "format": self.format,
            "filename": self.filename,
            "storage_locator": self.storage_locator,
            "created_by_request": self.created_by_request,
            "version_id": self.version_id,
        }


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_part(value: str, label: str, max_length: int = 160) -> str:
    value = str(value or "").strip()
    if not value or value in {".", ".."} or "/" in value or "\\" in value:
        raise DatasetContractError(f"Invalid {label}")
    if len(value) > max_length:
        raise DatasetContractError(f"{label} is too long")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value):
        raise DatasetContractError(f"Invalid {label}")
    return value


def safe_source_object_key(
    workspace_id: str,
    dataset_id: str,
    dataset_version_id: str,
    checksum: str,
    filename: str,
) -> str:
    """Build a non-overwriting, traversal-safe source object key."""
    workspace = _safe_part(workspace_id, "workspace_id")
    dataset = _safe_part(dataset_id, "dataset_id")
    version = _safe_part(dataset_version_id, "dataset_version_id")
    digest = _safe_part(checksum.lower(), "checksum", 128)
    original = Path(filename or "dataset.csv").name
    suffix = Path(original).suffix.lower()
    if suffix not in SUPPORTED_FORMATS:
        raise DatasetContractError("Only CSV and Parquet source artifacts are supported")
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", original).strip("._") or f"dataset{suffix}"
    if not safe_name.lower().endswith(suffix):
        safe_name += suffix
    return f"datasets/{workspace}/{dataset}/versions/{version}/{digest}/{safe_name}"


def _safe_filename(filename: str) -> str:
    name = Path(filename or "dataset.csv").name
    suffix = Path(name).suffix.lower()
    if suffix not in SUPPORTED_FORMATS:
        raise DatasetContractError("Only CSV and Parquet files are supported")
    if any(ord(char) < 32 for char in name):
        raise DatasetContractError("Filename contains control characters")
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._") or f"dataset{suffix}"


def _logical_type(series: Any) -> tuple[str, str]:
    import pandas as pd

    dtype = str(series.dtype)
    if pd.api.types.is_bool_dtype(series):
        return "boolean", dtype
    if pd.api.types.is_integer_dtype(series):
        return "integer", dtype
    if pd.api.types.is_float_dtype(series) or pd.api.types.is_numeric_dtype(series):
        return "number", dtype
    if pd.api.types.is_datetime64_any_dtype(series):
        return "timestamp", dtype
    return "string", dtype


def _semantic_role(name: str) -> str | None:
    normalized = name.lower()
    if normalized in {"id", "uuid"} or normalized.endswith("_id"):
        return "identifier"
    if any(token in normalized for token in ("timestamp", "_at", "_date", "_time")):
        return "timestamp"
    return None


def canonical_schema_manifest(frame: Any) -> list[dict[str, Any]]:
    """Serialize a dataframe schema deterministically without raw values."""
    names = [str(name) for name in frame.columns]
    if not names or any(not name.strip() for name in names):
        raise DatasetContractError("Dataset must contain at least one named column")
    if len(set(names)) != len(names):
        raise DatasetContractError("Dataset contains duplicate column names")
    manifest: list[dict[str, Any]] = []
    for ordinal, name in enumerate(names):
        if len(name) > 256 or any(ord(char) < 32 for char in name):
            raise DatasetContractError("Dataset contains an invalid column name")
        logical, physical = _logical_type(frame[name])
        manifest.append({
            "name": name,
            "logical_type": logical,
            "physical_type": physical,
            "nullable": bool(frame[name].isna().any()),
            "ordinal": ordinal,
            "semantic_role": _semantic_role(name),
            "sensitivity": "UNKNOWN",
            "masking": "NONE",
        })
    return manifest


def schema_hash(schema: Iterable[dict[str, Any]]) -> str:
    normalized = [dict(item) for item in schema]
    payload = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def schema_contract_hash(schema: Iterable[dict[str, Any]]) -> str:
    """Hash the stable data contract, independent of pandas physical aliases.

    Pandas 2 represents CSV string columns as ``object`` while pandas 3 may
    represent the same bytes as ``str``.  The immutable source checksum still
    verifies the bytes; schema execution should therefore compare the logical
    contract while retaining the physical dtype in the stored manifest for
    diagnostics.
    """
    normalized: list[dict[str, Any]] = []
    for raw in schema:
        item = dict(raw)
        logical_type = item.get("logical_type")
        if logical_type:
            item["physical_type"] = logical_type
        normalized.append(item)
    payload = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _read_frame(content: bytes, filename: str) -> Any:
    import pandas as pd

    suffix = Path(filename).suffix.lower()
    if suffix == ".parquet":
        try:
            return pd.read_parquet(BytesIO(content))
        except Exception as exc:
            raise DatasetContractError("Parquet content is not readable") from exc
    try:
        return pd.read_csv(BytesIO(content))
    except Exception as exc:
        raise DatasetContractError("CSV content is not readable") from exc


def inspect_upload(content: bytes, filename: str, content_type: str | None = None) -> InspectedUpload:
    if not content:
        raise DatasetContractError("The uploaded file is empty")
    if len(content) > MAX_UPLOAD_BYTES:
        raise DatasetContractError("The upload exceeds the 100 MB limit")
    safe_name = _safe_filename(filename)
    frame = _read_frame(content, safe_name)
    schema = canonical_schema_manifest(frame)
    return InspectedUpload(
        filename=safe_name,
        format=Path(safe_name).suffix.lower().lstrip("."),
        size_bytes=len(content),
        checksum=sha256_bytes(content),
        row_count=int(len(frame)),
        schema=schema,
    )


async def spool_upload(upload: Any, filename: str) -> tuple[Path, int, str]:
    """Stream an UploadFile to disk while enforcing wire size and hashing."""
    settings = get_settings()
    suffix = Path(filename or "dataset.csv").suffix.lower()
    handle = tempfile.NamedTemporaryFile(prefix="upload-", suffix=suffix, delete=False)
    path = Path(handle.name)
    digest = hashlib.sha256()
    total = 0
    try:
        with handle:
            while True:
                chunk = await upload.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > settings.upload_max_bytes:
                    raise UploadTooLargeError("The upload exceeds the 100 MB limit")
                digest.update(chunk)
                handle.write(chunk)
        if total == 0:
            raise DatasetContractError("The uploaded file is empty")
        return path, total, digest.hexdigest()
    except Exception:
        path.unlink(missing_ok=True)
        raise


def inspect_upload_path(path: Path, filename: str, content_type: str | None = None, *, checksum: str | None = None, size_bytes: int | None = None) -> InspectedUpload:
    """Bounded preflight inspection without materializing untrusted input."""
    import pandas as pd
    settings = get_settings()
    safe_name = _safe_filename(filename)
    actual_size = path.stat().st_size
    if actual_size > settings.upload_max_bytes:
        raise UploadTooLargeError("The upload exceeds the 100 MB limit")
    suffix = Path(safe_name).suffix.lower()
    if suffix == ".parquet":
        try:
            import pyarrow.parquet as pq
            parquet = pq.ParquetFile(path)
            columns = parquet.schema_arrow.names
            if len(columns) > settings.upload_max_columns:
                raise DatasetContractError("Dataset exceeds the maximum column limit")
            rows = 0
            decoded = 0
            for batch in parquet.iter_batches(batch_size=50_000):
                rows += batch.num_rows
                decoded += batch.nbytes
                if rows > settings.upload_max_rows or decoded > settings.upload_max_decoded_bytes:
                    raise DatasetContractError("Dataset exceeds decoded resource limits")
            frame = parquet.read().to_pandas()
        except DatasetContractError:
            raise
        except Exception as exc:
            raise DatasetContractError("Parquet content is not readable") from exc
    else:
        rows = 0
        decoded = 0
        first = None
        try:
            for chunk in pd.read_csv(path, chunksize=50_000):
                if first is None:
                    first = chunk
                    if len(chunk.columns) > settings.upload_max_columns:
                        raise DatasetContractError("Dataset exceeds the maximum column limit")
                rows += len(chunk)
                decoded += int(chunk.memory_usage(deep=True).sum())
                if rows > settings.upload_max_rows or decoded > settings.upload_max_decoded_bytes:
                    raise DatasetContractError("Dataset exceeds decoded resource limits")
            if first is None:
                raise DatasetContractError("CSV content is not readable")
            # Schema is determined from the first bounded batch; row/resource
            # limits were enforced cumulatively above, so no full reread is needed.
            frame = first
        except DatasetContractError:
            raise
        except Exception as exc:
            raise DatasetContractError("CSV content is not readable") from exc
    schema = canonical_schema_manifest(frame)
    return InspectedUpload(safe_name, suffix.lstrip("."), int(size_bytes if size_bytes is not None else actual_size), checksum or sha256_file(path), int(rows if suffix == ".parquet" else len(frame) if rows == len(frame) else rows), schema)


def verify_file(path: Path, expected_checksum: str, expected_size: int | None = None) -> None:
    path = _to_extended_path(path)
    if not path.exists() or not path.is_file():
        raise SourceIntegrityError("Source artifact is missing")
    actual_size = path.stat().st_size
    if expected_size is not None and actual_size != int(expected_size):
        raise SourceIntegrityError("Source artifact size does not match its metadata")
    if sha256_file(path) != str(expected_checksum).lower():
        raise SourceIntegrityError("Source artifact checksum does not match its metadata")


def read_verified_frame(path: Path, *, checksum: str, size_bytes: int, schema: list[dict[str, Any]] | None = None) -> Any:
    verify_file(path, checksum, size_bytes)
    frame = _read_frame(path.read_bytes(), path.name)
    actual_schema = canonical_schema_manifest(frame)
    if schema is not None and schema_contract_hash(actual_schema) != schema_contract_hash(schema):
        raise SourceIntegrityError("Source artifact schema does not match its immutable version manifest")
    return frame


def _row_ids(frame: Any) -> list[str]:

    for name in ("source_row_id", "id", "row_id"):
        if name in frame.columns:
            return [str(value) for value in frame[name].tolist()]
    return [str(index + 1) for index in range(len(frame))]


def _finite_float(value: Any) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def profile_frame(frame: Any, *, schema: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Return aggregate profile evidence and non-sensitive sample metadata."""
    import pandas as pd

    schema = schema or canonical_schema_manifest(frame)
    row_count = int(len(frame))
    columns: list[dict[str, Any]] = []
    total_null_cells = 0
    for item in schema:
        name = item["name"]
        series = frame[name]
        null_count = int(series.isna().sum())
        non_null = series.dropna()
        distinct = int(non_null.nunique(dropna=True))
        logical_type = item["logical_type"]
        record: dict[str, Any] = {
            **item,
            "null_count": null_count,
            "null_rate": null_count / row_count if row_count else 0.0,
            "non_null_count": int(len(non_null)),
            "distinct_count": distinct,
            "uniqueness_rate": distinct / len(non_null) if len(non_null) else 0.0,
            "is_unique_full_table": bool(row_count and null_count == 0 and distinct == row_count),
        }
        total_null_cells += null_count
        if logical_type in {"number", "integer"} and len(non_null):
            numeric = pd.to_numeric(non_null, errors="coerce").dropna()
            record.update({
                "min": _finite_float(numeric.min()) if len(numeric) else None,
                "max": _finite_float(numeric.max()) if len(numeric) else None,
                "negative_rate": float((numeric < 0).mean()) if len(numeric) else None,
            })
        # Samples are intentionally metadata-only.  Raw values remain in the
        # source artifact and are never persisted in the profile snapshot.
        record["sample_count"] = min(3, distinct)
        columns.append(record)

    completeness = 100.0 * (1 - total_null_cells / (row_count * len(schema))) if row_count and schema else 0.0
    duplicate_rate = float(frame.duplicated(keep="first").mean() * 100) if row_count else 0.0
    return {
        "row_count": row_count,
        "completeness_score": round(completeness, 2),
        "validity_score": None,
        "uniqueness_score": round(max(0.0, 100.0 - duplicate_rate), 2),
        "duplicate_rate": round(duplicate_rate, 2),
        "quality_score": round(completeness, 2) if row_count else None,
        "columns": columns,
        "schema_hash": schema_hash(schema),
        "generated_at": datetime.now(UTC).isoformat(),
        "sample_policy": "metadata_only",
    }


def validate_rule_spec(rule: dict[str, Any], allowed_columns: Iterable[str]) -> dict[str, Any]:
    """Validate a structured rule against the exact immutable schema."""
    if not isinstance(rule, dict):
        raise DatasetContractError("Rule must be an object")
    allowed = set(str(column) for column in allowed_columns)
    rule_type = str(rule.get("rule_type") or rule.get("type") or "").upper()
    aliases = {"NOT_NULL": "NOT_NULL", "UNIQUE": "UNIQUE", "NUMERIC_RANGE": "NUMERIC_RANGE",
               "RANGE": "NUMERIC_RANGE", "ACCEPTED_VALUES": "ACCEPTED_VALUES", "ROW_COUNT": "ROW_COUNT",
               "FRESHNESS": "FRESHNESS", "CROSS_FIELD_COMPARISON": "CROSS_FIELD_COMPARISON",
               "DUPLICATE_FINGERPRINT": "DUPLICATE_FINGERPRINT", "NULL_RATE": "NULL_RATE"}
    if rule_type not in aliases:
        raise DatasetContractError(f"Unsupported rule type: {rule_type}")
    canonical = aliases[rule_type]
    parameters = rule.get("effective_parameters") or rule.get("parameters") or rule
    if not isinstance(parameters, dict):
        raise DatasetContractError("Rule parameters must be an object")
    column = rule.get("column") or parameters.get("column")
    if canonical not in {"ROW_COUNT"}:
        if not isinstance(column, str) or column not in allowed:
            raise DatasetContractError(f"Rule references a column outside the immutable schema: {column}")
    if canonical in {"CROSS_FIELD_COMPARISON"}:
        target = parameters.get("target_column") or rule.get("target_column")
        cols = rule.get("columns") or parameters.get("columns")
        if isinstance(cols, list) and len(cols) == 2:
            left, target = cols[0], cols[1]
        else:
            left = column
        if not isinstance(target, str) or target not in allowed or left not in allowed:
            raise DatasetContractError("Cross-field rule references a column outside the immutable schema")
    if canonical == "DUPLICATE_FINGERPRINT":
        cols = rule.get("fingerprint_columns") or parameters.get("fingerprint_columns")
        if not isinstance(cols, list) or not cols or any(col not in allowed for col in cols):
            raise DatasetContractError("Duplicate fingerprint references a column outside the immutable schema")
    if canonical == "ACCEPTED_VALUES":
        values = rule.get("allowed_values") or parameters.get("allowed_values")
        if not isinstance(values, list) or not values:
            raise DatasetContractError("Accepted values requires a non-empty list")
    if canonical == "NUMERIC_RANGE" and parameters.get("min_value", parameters.get("min")) is None and parameters.get("max_value", parameters.get("max")) is None:
        raise DatasetContractError("Numeric range requires a minimum or maximum")
    if canonical == "CROSS_FIELD_COMPARISON" and (parameters.get("operator") not in {"<", "<=", ">", ">=", "==", "=", "!=", "<>"}):
        raise DatasetContractError("Unsupported cross-field comparison operator")
    return {**rule, "rule_type": canonical, "column": column}


def _comparison(left: Any, right: Any, operator: str) -> Any:
    if operator == "<":
        return left < right
    if operator == "<=":
        return left <= right
    if operator == ">":
        return left > right
    if operator == ">=":
        return left >= right
    if operator in {"=", "=="}:
        return left == right
    if operator in {"!=", "<>"}:
        return left != right
    raise DatasetContractError("Unsupported comparison operator")


def _parse_boolean(value: Any) -> bool | None:
    """Parse the small, explicit boolean vocabulary used by source files."""
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if normalized in {"true", "t", "yes", "y", "1"}:
        return True
    if normalized in {"false", "f", "no", "n", "0"}:
        return False
    return None


def _normalise_comparison_operands(left: Any, right: Any, schema: list[dict[str, Any]], params: dict[str, Any], operator: str) -> tuple[Any, Any, Any, Any, str]:
    """Return comparable operands plus masks for nulls and parse failures.

    The schema is the authority for numeric/timestamp columns.  When a source
    contains object/string columns, convertibility is used only to prevent
    accidental lexical ordering of numeric values.  Parse failures remain
    data violations with explicit execution-health metadata instead of
    escaping as a Python ``TypeError``.
    """
    import pandas as pd

    left_name, right_name = str(params.get("columns", [""])[0]), str(params.get("columns", ["", ""])[1])
    by_name = {str(item.get("name")): item for item in schema}
    left_type = str(by_name.get(left_name, {}).get("logical_type") or "string")
    right_type = str(by_name.get(right_name, {}).get("logical_type") or "string")
    requested_type = str(params.get("comparison_type") or params.get("value_type") or "").lower()

    datetime_string_hint = (
        left_type == right_type == "string"
        and (left.astype("string").str.match(r"^\s*\d{4}-\d{2}-\d{2}").any() or right.astype("string").str.match(r"^\s*\d{4}-\d{2}-\d{2}").any())
    )
    numeric_hint = requested_type in {"number", "numeric", "integer", "decimal"} or left_type in {"number", "integer"} or right_type in {"number", "integer"}
    datetime_hint = requested_type in {"date", "datetime", "timestamp", "timestamptz"} or left_type == "timestamp" or right_type == "timestamp" or datetime_string_hint

    if datetime_hint:
        left_values = pd.to_datetime(left, errors="coerce", utc=True)
        right_values = pd.to_datetime(right, errors="coerce", utc=True)
        comparison_kind = "datetime"
    elif numeric_hint or (operator not in {"=", "==", "!=", "<>"} and left_type == right_type == "string"):
        left_values = pd.to_numeric(left, errors="coerce")
        right_values = pd.to_numeric(right, errors="coerce")
        comparison_kind = "numeric"
    elif left_type == right_type == "boolean" or requested_type in {"bool", "boolean"}:
        left_values = left.map(_parse_boolean)
        right_values = right.map(_parse_boolean)
        comparison_kind = "boolean"
    else:
        # Equality is intentionally string-normalised so CSV/object values
        # compare consistently without introducing ordering semantics.
        left_values = left.map(lambda value: str(value).strip() if value is not None else value)
        right_values = right.map(lambda value: str(value).strip() if value is not None else value)
        comparison_kind = "string"

    null_mask = left.isna() | right.isna()
    parse_failure_mask = (~null_mask) & (left_values.isna() | right_values.isna())
    return left_values, right_values, null_mask, parse_failure_mask, comparison_kind


def execute_rule_frame(frame: Any, rule: dict[str, Any], *, failure_limit: int = FAILURE_SAMPLE_LIMIT) -> dict[str, Any]:
    """Execute one validated rule without SQL or shared transaction state."""
    import pandas as pd

    schema = canonical_schema_manifest(frame)
    normalized = validate_rule_spec(rule, [item["name"] for item in schema])
    rule_type = normalized["rule_type"]
    params = normalized.get("effective_parameters") or normalized.get("parameters") or normalized
    ids = _row_ids(frame)
    total_rows = int(len(frame))
    started = datetime.now(UTC)
    if total_rows == 0 and rule_type != "ROW_COUNT":
        return {"rule_id": normalized.get("rule_id", ""), "rule_type": rule_type, "status": "ERROR",
                "checked_count": 0, "failed_count": 0, "sample_failures": [], "error": "Source version has zero rows."}
    failed_mask = pd.Series(False, index=frame.index)
    error: str | None = None
    comparison_metadata: dict[str, Any] = {}
    column = normalized.get("column")
    if rule_type == "NOT_NULL":
        failed_mask = frame[column].isna()
    elif rule_type == "NULL_RATE":
        failed_mask = frame[column].isna()
        max_null_pct = float(params.get("max_null_pct", 5.0))
        if (float(failed_mask.sum()) / total_rows * 100) <= max_null_pct:
            failed_mask = pd.Series(False, index=frame.index)
    elif rule_type == "UNIQUE":
        failed_mask = frame[column].duplicated(keep="first")
    elif rule_type == "NUMERIC_RANGE":
        numeric = pd.to_numeric(frame[column], errors="coerce")
        failed_mask = numeric.isna()
        minimum = params.get("min_value", params.get("min"))
        maximum = params.get("max_value", params.get("max"))
        if minimum is not None:
            failed_mask = failed_mask | (numeric < float(minimum))
        if maximum is not None:
            failed_mask = failed_mask | (numeric > float(maximum))
    elif rule_type == "ACCEPTED_VALUES":
        values = normalized.get("allowed_values") or params.get("allowed_values") or params.get("accepted_values")
        failed_mask = frame[column].notna() & ~frame[column].astype(str).isin([str(value) for value in values])
    elif rule_type == "ROW_COUNT":
        minimum = int(params.get("min_row_count", normalized.get("min_row_count", 0)))
        failed_mask = pd.Series([total_rows < minimum] + [False] * max(0, total_rows - 1), index=frame.index)
    elif rule_type == "FRESHNESS":
        timestamps = pd.to_datetime(frame[column], errors="coerce", utc=True)
        newest = timestamps.max() if len(timestamps) else None
        if pd.isna(newest):
            error = "No readable timestamp was available for freshness evaluation."
        else:
            max_age_hours = float(params.get("max_age_hours", 24.0))
            if (datetime.now(UTC) - newest.to_pydatetime()).total_seconds() > max_age_hours * 3600:
                error = f"Latest value is older than the {max_age_hours:g} hour freshness limit."
    elif rule_type == "CROSS_FIELD_COMPARISON":
        cols = normalized.get("columns") or params.get("columns") or [column, params.get("target_column")]
        left, right = frame[cols[0]], frame[cols[1]]
        params = {**params, "columns": cols}
        operator = params.get("operator", "=")
        left_values, right_values, null_mask, parse_failure_mask, comparison_kind = _normalise_comparison_operands(
            left, right, schema, params, operator
        )
        comparable = ~(null_mask | parse_failure_mask)
        compared = pd.Series(False, index=frame.index, dtype=object)
        compared.loc[comparable] = [not _comparison(a, b, operator) for a, b in zip(left_values[comparable], right_values[comparable])]
        # An unparseable non-null value is an invalid comparison value, but it
        # is reported separately from ordinary rule violations and execution
        # errors so operators can distinguish source quality from runner health.
        failed_mask = compared | parse_failure_mask
        comparison_parse_failure_count = int(parse_failure_mask.sum())
        comparison_parse_failure_refs = [ids[index] for index, value in enumerate(parse_failure_mask.tolist()) if bool(value)][:failure_limit]
        comparison_metadata = {
            "comparison_kind": comparison_kind,
            "null_pair_count": int(null_mask.sum()),
            "parse_failure_count": comparison_parse_failure_count,
            "parse_failure_refs": comparison_parse_failure_refs,
            "execution_health": "DEGRADED" if comparison_parse_failure_count else "HEALTHY",
        }
    elif rule_type == "DUPLICATE_FINGERPRINT":
        cols = normalized.get("fingerprint_columns") or params.get("fingerprint_columns")
        failed_mask = frame.duplicated(subset=cols, keep="first")

    failed_indices = [position for position, value in enumerate(failed_mask.tolist()) if bool(value)]
    failed_count = len(failed_indices)
    if error:
        # A freshness parse/evaluation failure is an execution-health issue,
        # not a trusted data violation. Keep it distinct from FAIL so Graph 2
        # can aggregate it as PARTIAL/FAILED honestly.
        status = "ERROR"
        failed_count = 0
    else:
        status = "FAIL" if failed_count else "PASS"
    return {
        "rule_id": normalized.get("rule_id", ""),
        "table_name": "version_source",
        "column": column,
        "rule_type": rule_type,
        "status": status,
        "checked_count": total_rows,
        "failed_count": failed_count,
        "total_rows": total_rows,
        "violation_count": failed_count,
        "violation_rate": round(failed_count / total_rows, 6) if total_rows else 0.0,
        "sample_failures": [ids[index] for index in failed_indices[:failure_limit]],
        "sample_refs": [ids[index] for index in failed_indices[:failure_limit]],
        "violation_row_ids": [ids[index] for index in failed_indices[:EVIDENCE_ROW_ID_LIMIT]],
        "violation_row_ids_truncated": failed_count > EVIDENCE_ROW_ID_LIMIT,
        "duration_ms": round((datetime.now(UTC) - started).total_seconds() * 1000, 2),
        "error": error,
        "evidence_refs": normalized.get("evidence_refs", []),
        **(comparison_metadata if rule_type == "CROSS_FIELD_COMPARISON" else {}),
    }


def execute_rules_frame(frame: Any, rules: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Run every rule in an isolated boundary; one failure cannot poison the next."""
    results: list[dict[str, Any]] = []
    for rule in rules:
        try:
            results.append(execute_rule_frame(frame, rule))
        except Exception as exc:
            results.append({
                "rule_id": rule.get("rule_id", "") if isinstance(rule, dict) else "",
                "rule_type": str((rule or {}).get("rule_type") or (rule or {}).get("type") or "UNKNOWN") if isinstance(rule, dict) else "UNKNOWN",
                "table_name": "version_source", "status": "ERROR", "checked_count": 0,
                "failed_count": 0, "total_rows": 0, "violation_count": 0, "violation_rate": 0.0,
                "sample_failures": [], "sample_refs": [], "violation_row_ids": [],
                "violation_row_ids_truncated": False, "duration_ms": 0.0, "error": str(exc),
            })
    return results


def _local_storage_root() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "source_artifacts"


def _to_extended_path(path: Path) -> Path:
    if os.name == "nt":
        resolved = str(path.resolve())
        if not resolved.startswith(("\\\\?\\", "\\\\")):
            return Path(f"\\\\?\\{resolved}")
    return path


def store_source_artifact(content: bytes, inspected: InspectedUpload, *, workspace_id: str, dataset_id: str, dataset_version_id: str) -> SourceArtifactRef:
    """Store and verify a source artifact; production failures are fail-closed."""
    from src.services.dbt_artifact_store import get_dbt_artifact_store

    checksum = inspected.checksum
    key = safe_source_object_key(workspace_id, dataset_id, dataset_version_id, checksum, inspected.filename)
    settings = get_settings()
    if settings.app_env in {"local", "development", "test"}:
        raw_path = _local_storage_root() / key
        path = _to_extended_path(raw_path)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                verify_file(path, checksum, inspected.size_bytes)
                created_by_request = False
            else:
                path.write_bytes(content)
                verify_file(path, checksum, inspected.size_bytes)
                created_by_request = True
        except Exception as exc:
            raise SourceIntegrityError("Local source artifact could not be stored or verified") from exc
        return SourceArtifactRef(None, key, checksum, inspected.size_bytes, inspected.format, inspected.filename, f"local:{path}", created_by_request)
    try:
        ref = get_dbt_artifact_store().upload_source_file(key, content, checksum=checksum)
        if ref.sha256 != checksum or ref.size_bytes != inspected.size_bytes:
            raise SourceIntegrityError("Object-storage source metadata does not match the uploaded content")
        return SourceArtifactRef(ref.bucket, ref.object_key, checksum, inspected.size_bytes, inspected.format, inspected.filename, f"object://{ref.bucket}/{ref.object_key}", True, ref.version_id)
    except Exception as exc:
        raise SourceIntegrityError("Object-storage upload failed; version was not made executable") from exc


def store_source_artifact_path(path: Path, inspected: InspectedUpload, *, workspace_id: str, dataset_id: str, dataset_version_id: str) -> SourceArtifactRef:
    """Store a verified temporary file without loading it into memory."""
    from src.services.dbt_artifact_store import get_dbt_artifact_store
    key = safe_source_object_key(workspace_id, dataset_id, dataset_version_id, inspected.checksum, inspected.filename)
    settings = get_settings()
    if settings.app_env in {"local", "development", "test"}:
        target = _to_extended_path(_local_storage_root() / key)
        target.parent.mkdir(parents=True, exist_ok=True)
        existed = target.exists()
        if not existed:
            with path.open("rb") as source, target.open("wb") as destination:
                shutil.copyfileobj(source, destination, length=1024 * 1024)
        verify_file(target, inspected.checksum, inspected.size_bytes)
        return SourceArtifactRef(None, key, inspected.checksum, inspected.size_bytes, inspected.format, inspected.filename, f"local:{target}", not existed)
    try:
        ref = get_dbt_artifact_store().upload_source_path(key, path, checksum=inspected.checksum)
        if ref.sha256 != inspected.checksum or ref.size_bytes != inspected.size_bytes:
            raise SourceIntegrityError("Object-storage source metadata does not match the uploaded content")
        return SourceArtifactRef(ref.bucket, ref.object_key, inspected.checksum, inspected.size_bytes, inspected.format, inspected.filename, f"object://{ref.bucket}/{ref.object_key}", True, ref.version_id)
    except Exception as exc:
        raise SourceIntegrityError("Object-storage upload failed; version was not made executable") from exc


def delete_source_artifact(ref: SourceArtifactRef) -> None:
    """Compensate only an object created by the current import request."""
    if not ref.created_by_request:
        return
    if ref.storage_locator.startswith("local:"):
        path = Path(ref.storage_locator.removeprefix("local:"))
        path.unlink(missing_ok=True)
        return
    if ref.storage_locator.startswith("object://"):
        from src.services.dbt_artifact_store import DbtArtifactRef, get_dbt_artifact_store

        get_dbt_artifact_store().delete_artifact(DbtArtifactRef(
            bucket=ref.bucket or "",
            object_key=ref.object_key,
            sha256=ref.checksum,
            size_bytes=ref.size_bytes,
            version_id=ref.version_id,
        ))


def materialize_source_artifact(ref: SourceArtifactRef | dict[str, Any]) -> Path:
    """Materialize an artifact for a bounded execution/profile operation."""
    value = ref if isinstance(ref, SourceArtifactRef) else SourceArtifactRef(**ref)
    if value.storage_locator.startswith("local:"):
        path = Path(value.storage_locator.removeprefix("local:"))
        verify_file(path, value.checksum, value.size_bytes)
        return path
    if value.storage_locator.startswith("object://"):
        from src.services.dbt_artifact_store import get_dbt_artifact_store

        content = get_dbt_artifact_store().download_source_file({
            "bucket": value.bucket, "object_key": value.object_key,
            "sha256": value.checksum, "size_bytes": value.size_bytes,
            "version_id": value.version_id,
        })
        handle = tempfile.NamedTemporaryFile(prefix="ridepulse-source-", suffix=f".{value.format}", delete=False)
        handle.write(content)
        handle.close()
        path = Path(handle.name)
        verify_file(path, value.checksum, value.size_bytes)
        return path
    raise SourceIntegrityError("Unsupported source artifact locator")
