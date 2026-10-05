"""Turn one OpenAPI operation into a Pydantic batch the model must fill."""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model

from tester.openapi_loader import OperationSpec, ParameterSpec

CASE_KINDS = ("happy", "edge", "invalid")


def build_batch_model(operation: OperationSpec, count: int) -> type[BaseModel]:
    path_model = _params_model("PathParams", operation.path_params)
    query_model = _params_model("QueryParams", operation.query_params)
    fields: dict[str, Any] = {
        "name": (str, Field(..., description="Short label for this case")),
        "case_kind": (
            Literal["happy", "edge", "invalid"],
            Field(
                ...,
                description="happy = valid success, edge = valid boundary, invalid = should fail",
            ),
        ),
        "description": (str, Field(..., description="What this case is checking")),
        "path_params": (path_model, Field(...)),
        "query_params": (query_model, Field(...)),
        "expected_status": (int, Field(..., description="HTTP status this case should receive")),
    }
    if operation.body_schema is not None:
        body_model = schema_to_model("Body", operation.body_schema)
        fields["body"] = (body_model, Field(..., description="JSON request body"))
    else:
        fields["body"] = (None, Field(default=None, description="No request body"))

    case_model = create_model(
        "TestCase",
        __config__=ConfigDict(extra="ignore"),
        **fields,
    )
    case_list = Annotated[list[case_model], Field(min_length=count, max_length=count)]
    return create_model(
        "TestBatch",
        __config__=ConfigDict(extra="ignore"),
        cases=(case_list, ...),
    )


def schema_to_model(name: str, schema: dict) -> type[BaseModel]:
    properties = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    fields: dict[str, Any] = {}
    for key, prop in properties.items():
        if not isinstance(prop, dict):
            continue
        fields[_field_name(key)] = _field_definition(key, prop, key in required)
    config = ConfigDict(extra="ignore")
    if not fields:
        return create_model(_safe_name(name), __config__=config)
    return create_model(_safe_name(name), __config__=config, **fields)


def describe_operation(operation: OperationSpec) -> str:
    lines = [
        f"Method: {operation.method.upper()} {operation.path}",
        f"Summary: {operation.summary or '(none)'}",
        "Path parameters:",
        _describe_params(operation.path_params),
        "Query parameters:",
        _describe_params(operation.query_params),
        "JSON body:",
        describe_schema(operation.body_schema),
        f"Success statuses: {operation.success_statuses or '(unspecified)'}",
        f"Error statuses: {operation.error_statuses or '(unspecified)'}",
    ]
    return "\n".join(lines)


def describe_schema(schema: dict | None) -> str:
    if not schema:
        return "(none)"
    properties = schema.get("properties") or {}
    if not properties:
        return _prop_summary(schema)
    required = set(schema.get("required") or [])
    lines = []
    for name, prop in properties.items():
        if not isinstance(prop, dict):
            continue
        flag = "required" if name in required else "optional"
        lines.append(f"- {name}: {_prop_summary(prop)} ({flag})")
    return "\n".join(lines) or "(empty object)"


def primary_success(operation: OperationSpec) -> int:
    codes = operation.success_statuses
    if operation.method == "post" and 201 in codes:
        return 201
    if operation.method == "delete" and 204 in codes:
        return 204
    if codes:
        return codes[0]
    if operation.method == "post":
        return 201
    if operation.method == "delete":
        return 204
    return 200


def default_invalid_status(operation: OperationSpec) -> int:
    if operation.path_params and operation.method in {"get", "delete"}:
        return 404
    if 422 in operation.error_statuses:
        return 422
    if operation.error_statuses:
        return operation.error_statuses[0]
    return 422


def _params_model(name: str, params: list[ParameterSpec]) -> type[BaseModel]:
    fields: dict[str, Any] = {}
    for param in params:
        fields[_field_name(param.name)] = _field_definition(
            param.name,
            param.schema,
            param.required,
        )
    if not fields:
        return create_model(name, __config__=ConfigDict(extra="ignore"))
    return create_model(name, __config__=ConfigDict(extra="ignore"), **fields)


def _field_definition(name: str, prop: dict, required: bool) -> tuple[Any, Any]:
    annotation = _annotation(name, prop)
    nullable = _is_nullable(prop)
    if required and not nullable:
        return annotation, Field(..., description=_prop_summary(prop))
    optional = annotation | None
    return optional, Field(default=None, description=_prop_summary(prop))


def _annotation(name: str, prop: dict) -> Any:
    prop, _nullable = _unwrap(prop)
    if "enum" in prop and prop["enum"]:
        return Literal[tuple(prop["enum"])]
    schema_type = prop.get("type")
    if isinstance(schema_type, list):
        non_null = [item for item in schema_type if item != "null"]
        schema_type = non_null[0] if non_null else "string"
    if schema_type == "integer":
        return int
    if schema_type == "number":
        return float
    if schema_type == "boolean":
        return bool
    if schema_type == "array":
        items = prop.get("items") or {"type": "string"}
        return list[_annotation(f"{name}Item", items)]
    if schema_type == "object" or prop.get("properties"):
        return schema_to_model(name, prop)
    return str


def _unwrap(prop: dict) -> tuple[dict, bool]:
    nullable = False
    if isinstance(prop.get("type"), list) and "null" in prop["type"]:
        nullable = True
    for key in ("anyOf", "oneOf"):
        options = prop.get(key)
        if not isinstance(options, list):
            continue
        non_null = []
        for option in options:
            if not isinstance(option, dict):
                continue
            if option.get("type") == "null":
                nullable = True
                continue
            non_null.append(option)
        if len(non_null) == 1:
            merged = {key: value for key, value in prop.items() if key not in {"anyOf", "oneOf"}}
            merged.update(non_null[0])
            return merged, nullable
    if prop.get("nullable"):
        nullable = True
    return prop, nullable


def _is_nullable(prop: dict) -> bool:
    _, nullable = _unwrap(prop)
    return nullable


def _prop_summary(prop: dict) -> str:
    inner, nullable = _unwrap(prop)
    if "enum" in inner and inner["enum"]:
        text = "enum " + ", ".join(str(item) for item in inner["enum"])
    else:
        schema_type = inner.get("type") or "string"
        if isinstance(schema_type, list):
            schema_type = "|".join(str(item) for item in schema_type if item != "null")
        bits = [str(schema_type)]
        if inner.get("format"):
            bits.append(str(inner["format"]))
        for label, key in (
            ("min", "minimum"),
            ("max", "maximum"),
            ("minLength", "minLength"),
            ("maxLength", "maxLength"),
        ):
            if key in inner:
                bits.append(f"{label}={inner[key]}")
        text = " ".join(bits)
    if nullable:
        text += ", nullable"
    return text


def _describe_params(params: list[ParameterSpec]) -> str:
    if not params:
        return "(none)"
    lines = []
    for param in params:
        flag = "required" if param.required else "optional"
        lines.append(f"- {param.name}: {_prop_summary(param.schema)} ({flag})")
    return "\n".join(lines)


def _safe_name(name: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z_]", "", name) or "Model"
    if cleaned[0].isdigit():
        cleaned = f"M{cleaned}"
    return cleaned


def _field_name(name: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z_]", "_", name)
    if not cleaned or cleaned[0].isdigit():
        cleaned = f"f_{cleaned}"
    return cleaned
