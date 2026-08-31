from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, field_validator


class SemanticType(StrEnum):
    """The closed vocabulary of column meanings.

    Pinned because downstream behaviour branches on the exact string.
    ``rule_candidate_builder_node`` clamps a lower bound to zero only when the type
    is ``currency``; ``fare_amount`` classified as ``numeric`` therefore loses the
    non-negative invariant, and the resulting RANGE rule admits every negative fare
    it was meant to catch. A free-form string made that a silent one-word failure.

    Ground truth binds to these values too: a golden case selecting
    ``semantic_type: currency`` resolves to nothing when the model answers
    ``money``, and the case is then skipped rather than failed -- coverage quietly
    disappears instead of a defect being reported.
    """

    IDENTIFIER = "identifier"
    TIMESTAMP = "timestamp"
    CATEGORY = "category"
    CURRENCY = "currency"
    NUMERIC = "numeric"
    TEXT = "text"
    LOCATION = "location"
    BOOLEAN = "boolean"
    #: Upper case on purpose: rule_candidate_builder_node compares against "PII".
    PII = "PII"
    #: Explicit escape hatch. A value outside the vocabulary is recorded rather than
    #: raised: rejecting it would fail the whole table's contract over one column,
    #: and the heuristic fallback would then replace a mostly-correct reading with a
    #: name-based guess. Unknown is visible, countable, and safely inert -- no rule
    #: template fires on it.
    UNKNOWN = "unknown"


#: Words models reach for that mean one of the canonical types. Normalising them is
#: not leniency: the alternative is an interpretation that is right in substance and
#: unusable in effect, because every consumer matches on the literal.
_SYNONYMS: dict[str, SemanticType] = {
    "money": SemanticType.CURRENCY, "amount": SemanticType.CURRENCY,
    "price": SemanticType.CURRENCY, "monetary": SemanticType.CURRENCY,
    "cost": SemanticType.CURRENCY, "fare": SemanticType.CURRENCY,
    "id": SemanticType.IDENTIFIER, "uuid": SemanticType.IDENTIFIER,
    "key": SemanticType.IDENTIFIER, "primary_key": SemanticType.IDENTIFIER,
    "datetime": SemanticType.TIMESTAMP, "date": SemanticType.TIMESTAMP,
    "time": SemanticType.TIMESTAMP,
    "enum": SemanticType.CATEGORY, "categorical": SemanticType.CATEGORY,
    "code": SemanticType.CATEGORY,
    "string": SemanticType.TEXT, "freetext": SemanticType.TEXT,
    "free_text": SemanticType.TEXT, "description": SemanticType.TEXT,
    "number": SemanticType.NUMERIC, "float": SemanticType.NUMERIC,
    "int": SemanticType.NUMERIC, "integer": SemanticType.NUMERIC,
    "decimal": SemanticType.NUMERIC, "measure": SemanticType.NUMERIC,
    "geo": SemanticType.LOCATION, "coordinate": SemanticType.LOCATION,
    "geolocation": SemanticType.LOCATION,
    "bool": SemanticType.BOOLEAN,
    "personal": SemanticType.PII, "sensitive": SemanticType.PII,
    "personal_data": SemanticType.PII,
}


def normalize_semantic_type(value: object) -> SemanticType:
    """Map any answer onto the closed vocabulary, defaulting to UNKNOWN."""
    text = str(value or "").strip()
    if not text:
        return SemanticType.UNKNOWN
    if text == SemanticType.PII.value:
        return SemanticType.PII
    lowered = text.lower()
    for member in SemanticType:
        if lowered == member.value.lower():
            return member
    return _SYNONYMS.get(lowered, SemanticType.UNKNOWN)


class SemanticColumn(BaseModel):
    name: str = Field(..., description="Tên cột vật lý trong database.")
    semantic_type: SemanticType = Field(
        ...,
        description="Kiểu dữ liệu ngữ nghĩa thuộc từ vựng đóng SemanticType.",
    )

    @field_validator("semantic_type", mode="before")
    @classmethod
    def _normalize(cls, value: object) -> SemanticType:
        return normalize_semantic_type(value)
    business_role: str = Field(
        ..., description="Vai trò nghiệp vụ tương ứng (ví dụ: primary_key, transaction_amount, created_at, v.v.)."
    )
    nullable_expected: bool = Field(
        default=True, description="Liệu cột này có được phép mang giá trị trống/null theo logic nghiệp vụ không."
    )
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="Độ tin cậy của dự đoán phân tích cột.")
    description: str | None = Field(None, description="Mô tả nghiệp vụ ngắn gọn bằng tiếng Việt của cột.")


class SemanticRelationship(BaseModel):
    left_column: str = Field(..., description="Cột vế trái.")
    operator: str = Field(..., description="Toán tử so sánh (<=, <, =, >, >=, !=).")
    right_column: str = Field(..., description="Cột vế phải.")
    description: str | None = Field(None, description="Mô tả mối quan hệ nghiệp vụ bằng tiếng Việt.")


class TableSemanticContract(BaseModel):
    table_name: str = Field(..., description="Tên bảng nghiệp vụ.")
    domain: str = Field(..., description="Lĩnh vực nghiệp vụ dự đoán của bảng (ví dụ: e-commerce, IoT, healthcare).")
    table_purpose: str = Field(..., description="Mục đích nghiệp vụ của bảng (bằng tiếng Việt).")
    columns: list[SemanticColumn] = Field(default_factory=list)
    relationships: list[SemanticRelationship] = Field(default_factory=list)
    business_assumptions: list[str] = Field(
        default_factory=list, description="Các giả định nghiệp vụ được rút ra từ bảng."
    )


class DatasetSemanticContract(BaseModel):
    dataset_id: str = Field(..., description="ID của dataset.")
    tables: dict[str, TableSemanticContract] = Field(
        default_factory=dict, description="Bản đồ các bảng và contract ngữ nghĩa tương ứng."
    )
