import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.models.database import Base, DatasetModel
from src.services.data_dictionary_store import (
    DataDictionaryError,
    delete_data_dictionary,
    get_data_dictionary,
    load_supplied_dictionary_payload,
    parse_data_dictionary,
    save_data_dictionary,
    serialize_data_dictionary,
)


def session_with_dataset(dataset_id: str = "ds-1") -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = Session(engine)
    db.add(
        DatasetModel(
            id=dataset_id,
            name="Orders",
            description="",
            status="REGISTERED",
            row_count=10,
            source_label="orders.csv",
            manifest_version="versioned-v1",
            checksum="abc",
        )
    )
    db.commit()
    return db


def test_csv_dictionary_accepts_the_header_spellings_exports_actually_use():
    # Encoded rather than a bytes literal: the notes column carries Vietnamese,
    # and the parser has to survive a real UTF-8 export.
    payload = "Column Name,Description,Data Type,Nullable,Notes\norder_id,Ma don hang,identifier,false,PII;Cần mã hóa\n".encode("utf-8")
    document = parse_data_dictionary(payload, "dict.csv", "orders")

    column = document["tables"][0]["columns"][0]
    assert column["name"] == "order_id"
    assert column["description"] == "Ma don hang"
    assert column["semantic_type"] == "identifier"
    assert column["nullable_expected"] is False
    # One cell carrying several notes is split, not stored as a single string.
    assert column["governance_notes"] == ["PII", "Cần mã hóa"]


def test_csv_without_a_column_name_field_is_rejected_rather_than_stored_empty():
    with pytest.raises(DataDictionaryError):
        parse_data_dictionary(b"foo,bar\n1,2\n", "dict.csv", "orders")


def test_empty_upload_is_rejected():
    with pytest.raises(DataDictionaryError):
        parse_data_dictionary(b"", "dict.csv", "orders")


@pytest.mark.parametrize(
    "payload",
    [
        b'{"columns":[{"name":"total","description":"Tong tien"}]}',
        b'{"tables":[{"table_name":"orders","columns":[{"name":"total","description":"Tong tien"}]}]}',
        b'[{"name":"total","description":"Tong tien"}]',
        b'{"total":"Tong tien"}',
    ],
)
def test_json_dictionary_accepts_each_shape_a_user_might_paste(payload: bytes):
    document = parse_data_dictionary(payload, "dict.json", "orders")

    column = document["tables"][0]["columns"][0]
    assert column["name"] == "total"
    assert column["description"] == "Tong tien"


def test_extensionless_upload_is_sniffed_instead_of_refused():
    document = parse_data_dictionary(b'  [{"name":"total"}]', "dictionary", "orders")
    assert document["tables"][0]["columns"][0]["name"] == "total"


def test_reupload_replaces_the_previous_dictionary_rather_than_accumulating():
    db = session_with_dataset()
    first = parse_data_dictionary(b"name,description\na,first\n", "a.csv", "orders")
    save_data_dictionary(db, dataset_id="ds-1", dataset_version_id=None, payload=first, source_filename="a.csv", uploaded_by="steward")
    second = parse_data_dictionary(b"name,description\nb,second\nc,third\n", "b.csv", "orders")
    record = save_data_dictionary(db, dataset_id="ds-1", dataset_version_id=None, payload=second, source_filename="b.csv", uploaded_by="steward")

    assert record.column_count == 2
    assert record.source_filename == "b.csv"
    assert serialize_data_dictionary(record)["tables"][0]["columns"][0]["name"] == "b"


def test_deleting_the_upload_hands_the_work_back_to_the_agent():
    db = session_with_dataset()
    document = parse_data_dictionary(b"name,description\na,first\n", "a.csv", "orders")
    save_data_dictionary(db, dataset_id="ds-1", dataset_version_id=None, payload=document, source_filename="a.csv", uploaded_by="steward")

    # Graph 1A reads this payload to decide whether to skip its generator node.
    assert load_supplied_dictionary_payload(db, "ds-1") is not None
    assert delete_data_dictionary(db, "ds-1") is True
    assert load_supplied_dictionary_payload(db, "ds-1") is None
    assert get_data_dictionary(db, "ds-1") is None
    assert delete_data_dictionary(db, "ds-1") is False
