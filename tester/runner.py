"""Send generated cases to a live API and record pass or fail."""

from __future__ import annotations

import json
import re
import time
import uuid
from typing import Any

import httpx

from tester.openapi_loader import LoadedApi, OperationSpec

ID_PARAM = re.compile(r"(^id$|_id$|Id$)", re.IGNORECASE)


def run_cases(api: LoadedApi, operation: OperationSpec, cases: list[dict]) -> list[dict]:
    if not api.base_url:
        raise ValueError("Run mode needs a live server address, not only an OpenAPI file.")
    base = api.base_url.rstrip("/")
    results: list[dict] = []
    with httpx.Client(timeout=20.0) as client:
        for case in cases:
            results.append(_run_one(client, base, api, operation, case))
    return results


def _run_one(
    client: httpx.Client,
    base: str,
    api: LoadedApi,
    operation: OperationSpec,
    case: dict,
) -> dict:
    path_params = dict(case.get("path_params") or {})
    query_params = {
        key: value
        for key, value in (case.get("query_params") or {}).items()
        if value is not None
    }
    body = case.get("body") if operation.body_schema is not None else None
    seed_note = None
    if _needs_seed(operation, case):
        seeded, seed_note = _seed_id(client, base, api, operation)
        if seeded is not None:
            _inject_id(path_params, operation, seeded)

    started = time.perf_counter()
    try:
        url = base + _fill_path(operation.path, path_params)
    except KeyError as exc:
        return _result(case, path_params, query_params, body, None, False, 0, "", f"Missing path value {exc}")

    request_kwargs: dict[str, Any] = {}
    if query_params:
        request_kwargs["params"] = query_params
    if body is not None:
        request_kwargs["json"] = body
    try:
        response = client.request(operation.method.upper(), url, **request_kwargs)
    except httpx.HTTPError as exc:
        elapsed = (time.perf_counter() - started) * 1000
        return _result(case, path_params, query_params, body, None, False, elapsed, "", str(exc))

    elapsed = (time.perf_counter() - started) * 1000
    excerpt = response.text[:500]
    actual = response.status_code
    expected = int(case.get("expected_status"))
    passed = actual == expected
    error = seed_note if seed_note and not passed else None
    return _result(case, path_params, query_params, body, actual, passed, elapsed, excerpt, error)


def _needs_seed(operation: OperationSpec, case: dict) -> bool:
    if case.get("case_kind") not in {"happy", "edge"}:
        return False
    if operation.method not in {"get", "put", "patch", "delete"}:
        return False
    return any(ID_PARAM.search(param.name) for param in operation.path_params)


def _seed_id(
    client: httpx.Client,
    base: str,
    api: LoadedApi,
    operation: OperationSpec,
) -> tuple[Any | None, str | None]:
    parent = re.sub(r"/\{[^/]+\}$", "", operation.path)
    creator = next(
        (
            item
            for item in api.operations
            if item.method == "post" and item.path == parent and item.body_schema
        ),
        None,
    )
    if creator is None:
        return None, "No create endpoint was found to seed an id."
    payload = example_from_schema(creator.body_schema or {})
    try:
        response = client.post(base + parent, json=payload)
    except httpx.HTTPError as exc:
        return None, f"Seed request failed: {exc}"
    if response.status_code not in {200, 201}:
        return None, f"Seed create returned {response.status_code}: {response.text[:200]}"
    try:
        data = response.json()
    except json.JSONDecodeError:
        return None, "Seed create did not return JSON."
    if isinstance(data, dict) and "id" in data:
        return data["id"], None
    return None, "Seed create response had no id."


def _inject_id(path_params: dict, operation: OperationSpec, value: Any) -> None:
    id_params = [param.name for param in operation.path_params if ID_PARAM.search(param.name)]
    target = id_params[-1] if id_params else operation.path_params[-1].name
    path_params[target] = value


def example_from_schema(schema: dict) -> dict:
    token = uuid.uuid4().hex[:8]
    properties = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    body: dict[str, Any] = {}
    for name, prop in properties.items():
        if name not in required and name != "notes":
            continue
        body[name] = _example_value(name, prop if isinstance(prop, dict) else {}, token)
    for name in required:
        body.setdefault(name, _example_value(name, {}, token))
    return body


def _example_value(name: str, prop: dict, token: str) -> Any:
    schema_type = prop.get("type")
    if isinstance(schema_type, list):
        schema_type = next((item for item in schema_type if item != "null"), "string")
    lowered = name.lower()
    if schema_type == "integer":
        if "age" in lowered:
            return 30
        return 1
    if schema_type == "number":
        return 1.0
    if schema_type == "boolean":
        return True
    if schema_type == "array":
        return []
    if "email" in lowered:
        return f"seed.{token}@example.com"
    if "phone" in lowered:
        return "555-0100"
    if "city" in lowered:
        return "Colombo"
    if "name" in lowered:
        return f"Seed Customer {token}"
    if "note" in lowered:
        return "seed record"
    return f"sample-{token}"


def _fill_path(path: str, params: dict) -> str:
    def replace(match: re.Match) -> str:
        key = match.group(1)
        if key not in params or params[key] is None:
            raise KeyError(key)
        return str(params[key])

    return re.sub(r"\{([^}]+)\}", replace, path)


def _result(
    case: dict,
    path_params: dict,
    query_params: dict,
    body: Any,
    actual: int | None,
    passed: bool,
    latency_ms: float,
    excerpt: str,
    error: str | None,
) -> dict:
    return {
        "name": case.get("name"),
        "case_kind": case.get("case_kind"),
        "description": case.get("description"),
        "expected_status": case.get("expected_status"),
        "actual_status": actual,
        "passed": passed,
        "latency_ms": round(latency_ms, 1),
        "request": {
            "path_params": path_params,
            "query_params": query_params,
            "body": body,
        },
        "response_excerpt": excerpt,
        "error": error,
    }
