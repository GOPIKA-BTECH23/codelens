"""Turn a Pydantic model into the JSON Schema we send to providers for structured output.

Pydantic's own `model_json_schema()` is valid JSON Schema, but providers accept a subset:
  * Groq strict mode (constrained decoding, checked 2026-10-17): every property must be in
    `required` (optional values are expressed as a union with null), and every object must
    set `additionalProperties: false`.
  * Gemini (checked 2026-10-17): supports a documented subset; very large or deeply nested
    schemas may be rejected.
So we send ONE conservative shape to both: `$ref`s inlined, every field required, no extra
properties, and value constraints (minimum, maxLength, ...) removed. The constraints are not
lost: Pydantic re-validates every answer against the full model afterwards, and an answer
that breaks one triggers the agent's retry. The field descriptions stay, because they are
instructions the model reads.
"""

from typing import Any

from pydantic import BaseModel

# Keywords we drop: annotations the model does not need, and constraints that strict modes
# may reject. Pydantic enforces the constraints on our side.
_DROP = frozenset(
    {
        "title",
        "default",
        "examples",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "minLength",
        "maxLength",
        "minItems",
        "maxItems",
        "pattern",
        "format",
    }
)


def provider_schema(model: type[BaseModel]) -> dict[str, Any]:
    """The strict, self-contained JSON Schema for `model`."""
    schema = model.model_json_schema()
    definitions = schema.pop("$defs", {})
    result = _clean(schema, definitions, seen=())
    assert isinstance(result, dict)
    return result


def _clean(node: Any, definitions: dict[str, Any], seen: tuple[str, ...]) -> Any:
    if isinstance(node, list):
        return [_clean(item, definitions, seen) for item in node]
    if not isinstance(node, dict):
        return node
    if "$ref" in node:
        name = node["$ref"].removeprefix("#/$defs/")
        if name in seen:
            raise ValueError(f"recursive schema through '{name}' is not supported")
        inlined = _clean(definitions[name], definitions, (*seen, name))
        siblings = {k: v for k, v in node.items() if k != "$ref"}  # e.g. a field description
        return {**inlined, **_clean(siblings, definitions, seen)}

    cleaned: dict[str, Any] = {}
    for key, value in node.items():
        if key in _DROP:
            continue
        if key == "properties":
            cleaned[key] = {name: _clean(sub, definitions, seen) for name, sub in value.items()}
        else:
            cleaned[key] = _clean(value, definitions, seen)

    if cleaned.get("type") == "object" and "properties" in cleaned:
        cleaned["required"] = list(cleaned["properties"])
        cleaned["additionalProperties"] = False
    return cleaned
