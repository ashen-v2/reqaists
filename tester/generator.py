"""Ask local Ollama for a batch of classified test cases."""

from __future__ import annotations

import os
import re

from pydantic import ValidationError

from tester.openapi_loader import OperationSpec
from tester.schema_builder import (
    build_batch_model,
    default_invalid_status,
    describe_operation,
    primary_success,
)

DEFAULT_MODEL = "gemma4:e4b"
CHUNK_SIZE = 5


def model_name() -> str:
    return os.environ.get("OLLAMA_MODEL", DEFAULT_MODEL)


def generate_cases(operation: OperationSpec, user_prompt: str, count: int) -> list[dict]:
    if count < 1:
        raise ValueError("Ask for at least 1 test case.")
    if count > 30:
        raise ValueError("Ask for 30 test cases or fewer in one run.")
    prompt = user_prompt.strip()
    if not prompt:
        raise ValueError("Describe the kind of data you want.")

    client = _client()
    name = model_name()
    cases: list[dict] = []
    remaining = count
    while remaining:
        size = min(CHUNK_SIZE, remaining)
        chunk = _generate_chunk(client, name, operation, prompt, size, cases)
        cases.extend(chunk)
        remaining -= size
    return cases


def _generate_chunk(client, name: str, operation: OperationSpec, user_prompt: str, size: int, prior: list[dict]) -> list[dict]:
    batch_model = build_batch_model(operation, size)
    schema = batch_model.model_json_schema()
    messages = [
        {"role": "system", "content": _system_prompt()},
        {"role": "user", "content": _user_prompt(operation, user_prompt, size, prior)},
    ]
    last_error = ""
    for attempt in range(2):
        if last_error:
            messages = [
                *messages,
                {
                    "role": "user",
                    "content": (
                        "The previous JSON failed validation: "
                        f"{last_error}. Return exactly {size} cases that match the schema."
                    ),
                },
            ]
        try:
            response = client.chat(
                model=name,
                messages=messages,
                format=schema,
                think=False,
                keep_alive="10m",
                options={"temperature": 0.7, "num_predict": 4096},
            )
        except Exception as exc:
            raise RuntimeError(_ollama_error(exc, name)) from exc
        raw = (response.message.content or "").strip()
        try:
            batch = batch_model.model_validate_json(_strip_fences(raw))
        except ValidationError as exc:
            last_error = str(exc)
            continue
        dumped = [item.model_dump(mode="json") for item in batch.cases]
        return [_coerce_status(operation, item) for item in dumped]
    raise RuntimeError(f"Ollama returned test data that did not match the schema. {last_error}")


def _coerce_status(operation: OperationSpec, case: dict) -> dict:
    kind = case.get("case_kind")
    if kind in {"happy", "edge"}:
        case["expected_status"] = primary_success(operation)
    else:
        status = case.get("expected_status")
        if not isinstance(status, int) or status < 400:
            case["expected_status"] = default_invalid_status(operation)
        case["case_kind"] = "invalid"
    return case


def _system_prompt() -> str:
    return (
        "You generate HTTP API test cases as JSON that matches the given schema. "
        "Classify every case with case_kind: "
        "happy means realistic valid data that should succeed, "
        "edge means still valid data sitting on a boundary, "
        "invalid means the types stay correct but the values should be rejected or not found. "
        "Follow the user's request for locales, names, and scenarios. "
        "Make each case different. Do not wrap the JSON in markdown."
    )


def _user_prompt(operation: OperationSpec, user_prompt: str, size: int, prior: list[dict]) -> str:
    used = ", ".join(case.get("name", "") for case in prior if case.get("name"))
    prior_line = f"Names already used, do not repeat them: {used}" if used else "This is the first batch."
    success = primary_success(operation)
    invalid = default_invalid_status(operation)
    return (
        f"Produce exactly {size} test cases.\n"
        f"User request: {user_prompt}\n"
        f"{prior_line}\n"
        "Rules:\n"
        f"- happy and edge cases must satisfy the API constraints and use expected_status {success}.\n"
        f"- invalid cases must break a constraint or use a missing id, and use expected_status {invalid} "
        "unless a different 4xx code is clearly right.\n"
        "- Include a mix of happy, edge, and invalid when the count allows it.\n"
        "- Put path values in path_params, query values in query_params, and the JSON object in body.\n"
        "- If a section has no fields, use an empty object. If there is no body, set body to null.\n\n"
        f"{describe_operation(operation)}"
    )


def _client():
    from ollama import Client

    host = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
    return Client(host=host)


def _strip_fences(text: str) -> str:
    if not text.startswith("```"):
        return text
    text = re.sub(r"^```(?:json)?\s*", "", text)
    return re.sub(r"\s*```$", "", text)


def _ollama_error(exc: Exception, name: str) -> str:
    text = str(exc).lower()
    if "connection" in text or "refused" in text or "11434" in text:
        return "Ollama is not reachable at 127.0.0.1:11434. Start Ollama and try again."
    if "not found" in text or "try pulling" in text:
        return f"Model '{name}' is not installed. Run: ollama pull {name}"
    return f"Ollama request failed: {exc}"
