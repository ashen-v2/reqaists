"""Local web page for the same two tester modes."""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from starlette.requests import Request

from tester.generator import model_name
from tester.openapi_loader import LoadedApi, load_api
from tester.service import RUNS, prepare

ROOT = Path(__file__).resolve().parents[1]
UPLOADS = ROOT / "uploads"
TEMPLATES = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))
RUN_ID = re.compile(r"^\d{8}-\d{6}(?:-\d+)?$")

app = FastAPI(title="CRUD API Tester", version="1.0.0")
CACHE: dict[str, LoadedApi] = {}


class JobRequest(BaseModel):
    mode: str
    token: str
    operation_key: str
    count: int = Field(ge=1, le=30)
    prompt: str = Field(min_length=1)


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return TEMPLATES.TemplateResponse(
        request,
        "index.html",
        {"model_name": model_name()},
    )


@app.post("/api/spec")
async def read_spec(
    source: str = Form(default=""),
    file: UploadFile | None = File(default=None),
) -> dict:
    token, loaded = await _load_request(source, file)
    CACHE[token] = loaded
    return {
        "token": token,
        "base_url": loaded.base_url,
        "source": loaded.source_label,
        "operations": [item.public() for item in loaded.operations],
    }


@app.post("/api/jobs")
def create_job(job: JobRequest) -> dict:
    loaded = _cached(job.token)
    if job.mode == "run" and not loaded.base_url:
        raise HTTPException(
            status_code=400,
            detail="Run mode needs a live server address, not only an OpenAPI file.",
        )
    if not any(item.key == job.operation_key for item in loaded.operations):
        raise HTTPException(status_code=400, detail=f"Unknown endpoint '{job.operation_key}'.")
    try:
        template = prepare(loaded.spec_location, job.operation_key, job.count, job.prompt, job.mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"template": template, "download": f"/api/runs/{template['id']}"}


@app.get("/api/runs/{run_id}")
def download_run(run_id: str) -> FileResponse:
    if not RUN_ID.fullmatch(run_id):
        raise HTTPException(status_code=404, detail="Run not found.")
    path = RUNS / f"{run_id}.json"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Run not found.")
    return FileResponse(path, media_type="application/json", filename=path.name)


async def _load_request(source: str, file: UploadFile | None) -> tuple[str, LoadedApi]:
    if file is not None and file.filename:
        payload = await file.read()
        try:
            json.loads(payload)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="Uploaded file is not valid JSON.") from exc
        UPLOADS.mkdir(parents=True, exist_ok=True)
        dest = UPLOADS / f"{uuid.uuid4().hex}.json"
        dest.write_bytes(payload)
        token = f"file:{dest.name}"
        try:
            return token, load_api(str(dest))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    cleaned = source.strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="Enter a server address or upload an OpenAPI file.")
    try:
        return cleaned, load_api(cleaned)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _cached(token: str) -> LoadedApi:
    loaded = CACHE.get(token)
    if loaded is not None:
        return loaded
    if token.startswith("file:"):
        path = UPLOADS / token.removeprefix("file:")
        if not path.is_file():
            raise HTTPException(status_code=400, detail="That OpenAPI upload is no longer available. Load it again.")
        source = str(path)
    else:
        source = token
    try:
        loaded = load_api(source)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    CACHE[token] = loaded
    return loaded
