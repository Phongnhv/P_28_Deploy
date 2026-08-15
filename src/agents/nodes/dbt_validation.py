from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import yaml

from src.config import get_settings
from src.services.dbt_artifact_store import get_dbt_artifact_store


def validate_dbt_yaml_structure(content: str | bytes) -> dict:
    parsed = yaml.safe_load(content)
    if not isinstance(parsed, dict):
        raise ValueError("dbt YAML root must be a mapping")
    if set(parsed) - {"version", "models"}:
        raise ValueError("dbt YAML root may contain only 'version' and 'models'")
    if parsed.get("version") != 2:
        raise ValueError("dbt YAML version must be 2")
    models = parsed.get("models")
    if not isinstance(models, list) or not models:
        raise ValueError("dbt YAML must contain a non-empty models list")
    for model in models:
        if not isinstance(model, dict) or not isinstance(model.get("name"), str):
            raise ValueError("each dbt model must be a mapping with a string name")
        if set(model) - {"name", "columns", "tests", "description"}:
            raise ValueError(f"unsupported keys in model {model.get('name')!r}")
        columns = model.get("columns", [])
        if not isinstance(columns, list):
            raise ValueError(f"columns for model {model['name']!r} must be a list")
        for column in columns:
            if not isinstance(column, dict) or not isinstance(column.get("name"), str):
                raise ValueError("each dbt column must be a mapping with a string name")
            if set(column) - {"name", "tests", "data_tests", "description"}:
                raise ValueError(f"unsupported keys in column {column.get('name')!r}")
            tests = column.get("data_tests", column.get("tests", []))
            if not isinstance(tests, list):
                raise ValueError(f"tests for column {column['name']!r} must be a list")
            if any(not isinstance(test, (str, dict)) for test in tests):
                raise ValueError(f"invalid test declaration for column {column['name']!r}")
    return parsed


def get_state_dbt_yaml(state: dict) -> str:
    content = state.get("generated_dbt_yaml")
    if content:
        return str(content)
    artifact_ref = state.get("dbt_artifact_ref")
    if artifact_ref:
        return get_dbt_artifact_store().download_yaml(artifact_ref).decode("utf-8")
    trace_path = Path(state.get("dbt_trace_file_path") or "")
    if trace_path.is_file():
        return trace_path.read_text(encoding="utf-8")
    raise ValueError("No generated dbt YAML is available")


def materialize_dbt_project(template_dir: Path, workspace: Path, content: str) -> Path:
    dbt_dir = workspace / "dbt_project"
    shutil.copytree(
        template_dir,
        dbt_dir,
        ignore=shutil.ignore_patterns("target", "logs", "dbt_packages", "dbt_modules", "generated_dq_tests.yml"),
    )
    generated_file = dbt_dir / "models" / "generated_dq_tests.yml"
    generated_file.parent.mkdir(parents=True, exist_ok=True)
    generated_file.write_text(content, encoding="utf-8")
    return dbt_dir


def run_dbt_parse(dbt_dir: Path) -> tuple[bool, str, int | None]:
    dbt_cmd = shutil.which("dbt")
    settings = get_settings()
    if not dbt_cmd:
        if settings.app_env in ("local", "development", "test"):
            return True, "dbt executable unavailable; structural validation used", None
        return False, "dbt executable is required in production", None
    try:
        result = subprocess.run(
            [dbt_cmd, "parse", "--project-dir", str(dbt_dir), "--profiles-dir", str(dbt_dir)],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except Exception as exc:
        return False, f"dbt parse could not run: {exc}", None
    output = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part.strip())
    return result.returncode == 0, output, result.returncode
