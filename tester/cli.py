"""Interactive terminal for generating and running API tests."""

from __future__ import annotations

from tester.openapi_loader import load_api
from tester.service import prepare


def main() -> None:
    print("CRUD API Tester")
    print("Uses local Ollama (gemma4:e4b by default) to build classified test data.")
    mode = _choose_mode()
    if mode == "run":
        source = _ask("Server address (example: localhost:8000)")
    else:
        source = _ask("Server address, OpenAPI URL, or path to an OpenAPI JSON file")

    try:
        api = load_api(source)
    except ValueError as exc:
        print(f"Error: {exc}")
        return

    print()
    for index, operation in enumerate(api.operations, start=1):
        summary = f" - {operation.summary}" if operation.summary else ""
        print(f"{index}. {operation.key}{summary}")
    choice = _ask_int("Endpoint number", 1, len(api.operations))
    operation = api.operations[choice - 1]
    count = _ask_int("How many test cases", 1, 30)
    prompt = _ask("What kind of data (example: edge cases, or Sri Lankan names)")

    print()
    print(f"Generating {count} cases for {operation.key}...")
    try:
        template = prepare(source, operation.key, count, prompt, mode)
    except (ValueError, RuntimeError) as exc:
        print(f"Error: {exc}")
        return

    _print_cases(template)
    if mode == "run":
        _print_results(template)
    print(f"Saved {template['path']}")


def _choose_mode() -> str:
    while True:
        raw = input("Mode: [1] Run tests  [2] Generate only: ").strip()
        if raw in {"1", "run"}:
            return "run"
        if raw in {"2", "generate"}:
            return "generate"
        print("Enter 1 or 2.")


def _ask(label: str) -> str:
    while True:
        value = input(f"{label}: ").strip()
        if value:
            return value
        print("Enter a value.")


def _ask_int(label: str, low: int, high: int) -> int:
    while True:
        raw = input(f"{label} ({low}-{high}): ").strip()
        if raw.isdigit() and low <= int(raw) <= high:
            return int(raw)
        print(f"Enter a whole number from {low} to {high}.")


def _print_cases(template: dict) -> None:
    print()
    for index, case in enumerate(template["cases"], start=1):
        print(
            f"{index}. [{case['case_kind']}] {case['name']} "
            f"-> expected {case['expected_status']}"
        )
        print(f"   {case['description']}")


def _print_results(template: dict) -> None:
    print()
    for result in template["results"]:
        mark = "PASS" if result["passed"] else "FAIL"
        actual = result["actual_status"] if result["actual_status"] is not None else "-"
        print(
            f"{mark}  {result['name']}  "
            f"expected {result['expected_status']}  actual {actual}  "
            f"{result['latency_ms']} ms"
        )
        if result.get("error"):
            print(f"     {result['error']}")
    print(f"{template['passed']} passed, {template['failed']} failed")
