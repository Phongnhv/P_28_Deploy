import csv
import hashlib
import json
from pathlib import Path
from typing import Any

MANIFEST_ALLOW_LIST: dict[str, str] = {
    "nyc-yellow-demo-v1": "src/resources/manifest.json",
    "nyc-yellow-50k-v1": "data/yellow_tripdata_2025/semantic_data/manifest.json"
}


def _get_project_root() -> Path:
    """Trả về đường dẫn tuyệt đối của gốc dự án."""
    return Path(__file__).resolve().parent.parent.parent


def load_manifest(manifest_name: str) -> dict[str, Any]:
    """Tải và validate tệp manifest metadata theo allow-list.

    Chặn các lỗ hổng bảo mật như Path Traversal (chứa '..', '/', '\\').

    Args:
        manifest_name: Tên đăng ký của manifest.

    Returns:
        Dict chứa metadata của manifest.

    Raises:
        ValueError: Nếu manifest_name không nằm trong allow-list hoặc chứa ký tự độc hại.
        FileNotFoundError: Nếu tệp manifest không tồn tại trên đĩa.
    """
    if not manifest_name or not isinstance(manifest_name, str):
        raise ValueError("manifest_name không được để trống và phải là chuỗi.")

    # Chặn Path Traversal
    if ".." in manifest_name or "/" in manifest_name or "\\" in manifest_name:
        raise ValueError(f"Biểu thức manifest_name '{manifest_name}' không hợp lệ (Phát hiện ký tự Path Traversal).")

    if manifest_name not in MANIFEST_ALLOW_LIST:
        raise ValueError(f"Manifest '{manifest_name}' không nằm trong danh sách allow-list được phép truy cập.")

    relative_path = MANIFEST_ALLOW_LIST[manifest_name]
    manifest_path = _get_project_root() / relative_path

    if not manifest_path.exists():
        raise FileNotFoundError(f"Tệp manifest tại đường dẫn '{manifest_path}' không tồn tại.")

    with open(manifest_path, encoding="utf-8") as f:
        manifest_data: dict[str, Any] = json.load(f)

    return manifest_data


def verify_checksum(csv_path: str, expected_sha256: str) -> bool:
    """Tính toán mã hash SHA-256 của tệp CSV thực tế và đối chiếu với mã mong đợi.

    Args:
        csv_path: Đường dẫn tệp CSV (tương đối hoặc tuyệt đối).
        expected_sha256: Mã hash SHA-256 hex string mong đợi.

    Returns:
        True nếu checksum khớp.

    Raises:
        FileNotFoundError: Nếu tệp CSV không tồn tại.
        ValueError: Nếu checksum không khớp với mã hash mong đợi.
    """
    path = Path(csv_path)
    if not path.is_absolute():
        path = _get_project_root() / csv_path

    if not path.exists():
        raise FileNotFoundError(f"Tệp CSV tại '{path}' không tồn tại.")

    sha256 = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha256.update(chunk)

    calculated_hash = sha256.hexdigest().lower()
    expected_hash = expected_sha256.strip().lower()

    if calculated_hash != expected_hash:
        raise ValueError(
            f"Checksum SHA-256 không khớp cho tệp '{csv_path}'. "
            f"Tính toán: '{calculated_hash}', Mong đợi: '{expected_hash}'."
        )

    return True


def load_dataset_rows(manifest_name: str) -> list[dict[str, Any]]:
    """Tải tất cả các bản ghi dữ liệu từ dataset tương ứng với manifest_name.

    Quy trình: Validate manifest -> Kiểm tra Checksum SHA-256 -> Đọc dữ liệu CSV bằng DictReader.

    Args:
        manifest_name: Tên manifest đã đăng ký.

    Returns:
        Danh sách các dictionary biểu diễn từng dòng dữ liệu.

    Raises:
        ValueError: Nếu manifest không hợp lệ hoặc checksum không khớp.
    """
    manifest = load_manifest(manifest_name)
    local_path = manifest.get("local_path", "src/resources/nyc_yellow_demo.csv")
    expected_sha256 = manifest.get("file_sha256", "")

    # Kiểm tra toàn vẹn dữ liệu
    verify_checksum(local_path, expected_sha256)

    full_csv_path = _get_project_root() / local_path
    rows: list[dict[str, Any]] = []

    with open(full_csv_path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(dict(row))

    return rows

