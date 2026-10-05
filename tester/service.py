"""Shared flow for the CLI and the web UI."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from tester.generator import generate_cases, model_name
from tester.openapi_loader import LoadedApi, load_api
from tester.runner import run_cases

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "runs"


def prepare(source: str, operation_key: str, count: int, prompt: str, mode: str) -> dict:
    if mode not in {"run", "generate"}:
        raise ValueError("Mode must be 'run' or 'generate'.")
    api = load_api(source)
    if mode == "run" and not api.base_url:
        raise ValueError("Run mode needs a live server address, not only an OpenAPI file.")
    operation = api.operation(operation_key)
    cases = generate_cases(operation, prompt, count)
    template = _template(api, operation_key, operation, prompt, count, cases, mode)
    if mode == "run":
        template["results"] = run_cases(api, operation, cases)
        template["passed"] = sum(1 for item in template["results"] if item["passed"])
        template["failed"] = len(template["results"]) - template["passed"]
    path = _save(template)
    template["path"] = str(path)
    return template


def load_for_display(source: str) -> LoadedApi:
    return load_api(source)


def _template(api: LoadedApi, operation_key: str, operation, prompt: str, count: int, cases: list[dict], mode: str) -> dict:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return {
        "id": stamp,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "source": api.source_label,
        "base_url": api.base_url,
        "mode": mode,
        "model": model_name(),
        "endpoint": operation.public() | {"key": operation_key},
        "user_prompt": prompt,
        "count": count,
        "cases": cases,
        "results": None,
    }


def _save(template: dict) -> Path:
    RUNS.mkdir(parents=True, exist_ok=True)
    path = RUNS / f"{template['id']}.json"
    suffix = 2
    while path.exists():
        path = RUNS / f"{template['id']}-{suffix}.json"
        suffix += 1
    template["id"] = path.stem
    path.write_text(json.dumps(template, indent=2), encoding="utf-8")
    return path
