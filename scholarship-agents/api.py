from __future__ import annotations

import asyncio
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
        return JSONResponse(content=result)

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        # Clean up temp files
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)
