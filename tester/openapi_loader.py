"""Load an OpenAPI document and split it into operations."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import httpx

HTTP_METHODS = {"get", "post", "put", "patch", "delete"}


@dataclass
class ParameterSpec:
    name: str
    required: bool
    schema: dict


@dataclass
class OperationSpec:
    key: str
    method: str
    path: str
    summary: str
    path_params: list[ParameterSpec] = field(default_factory=list)
    query_params: list[ParameterSpec] = field(default_factory=list)
    body_schema: dict | None = None
    success_statuses: list[int] = field(default_factory=list)
    error_statuses: list[int] = field(default_factory=list)

    def public(self) -> dict:
        return {
            "key": self.key,
            "method": self.method.upper(),
            "path": self.path,
            "summary": self.summary,
            "path_params": [item.name for item in self.path_params],
            "query_params": [item.name for item in self.query_params],
            "has_body": self.body_schema is not None,
            "success_statuses": self.success_statuses,
            "error_statuses": self.error_statuses,
        }


@dataclass
class LoadedApi:
    source_label: str
    spec_location: str
    base_url: str | None
    operations: list[OperationSpec]

    def operation(self, key: str) -> OperationSpec:
        for item in self.operations:
            if item.key == key:
                return item
        known = ", ".join(item.key for item in self.operations)
        raise ValueError(f"Unknown endpoint '{key}'. Known endpoints: {known}")


def interpret_source(source: str) -> tuple[str, str, str | None]:
    """Return kind ('file' or 'url'), spec location, and API base URL."""
    raw = source.strip().strip('"').strip("'")
    if not raw:
        raise ValueError("Enter a server address, an OpenAPI URL, or a file path.")

    path = Path(raw)
    if path.exists() and path.is_file():
        return "file", str(path.resolve()), None

    url = raw if "://" in raw else f"http://{raw}"
    parsed = urlparse(url)
    if not parsed.netloc:
        raise ValueError(f"Could not read a host from '{source}'.")
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    if parsed.path.rstrip("/").endswith("openapi.json"):
        spec_url = url
    else:
        spec_url = f"{base_url}/openapi.json"
    return "url", spec_url, base_url


def load_api(source: str) -> LoadedApi:
    kind, location, base_url = interpret_source(source)
    if kind == "file":
        try:
            spec = json.loads(Path(location).read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"OpenAPI file is not valid JSON: {exc}") from exc
        label = str(Path(location))
    else:
        try:
            response = httpx.get(location, timeout=15.0, follow_redirects=True)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ValueError(f"Could not fetch OpenAPI from {location}: {exc}") from exc
        try:
            spec = response.json()
        except json.JSONDecodeError as exc:
            raise ValueError(f"{location} did not return JSON.") from exc
        label = location

    if not isinstance(spec, dict) or "paths" not in spec:
        raise ValueError("That document has no paths. Expected an OpenAPI document.")

    operations = parse_operations(spec)
    if not operations:
        raise ValueError("The OpenAPI document has no HTTP operations.")
    return LoadedApi(
        source_label=label,
        spec_location=location,
        base_url=base_url,
        operations=operations,
    )


def parse_operations(spec: dict) -> list[OperationSpec]:
    components = (spec.get("components") or {}).get("schemas") or {}
    operations: list[OperationSpec] = []
    for path, path_item in (spec.get("paths") or {}).items():
        if not isinstance(path_item, dict):
            continue
        shared_params = path_item.get("parameters") or []
        for method, operation in path_item.items():
            if method.lower() not in HTTP_METHODS or not isinstance(operation, dict):
                continue
            params = [_parameter(item, components) for item in shared_params]
            params += [
                _parameter(item, components) for item in operation.get("parameters") or []
            ]
            params = [item for item in params if item is not None]
            body_schema = _body_schema(operation, components)
            success, errors = _statuses(operation)
            method_name = method.lower()
            operations.append(
                OperationSpec(
                    key=f"{method_name.upper()} {path}",
                    method=method_name,
                    path=path,
                    summary=operation.get("summary") or operation.get("operationId") or "",
                    path_params=[_public_param(item) for item in params if item.location == "path"],
                    query_params=[_public_param(item) for item in params if item.location == "query"],
                    body_schema=body_schema,
                    success_statuses=success,
                    error_statuses=errors,
                )
            )
    return operations


@dataclass
class _ParsedParameter:
    name: str
    location: str
    required: bool
    schema: dict


def _public_param(item: _ParsedParameter) -> ParameterSpec:
    return ParameterSpec(name=item.name, required=item.required, schema=item.schema)


def _parameter(raw: dict, components: dict) -> _ParsedParameter | None:
    if not isinstance(raw, dict):
        return None
    if "$ref" in raw:
        raw = _resolve(raw, components, [])
    location = raw.get("in")
    name = raw.get("name")
    if location not in {"path", "query"} or not name:
        return None
    schema = _resolve(raw.get("schema") or {"type": "string"}, components, [])
    required = bool(raw.get("required")) or location == "path"
    return _ParsedParameter(name=name, location=location, required=required, schema=schema)


def _body_schema(operation: dict, components: dict) -> dict | None:
    body = operation.get("requestBody") or {}
    if "$ref" in body:
        body = _resolve(body, components, [])
    content = body.get("content") or {}
    media = content.get("application/json") or {}
    schema = media.get("schema")
    if not schema:
        return None
    return _merge_all_of(_resolve(schema, components, []))


def _statuses(operation: dict) -> tuple[list[int], list[int]]:
    success: list[int] = []
    errors: list[int] = []
    for code in operation.get("responses") or {}:
        if not str(code).isdigit():
            continue
        number = int(code)
        if 200 <= number < 300:
            success.append(number)
        elif number >= 400:
            errors.append(number)
    return success, errors


def _resolve(node, components: dict, stack: list[str]):
    if isinstance(node, list):
        return [_resolve(item, components, stack) for item in node]
    if not isinstance(node, dict):
        return node
    if "$ref" in node:
        name = str(node["$ref"]).rsplit("/", 1)[-1]
        if name in stack:
            return {"type": "object", "title": name}
        target = components.get(name)
        if target is None:
            return {"type": "object", "title": name}
        stack.append(name)
        resolved = _resolve(json.loads(json.dumps(target)), components, stack)
        stack.pop()
        extras = {key: value for key, value in node.items() if key != "$ref"}
        if extras and isinstance(resolved, dict):
            resolved = {**resolved, **_resolve(extras, components, stack)}
        return _merge_all_of(resolved) if isinstance(resolved, dict) else resolved
    return {
        key: _resolve(value, components, stack)
        for key, value in node.items()
    }


def _merge_all_of(schema: dict) -> dict:
    parts = schema.get("allOf")
    if not parts:
        return schema
    properties: dict = {}
    required: list[str] = []
    merged = {key: value for key, value in schema.items() if key != "allOf"}
    for part in parts:
        if not isinstance(part, dict):
            continue
        part = _merge_all_of(part)
        properties.update(part.get("properties") or {})
        required.extend(part.get("required") or [])
    properties.update(schema.get("properties") or {})
    required.extend(schema.get("required") or [])
    merged["type"] = merged.get("type") or "object"
    merged["properties"] = properties
    if required:
        merged["required"] = list(dict.fromkeys(required))
    return merged
