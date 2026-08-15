from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.nodes.dbt_validation import validate_dbt_yaml_structure
from src.agents.nodes.llm_dbt_repair_node import llm_dbt_repair_node
from src.agents.nodes.validate_dbt_project_node import validate_dbt_project_node


VALID_YAML = """version: 2
models:
  - name: trips
    columns:
      - name: trip_id
        tests:
          - not_null
"""


def test_validate_dbt_yaml_structure_accepts_schema_yaml():
    parsed = validate_dbt_yaml_structure(VALID_YAML)
    assert parsed["models"][0]["name"] == "trips"


@pytest.mark.parametrize(
    "content, message",
    [
        ("version: 2\nmodels:\n  - name: trips\n   columns: []\n", "mapping"),
        ("version: 2\nmodels: invalid\n", "models list"),
        ("version: 2\nmodels: []\nhooks: []\n", "only 'version' and 'models'"),
    ],
)
def test_validate_dbt_yaml_structure_rejects_invalid_content(content, message):
    with pytest.raises(Exception, match=message):
        validate_dbt_yaml_structure(content)


@pytest.mark.asyncio
async def test_validate_dbt_project_captures_dbt_parse_error(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "src.agents.nodes.validate_dbt_project_node.run_dbt_parse",
        lambda _dbt_dir: (False, "undefined macro dbt_utils.expression_is_true", 2),
    )
    monkeypatch.setattr(
        "src.agents.nodes.validate_dbt_project_node.get_settings",
        lambda: MagicMock(output_dir=str(tmp_path), app_env="test"),
    )
    result = await validate_dbt_project_node(
        {"test_run_id": "parse-error", "generated_dbt_yaml": VALID_YAML}
    )
    assert result["dbt_validation_valid"] is False
    assert "undefined macro" in result["dbt_validation_error"]
    assert result["dbt_validation_attempts"] == 0


@pytest.mark.asyncio
async def test_llm_dbt_repair_updates_yaml_and_attempt_history(monkeypatch):
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=MagicMock(content=f"```yaml\n{VALID_YAML}```"))
    monkeypatch.setattr("src.agents.nodes.llm_dbt_repair_node.get_llm", lambda *args, **kwargs: llm)
    result = await llm_dbt_repair_node(
        {
            "generated_dbt_yaml": "version: 2\nmodels: invalid\n",
            "dbt_validation_valid": False,
            "dbt_validation_error": "models must be a list",
            "dbt_validation_attempts": 0,
            "approved_rules": [{"table_name": "trips", "column": "trip_id", "rule_type": "NOT_NULL"}],
        }
    )
    assert result["generated_dbt_yaml"] == VALID_YAML
    assert result["dbt_validation_attempts"] == 1
    assert len(result["dbt_repair_history"]) == 1


@pytest.mark.asyncio
async def test_llm_dbt_repair_rejects_unapproved_scope(monkeypatch):
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=MagicMock(content=VALID_YAML.replace("trip_id", "secret")))
    monkeypatch.setattr("src.agents.nodes.llm_dbt_repair_node.get_llm", lambda *args, **kwargs: llm)
    result = await llm_dbt_repair_node(
        {
            "generated_dbt_yaml": VALID_YAML,
            "dbt_validation_valid": False,
            "dbt_validation_attempts": 2,
            "approved_rules": [{"table_name": "trips", "column": "trip_id"}],
        }
    )
    assert result["dbt_validation_attempts"] == 3
    assert "outside approved rules" in result["dbt_validation_error"]
