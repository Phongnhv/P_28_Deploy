import pytest

from src.agents.nodes.data_dictionary_generator_node import data_dictionary_generator_node


class FakeResult:
    table_name = "wrong"

    def model_dump(self):
        return {"table_name": self.table_name, "columns": [{"name": "amount"}]}


class FakeStructured:
    async def ainvoke(self, _messages):
        return FakeResult()


class FakeLLM:
    def with_structured_output(self, _model):
        return FakeStructured()


@pytest.mark.asyncio
async def test_infers_dictionary_when_missing(monkeypatch):
    monkeypatch.setattr(
        "src.agents.nodes.data_dictionary_generator_node.get_llm",
        lambda *_args, **_kwargs: FakeLLM(),
    )
    state = {
        "dataset_profile_digest": {"orders": {"columns": []}},
        "metadata": {"domain_hint": "e-commerce"},
    }
    result = await data_dictionary_generator_node(state)
    assert result["data_dictionary_source"] == "inferred"
    assert result["normalized_data_dictionary"]["inferred"] is True
    assert result["normalized_data_dictionary"]["tables"]["orders"]["table_name"] == "orders"


@pytest.mark.asyncio
async def test_preserves_supplied_dictionary():
    supplied = {"tables": {"orders": {"columns": [{"name": "id"}]}}}
    result = await data_dictionary_generator_node({"normalized_data_dictionary": supplied})
    assert result == {"data_dictionary_source": "supplied"}
