from __future__ import annotations

import asyncio
import argparse
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, Optional
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from config import settings
from event_bus import EventBus
from models import PipelineEvent
from pipeline import run_pipeline
from startup_check import run_startup_checks

app = FastAPI(
    title="AI Scholarship Finder API",
    description="Multi-agent academic opportunity search, verification, ranking, and SOP drafting service.",
    version="1.0.0",
)

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
async def health_check() -> Dict[str, Any]:
    return {
        "status": "online",
        "gemini_model": settings.GEMINI_MODEL,
        "gemini_configured": bool(settings.GEMINI_API_KEY),
        "tavily_configured": bool(settings.TAVILY_API_KEY),
    }


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
    pick: int = Form(1),
) -> JSONResponse:
    """
    Executes the multi-agent pipeline for uploaded student profile files.
    """
    temp_dir = tempfile.mkdtemp(prefix="scholarship_agents_")
    resume_path = None
    marksheet_path = None

    try:
        if REPLAY_RESULT_PATH is not None:
            startup_result = await run_startup_checks()
            if not (startup_result.get("llm_live") and startup_result.get("tavily_live")):
                return _load_replay_result()

        if resume:
            resume_path = os.path.join(temp_dir, resume.filename or "resume.pdf")
            with open(resume_path, "wb") as buffer:
                shutil.copyfileobj(resume.file, buffer)

        if marksheet:
            marksheet_path = os.path.join(temp_dir, marksheet.filename or "marksheet.pdf")
            with open(marksheet_path, "wb") as buffer:
                shutil.copyfileobj(marksheet.file, buffer)

        inputs = {
            "resume_path": resume_path,
            "marksheet_path": marksheet_path,
            "github_username": github_username,
            "chosen_pick": pick,
        }

        # Forward events from the pipeline run to the global event bus
        async def forward_event(event: PipelineEvent):
            for q in list(global_event_bus._queues):
                await q.put(event)

        result = await run_pipeline(inputs, on_event=forward_event, print_to_terminal=True)
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
