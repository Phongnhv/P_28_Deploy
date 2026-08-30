"""Persistence and normalisation for Steward-supplied data dictionaries.

Graph 1A decides between a supplied dictionary and an LLM-inferred one by
looking for ``normalized_data_dictionary`` in its state. This module owns the
supplied side: it turns an uploaded CSV/JSON file into that exact shape and
stores it, so the graph can keep using the branch it already has.
"""

from __future__ import annotations

import csv
import io
import json
import uuid
from typing import Any

from sqlalchemy.orm import Session

from src.models.data_dictionary import InferredDictionaryColumn, InferredDictionaryTable
from src.models.database import DatasetDataDictionaryModel


class DataDictionaryError(ValueError):
    """The uploaded file cannot be read as a data dictionary."""


# A dictionary export rarely uses our field names. Accept the spellings that
# actually show up in dbt docs, Excel exports and hand-written sheets.
_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "name": ("name", "column", "column_name", "field", "field_name", "ten_cot", "cot"),
    "description": ("description", "desc", "definition", "meaning", "comment", "mo_ta", "y_nghia"),
    "semantic_type": ("semantic_type", "type", "data_type", "dtype", "logical_type", "kieu_du_lieu"),
    "business_role": ("business_role", "role", "business_meaning", "vai_tro"),
    "nullable_expected": ("nullable_expected", "nullable", "is_nullable", "allow_null", "cho_phep_null"),
    "governance_notes": ("governance_notes", "notes", "note", "governance", "ghi_chu"),
}

_TRUTHY = {"1", "true", "yes", "y", "t", "co", "có"}
_FALSEY = {"0", "false", "no", "n", "f", "khong", "không"}


def _normalise_header(header: str) -> str:
    return header.strip().lower().replace(" ", "_").replace("-", "_")


def _build_header_map(headers: list[str]) -> dict[str, str]:
    """Map each canonical field to the actual header that supplies it."""
    seen = {_normalise_header(h): h for h in headers if h and h.strip()}
    resolved: dict[str, str] = {}
    for field, aliases in _COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in seen:
                resolved[field] = seen[alias]
                break
    return resolved


def _parse_bool(value: Any, default: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in _TRUTHY:
        return True
    if text in _FALSEY:
        return False
    return default


def _parse_notes(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value or "").strip()
    if not text:
        return []
    # Sheets carry several notes in one cell; split on the usual separators.
    parts = [part.strip() for part in text.replace("|", ";").replace("\n", ";").split(";")]
    return [part for part in parts if part]


def _column_from_mapping(raw: dict[str, Any], header_map: dict[str, str] | None = None) -> InferredDictionaryColumn | None:
    def pick(field: str) -> Any:
        if header_map and field in header_map:
            return raw.get(header_map[field])
        for alias in _COLUMN_ALIASES[field]:
            for key, value in raw.items():
                if _normalise_header(str(key)) == alias:
                    return value
        return None

    name = str(pick("name") or "").strip()
    if not name:
        return None
    return InferredDictionaryColumn(
        name=name,
        description=str(pick("description") or "").strip(),
        semantic_type=str(pick("semantic_type") or "unknown").strip() or "unknown",
        business_role=str(pick("business_role") or "unknown").strip() or "unknown",
        nullable_expected=_parse_bool(pick("nullable_expected")),
        governance_notes=_parse_notes(pick("governance_notes")),
    )


def _parse_csv(payload: bytes, table_name: str) -> InferredDictionaryTable:
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = payload.decode("latin-1")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise DataDictionaryError("The dictionary file has no header row.")
    header_map = _build_header_map(list(reader.fieldnames))
    if "name" not in header_map:
        raise DataDictionaryError(
            "The dictionary needs a column-name field. Accepted headers: "
            + ", ".join(_COLUMN_ALIASES["name"])
        )
    columns = [col for row in reader if (col := _column_from_mapping(row, header_map))]
    if not columns:
        raise DataDictionaryError("The dictionary file contains no column rows.")
    return InferredDictionaryTable(table_name=table_name, description="", columns=columns)


def _parse_json(payload: bytes, table_name: str) -> InferredDictionaryTable:
    try:
        document = json.loads(payload.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DataDictionaryError(f"The dictionary file is not valid JSON: {exc}") from exc

    # Accept the shape we emit ({"tables": [...]}), a bare table, a list of
    # columns, or a {column: description} object.
    if isinstance(document, dict) and isinstance(document.get("tables"), list) and document["tables"]:
        document = document["tables"][0]
    if isinstance(document, dict) and isinstance(document.get("columns"), list):
        raw_columns: list[Any] = document["columns"]
        description = str(document.get("description") or "").strip()
        table_name = str(document.get("table_name") or table_name)
        business_rules = _parse_notes(document.get("business_rules"))
    elif isinstance(document, list):
        raw_columns, description, business_rules = document, "", []
    elif isinstance(document, dict):
        raw_columns = [{"name": key, "description": value} for key, value in document.items()]
        description, business_rules = "", []
    else:
        raise DataDictionaryError("The dictionary JSON must be an object or a list of columns.")

    columns: list[InferredDictionaryColumn] = []
    for entry in raw_columns:
        if isinstance(entry, dict):
            column = _column_from_mapping(entry)
        elif isinstance(entry, str):
            column = InferredDictionaryColumn(name=entry.strip())
        else:
            column = None
        if column:
            columns.append(column)
    if not columns:
        raise DataDictionaryError("The dictionary file contains no column entries.")
    return InferredDictionaryTable(
        table_name=table_name,
        description=description,
        columns=columns,
        business_rules=business_rules,
    )


def parse_data_dictionary(payload: bytes, filename: str, table_name: str) -> dict[str, Any]:
    """Return the ``{"tables": [...]}`` payload Graph 1A consumes."""
    if not payload:
        raise DataDictionaryError("The uploaded dictionary file is empty.")
    suffix = (filename or "").rsplit(".", 1)[-1].lower()
    if suffix == "json":
        table = _parse_json(payload, table_name)
    elif suffix in {"csv", "tsv", "txt"}:
        table = _parse_csv(payload, table_name)
    else:
        # Sniff rather than reject: exports often arrive without a useful suffix.
        stripped = payload.lstrip()[:1]
        table = _parse_json(payload, table_name) if stripped in (b"{", b"[") else _parse_csv(payload, table_name)
    return {"tables": [table.model_dump()]}


def get_data_dictionary(db: Session, dataset_id: str) -> DatasetDataDictionaryModel | None:
    return (
        db.query(DatasetDataDictionaryModel)
        .filter(DatasetDataDictionaryModel.dataset_id == dataset_id)
        .order_by(DatasetDataDictionaryModel.updated_at.desc())
        .first()
    )


def save_data_dictionary(
    db: Session,
    *,
    dataset_id: str,
    dataset_version_id: str | None,
    payload: dict[str, Any],
    source_filename: str | None,
    uploaded_by: str | None,
) -> DatasetDataDictionaryModel:
    """Store the supplied dictionary, replacing any earlier upload."""
    tables = payload.get("tables") or []
    column_count = sum(len(table.get("columns") or []) for table in tables if isinstance(table, dict))
    record = get_data_dictionary(db, dataset_id)
    if record:
        record.dataset_version_id = dataset_version_id
        record.source = "UPLOADED"
        record.source_filename = source_filename
        record.column_count = column_count
        record.payload_json = json.dumps(payload, ensure_ascii=False)
        record.uploaded_by = uploaded_by
    else:
        record = DatasetDataDictionaryModel(
            id=f"ddict-{uuid.uuid4().hex[:20]}",
            dataset_id=dataset_id,
            dataset_version_id=dataset_version_id,
            source="UPLOADED",
            source_filename=source_filename,
            column_count=column_count,
            payload_json=json.dumps(payload, ensure_ascii=False),
            uploaded_by=uploaded_by,
        )
        db.add(record)
    db.commit()
    db.refresh(record)
    return record


def delete_data_dictionary(db: Session, dataset_id: str) -> bool:
    """Drop the upload so Graph 1A falls back to inferring the dictionary."""
    record = get_data_dictionary(db, dataset_id)
    if not record:
        return False
    db.delete(record)
    db.commit()
    return True


def load_supplied_dictionary_payload(db: Session, dataset_id: str) -> dict[str, Any] | None:
    """Return the stored payload for seeding Graph 1A state, if one exists."""
    record = get_data_dictionary(db, dataset_id)
    if not record:
        return None
    try:
        payload = json.loads(record.payload_json or "{}")
    except (TypeError, ValueError):
        return None
    return payload if payload.get("tables") else None


def serialize_data_dictionary(record: DatasetDataDictionaryModel) -> dict[str, Any]:
    try:
        payload = json.loads(record.payload_json or "{}")
    except (TypeError, ValueError):
        payload = {}
    return {
        "id": record.id,
        "dataset_id": record.dataset_id,
        "dataset_version_id": record.dataset_version_id,
        "source": record.source,
        "source_filename": record.source_filename,
        "column_count": record.column_count,
        "tables": payload.get("tables") or [],
        "updated_at": record.updated_at.isoformat() if record.updated_at else None,
    }
