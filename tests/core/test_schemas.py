import pytest
from pydantic import ValidationError

from investigator.schemas import LLM_OUTPUT_SCHEMAS, Incident, Usage

NON_PORTABLE_KEYS = {"oneOf", "allOf", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"}


def _walk(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value)


@pytest.mark.parametrize("schema", LLM_OUTPUT_SCHEMAS, ids=lambda s: s.__name__)
def test_llm_schemas_stay_portable(schema):
    for node in _walk(schema.model_json_schema()):
        assert not NON_PORTABLE_KEYS & node.keys()
        assert "anyOf" not in node
        assert not isinstance(node.get("additionalProperties"), dict)


def test_no_llm_schema_can_self_report_a_score():
    for schema in LLM_OUTPUT_SCHEMAS:
        for node in _walk(schema.model_json_schema()):
            assert not {"score", "confidence", "verified_ratio", "faithfulness"} & set(
                node.get("properties", {})
            )


def test_usage_adds():
    total = Usage(input_tokens=1, tool_calls=2) + Usage(input_tokens=3, output_tokens=4)
    assert total == Usage(input_tokens=4, output_tokens=4, tool_calls=2)
    assert total.total_tokens == 8


def test_incident_rejects_oversized_input():
    with pytest.raises(ValidationError):
        Incident(
            id="INC-1", title="t", description="x" * 4001, service="s", reported_at="2026-01-01"
        )
