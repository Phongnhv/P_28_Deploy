import shutil
import subprocess
from pathlib import Path

import yaml


def _get_project_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def test_dbt_project_files_exist():
    """Kiểm tra sự tồn tại của tất cả các tệp cấu hình và models trong dbt project."""
    root = _get_project_root()
    dbt_dir = root / "dbt_project"

    assert dbt_dir.exists(), "Thư mục dbt_project không tồn tại."
    assert (dbt_dir / "dbt_project.yml").exists(), "Tệp dbt_project.yml không tồn tại."
    assert (dbt_dir / "profiles.yml").exists(), "Tệp profiles.yml không tồn tại."
    assert (dbt_dir / "models" / "staging" / "stg_trips.sql").exists(), "Staging model stg_trips.sql không tồn tại."
    assert (dbt_dir / "models" / "analytics" / "profile_input.sql").exists(), (
        "Analytics model profile_input.sql không tồn tại."
    )
    assert (dbt_dir / "models" / "schema.yml").exists(), "Tệp schema.yml không tồn tại."


def test_dbt_project_yml_validity():
    """Kiểm tra tính đúng đắn của tệp cấu hình dbt_project.yml."""
    root = _get_project_root()
    config_path = root / "dbt_project" / "dbt_project.yml"

    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    assert config.get("name") == "ridepulse_dbt"
    assert config.get("profile") == "ridepulse"
    assert "models" in config
    assert "ridepulse_dbt" in config["models"]


def test_dbt_models_sql_structure():
    """Kiểm tra nội dung các tệp dbt SQL models chứa đúng jinja references."""
    root = _get_project_root()
    stg_path = root / "dbt_project" / "models" / "staging" / "stg_trips.sql"
    profile_path = root / "dbt_project" / "models" / "analytics" / "profile_input.sql"

    with open(stg_path, encoding="utf-8") as f:
        stg_sql = f.read()
    assert "source('public', 'trips_canonical')" in stg_sql or 'source("public", "trips_canonical")' in stg_sql
    assert "source_row_id" in stg_sql

    with open(profile_path, encoding="utf-8") as f:
        profile_sql = f.read()
    assert "ref('stg_trips')" in profile_sql or 'ref("stg_trips")' in profile_sql
    assert "select *" in profile_sql.lower()


def test_dbt_schema_yml_tests():
    """Kiểm tra schema.yml chứa đầy đủ khai báo source và data contract tests."""
    root = _get_project_root()
    schema_path = root / "dbt_project" / "models" / "schema.yml"

    with open(schema_path, encoding="utf-8") as f:
        schema = yaml.safe_load(f)

    assert "sources" in schema
    assert schema["sources"][0]["name"] == "public"
    assert schema["sources"][0]["tables"][0]["name"] == "trips_canonical"

    models_map = {m["name"]: m for m in schema.get("models", [])}
    assert "stg_trips" in models_map
    assert "profile_input" in models_map


def test_dbt_parse_if_installed():
    """Nếu dbt CLI có sẵn trong môi trường, kiểm tra chạy dbt parse thành công."""
    dbt_cmd = shutil.which("dbt")
    if not dbt_cmd:
        return

    root = _get_project_root()
    dbt_dir = root / "dbt_project"

    result = subprocess.run(
        [dbt_cmd, "parse", "--project-dir", str(dbt_dir), "--profiles-dir", str(dbt_dir)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"dbt parse thất bại: {result.stderr}"
