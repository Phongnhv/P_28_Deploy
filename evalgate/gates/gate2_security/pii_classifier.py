"""Classify which columns of an arbitrary dataset hold personal data.

Once users upload their own data, the system must know what is sensitive *before*
anything is sent to a third-party model.  The classifier is a heuristic -- name
tokens plus value regexes -- and deliberately fails closed on free text, because
a false negative here means real personal data leaves the boundary while a false
positive only costs a redaction.

This is an engineering control, not a legal guarantee.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Iterable

import pandas as pd


class PIIClass(StrEnum):
    EMAIL = "EMAIL"
    PHONE = "PHONE"
    NATIONAL_ID = "NATIONAL_ID"
    CREDIT_CARD = "CREDIT_CARD"
    IP = "IP"
    NAME = "NAME"
    ADDRESS = "ADDRESS"
    DOB = "DOB"
    GEO_PRECISE = "GEO_PRECISE"
    PLATE = "PLATE"
    FREE_TEXT = "FREE_TEXT"


_VALUE_PATTERNS: dict[PIIClass, re.Pattern[str]] = {
    PIIClass.EMAIL: re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$"),
    PIIClass.IP: re.compile(r"^\d{1,3}(\.\d{1,3}){3}$"),
    PIIClass.CREDIT_CARD: re.compile(r"^\d{13,19}$"),
    PIIClass.PHONE: re.compile(r"^\+?\d[\d\s.\-()]{7,15}$"),
    PIIClass.NATIONAL_ID: re.compile(r"^\d{9,12}$"),
}

_NAME_TOKENS: dict[PIIClass, tuple[str, ...]] = {
    PIIClass.EMAIL: ("email", "mail"),
    PIIClass.PHONE: ("phone", "mobile", "tel", "sdt"),
    PIIClass.NATIONAL_ID: ("national_id", "cccd", "cmnd", "ssn", "passport", "id_number"),
    PIIClass.CREDIT_CARD: ("card_number", "credit_card", "pan"),
    PIIClass.IP: ("ip_address", "ip_addr", "client_ip"),
    PIIClass.NAME: ("full_name", "patient_name", "customer_name", "ho_ten", "_name"),
    PIIClass.ADDRESS: ("address", "street", "dia_chi"),
    PIIClass.DOB: ("date_of_birth", "dob", "birth", "ngay_sinh"),
    PIIClass.GEO_PRECISE: ("latitude", "longitude", "lat", "lon", "geo_point"),
    PIIClass.PLATE: ("license_plate", "plate", "bien_so", "vin"),
}

_FREE_TEXT_MIN_AVG_LENGTH = 40


@dataclass(frozen=True)
class ColumnClassification:
    column: str
    pii_class: PIIClass | None
    confidence: float
    reason: str

    @property
    def is_pii(self) -> bool:
        return self.pii_class is not None


def classify_column(name: str, samples: Iterable[Any]) -> ColumnClassification:
    lowered = str(name).lower()

    for pii_class, tokens in _NAME_TOKENS.items():
        if any(token in lowered for token in tokens):
            return ColumnClassification(
                name, pii_class, 0.9, f"column name matches token for {pii_class.value}"
            )

    values = [str(v) for v in samples if v is not None and str(v).strip()][:200]
    if not values:
        return ColumnClassification(name, None, 1.0, "no sampled values")

    for pii_class, pattern in _VALUE_PATTERNS.items():
        hits = sum(1 for v in values if pattern.match(v))
        if hits / len(values) >= 0.8:
            return ColumnClassification(
                name, pii_class, hits / len(values),
                f"{hits}/{len(values)} sampled values match the {pii_class.value} pattern",
            )

    average_length = sum(len(v) for v in values) / len(values)
    distinct_ratio = len(set(values)) / len(values)
    if average_length >= _FREE_TEXT_MIN_AVG_LENGTH and distinct_ratio > 0.8:
        # Fail closed: unbounded free text can carry anything.
        return ColumnClassification(
            name, PIIClass.FREE_TEXT, 0.5,
            f"high-entropy free text (avg len {average_length:.0f}); treated as sensitive by default",
        )

    return ColumnClassification(name, None, 0.8, "no PII signal detected")


def classify_dataframe(df: pd.DataFrame, *, sample_rows: int = 200) -> dict[str, ColumnClassification]:
    head = df.head(sample_rows)
    return {
        str(column): classify_column(column, head[column].tolist())
        for column in df.columns
    }


def pii_columns(df: pd.DataFrame) -> set[str]:
    return {
        name
        for name, classification in classify_dataframe(df).items()
        if classification.is_pii
    }


def assert_no_pii_in_payload(
    payload: str, classifications: dict[str, ColumnClassification]
) -> list[str]:
    """Return the PII column names that appear verbatim in an outbound payload."""
    return sorted(
        name
        for name, classification in classifications.items()
        if classification.is_pii and name in payload
    )
