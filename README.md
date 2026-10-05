# CRUD API Tester

A local tester that reads an OpenAPI document, builds a Pydantic model for one endpoint, and asks Ollama (`gemma4:e4b` by default) to fill classified test cases. Each case is marked `happy`, `edge`, or `invalid`. You can call the endpoint with that data, or only save it.

## Requirements

- Python 3.11+
- [Ollama](https://ollama.com) running locally
- The model: `ollama pull gemma4:e4b`

`OLLAMA_MODEL` overrides the model name. `OLLAMA_HOST` overrides the Ollama address (default `http://127.0.0.1:11434`).

## Install

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Sample API

The Customer CRUD server uses SQLite (`sample_api/customers.db`) and publishes `/openapi.json`.

```powershell
uvicorn sample_api.main:app --port 8000
```

Endpoints:

- `POST /customers`
- `GET /customers` with optional `city` and `min_age`
- `GET /customers/{customer_id}`
- `PUT /customers/{customer_id}`
- `DELETE /customers/{customer_id}`

Interactive docs: `http://localhost:8000/docs`

## Tester

Two modes, in the terminal or in the browser:

1. **Run tests.** Enter a live address such as `localhost:8000`. The tester loads OpenAPI, asks which endpoint, how many cases, and what kind of data, then calls the endpoint. Happy and edge cases that need an id create a seed record first.
2. **Generate only.** Enter a server address, an OpenAPI URL, or a path to an OpenAPI JSON file. Cases are saved and the API is not called.

Saved templates go to `runs/{timestamp}.json`.

### Terminal

```powershell
python -m tester
```

### Web

Use a second terminal so the sample API can stay on port 8000.

```powershell
uvicorn tester.web:app --port 8080
```

Open `http://localhost:8080`.
