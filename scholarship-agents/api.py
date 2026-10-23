from __future__ import annotations

import asyncio
import argparse
import json
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, Optional
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from event_bus import EventBus
from models import PipelineEvent
from pipeline import run_pipeline
from startup_check import run_startup_checks

app = FastAPI(
    title="AI Scholarship Finder API",
    description="Multi-agent academic opportunity search, verification, ranking, and SOP drafting service.",
    version="1.0.0",
)
logger = logging.getLogger(__name__)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global or session event bus broadcaster
global_event_bus = EventBus(print_to_terminal=True)
REPLAY_RESULT_PATH: Optional[Path] = None
STATIC_INDEX_PATH = Path(__file__).resolve().parent / "static" / "index.html"
DEMO_RESULT_PATH = Path(__file__).resolve().parent / "samples" / "demo_result.json"
app.mount("/static", StaticFiles(directory=STATIC_INDEX_PATH.parent), name="static")


def _load_replay_result() -> JSONResponse:
    if REPLAY_RESULT_PATH is None or not REPLAY_RESULT_PATH.is_file():
        raise HTTPException(
            status_code=503,
            detail="Live APIs failed and no readable replay result was configured.",
        )
    try:
        with REPLAY_RESULT_PATH.open("r", encoding="utf-8") as result_file:
            result = json.load(result_file)
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Unable to load configured replay result: {exc}",
        ) from exc

    if not isinstance(result, dict):
        raise HTTPException(
            status_code=500,
            detail="Configured replay result must contain a JSON object.",
        )
    result["run_mode"] = "REPLAY"
    result["replay_notice"] = (
        "REPLAY: serving a saved demo result because live APIs were unavailable. "
        "This response is not a live run."
    )
    return JSONResponse(
        content=result,
        headers={"X-Run-Mode": "REPLAY"},
    )


@app.get("/api/health")
async def health_check() -> Dict[str, bool]:
    startup_result = await run_startup_checks()
    return {"live": bool(startup_result.get("llm_live"))}


@app.get("/")
async def index() -> FileResponse:
    if not STATIC_INDEX_PATH.is_file():
        raise HTTPException(status_code=404, detail="static/index.html was not found.")
    return FileResponse(STATIC_INDEX_PATH, media_type="text/html")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> Response:
    return Response(
        content=(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
            '<rect width="32" height="32" rx="7" fill="#12233f"/>'
            '<path d="M7 9h18v2H7zm0 5h18v2H7zm0 5h12v2H7z" fill="#0f8b8d"/>'
            "</svg>"
        ),
        media_type="image/svg+xml",
    )


@app.get("/api/replay")
async def replay_result() -> JSONResponse:
    if not DEMO_RESULT_PATH.is_file():
        raise HTTPException(status_code=404, detail="samples/demo_result.json was not found.")
    try:
        with DEMO_RESULT_PATH.open("r", encoding="utf-8") as result_file:
            result = json.load(result_file)
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Unable to load samples/demo_result.json: {exc}",
        ) from exc
    return JSONResponse(content=result)


@app.get("/api/events")
async def stream_events() -> StreamingResponse:
    """Server-Sent Events (SSE) endpoint to stream agent activities in real-time."""
    queue = global_event_bus.create_stream_queue()

    async def event_generator() -> AsyncGenerator[str, None]:
        try:
            # Yield initial connection heartbeat
            yield f"event: ping\ndata: {json.dumps({'message': 'connected'})}\n\n"
            while True:
                event: PipelineEvent = await queue.get()
                payload = json.dumps(event.model_dump())
                yield f"event: {event.type}\ndata: {payload}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            global_event_bus.remove_stream_queue(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/find-scholarships")
async def find_scholarships_endpoint(
    resume: Optional[UploadFile] = File(None),
    marksheet: Optional[UploadFile] = File(None),
    github_username: Optional[str] = Form(None),
    target_countries: Optional[str] = Form(None),
    target_degree_level: Optional[str] = Form(None),
    financial_need: Optional[str] = Form(None),
    interests: Optional[str] = Form(None),
    pick: Optional[int] = Form(None),
) -> JSONResponse:
    """
    Executes the multi-agent pipeline for uploaded student profile files.
    """
    temp_dir = tempfile.mkdtemp(prefix="scholarship_agents_")
    resume_path = None
    marksheet_path = None
    uploaded_files: Dict[str, Dict[str, Any]] = {}
    received_fields = [
        name for name, value in (
            ("resume", resume),
            ("marksheet", marksheet),
            ("github_username", github_username),
            ("target_countries", target_countries),
            ("target_degree_level", target_degree_level),
            ("financial_need", financial_need),
            ("interests", interests),
            ("pick", pick),
        ) if value is not None
    ]

    try:
        if REPLAY_RESULT_PATH is not None:
            startup_result = await run_startup_checks()
            if not (startup_result.get("llm_live") and startup_result.get("tavily_live")):
                return _load_replay_result()

        if resume:
            resume_bytes = await resume.read()
            safe_name = Path(resume.filename or "resume.pdf").name
            resume_path = os.path.join(temp_dir, f"resume_{safe_name}")
            with open(resume_path, "wb") as buffer:
                buffer.write(resume_bytes)
            uploaded_files["resume"] = {
                "filename": safe_name,
                "size_bytes": len(resume_bytes),
            }

        if marksheet:
            marksheet_bytes = await marksheet.read()
            safe_name = Path(marksheet.filename or "marksheet.pdf").name
            marksheet_path = os.path.join(temp_dir, f"marksheet_{safe_name}")
            with open(marksheet_path, "wb") as buffer:
                buffer.write(marksheet_bytes)
            uploaded_files["marksheet"] = {
                "filename": safe_name,
                "size_bytes": len(marksheet_bytes),
            }

        logger.info(
            "Received scholarship form fields=%s uploaded_files=%s",
            received_fields,
            uploaded_files,
        )

        inputs = {
            "resume_path": resume_path,
            "marksheet_path": marksheet_path,
            "github_username": github_username,
            "target_countries": target_countries,
            "target_degree_level": target_degree_level or "Master's",
            "financial_need": financial_need,
            "interests": interests,
            "chosen_pick": pick or 1,
        }

        # Forward events from the pipeline run to the global event bus
        async def forward_event(event: PipelineEvent):
            for q in list(global_event_bus._queues):
                await q.put(event)

        result = await run_pipeline(inputs, on_event=forward_event, print_to_terminal=True)
        profile = result.get("student_profile") or {}
        inputs_read = profile.get("inputs_read") or {}
        for field_name, file_metadata in uploaded_files.items():
            extracted = (inputs_read.get(field_name) or {}).get("characters_read", 0)
            logger.info(
                "Extracted uploaded file field=%s filename=%s size_bytes=%d characters_extracted=%d",
                field_name,
                file_metadata["filename"],
                file_metadata["size_bytes"],
                extracted,
            )
        ranked = (result.get("ranked_results") or {}).get("top_opportunities") or []
        has_live_verification = any(
            opportunity.get("verification_mode") == "llm_live"
            for opportunity in ranked
        )
        if REPLAY_RESULT_PATH is not None and not has_live_verification:
            return _load_replay_result()
        result["run_mode"] = "LIVE" if has_live_verification else "OFFLINE"
        return JSONResponse(
            content=result,
            headers={"X-Run-Mode": result["run_mode"]},
        )

    except Exception as e:
        if REPLAY_RESULT_PATH is not None:
            return _load_replay_result()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        # Clean up temp files
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass


if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser(description="AI Scholarship Finder API")
    parser.add_argument(
        "--replay",
        type=Path,
        help="Saved JSON result to serve with an explicit REPLAY label if live APIs fail",
    )
    arguments = parser.parse_args()
    if arguments.replay is not None:
        REPLAY_RESULT_PATH = arguments.replay.resolve()
        if not REPLAY_RESULT_PATH.is_file():
            parser.error(f"replay file does not exist: {REPLAY_RESULT_PATH}")
    uvicorn.run(app, host="0.0.0.0", port=8000)
