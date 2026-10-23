import asyncio
import pytest
import httpx
import api
from api import app
from models import PipelineEvent

@pytest.mark.asyncio
async def test_health_check_endpoint(monkeypatch):
    async def live_startup():
        return {"llm_live": True}

    monkeypatch.setattr(api, "run_startup_checks", live_startup)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json() == {"live": True}


@pytest.mark.asyncio
async def test_health_reports_offline(monkeypatch):
    async def offline_startup():
        return {"llm_live": False}

    monkeypatch.setattr(api, "run_startup_checks", offline_startup)
    response = await httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ).get("/api/health")
    assert response.json() == {"live": False}


@pytest.mark.asyncio
async def test_index_serves_static_page(tmp_path, monkeypatch):
    index_file = tmp_path / "index.html"
    index_file.write_text("<html>sample UI</html>", encoding="utf-8")
    monkeypatch.setattr(api, "STATIC_INDEX_PATH", index_file)
    response = await httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ).get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.text == "<html>sample UI</html>"


@pytest.mark.asyncio
async def test_replay_endpoint_returns_demo_result(tmp_path, monkeypatch):
    result_file = tmp_path / "demo_result.json"
    result_file.write_text('{"run_mode":"LIVE"}', encoding="utf-8")
    monkeypatch.setattr(api, "DEMO_RESULT_PATH", result_file)
    response = await httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ).get("/api/replay")
    assert response.status_code == 200
    assert response.json() == {"run_mode": "LIVE"}


@pytest.mark.asyncio
async def test_replay_endpoint_returns_404_when_demo_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "DEMO_RESULT_PATH", tmp_path / "missing.json")
    response = await httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ).get("/api/replay")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_events_stream_json_event_and_recovery_type():
    response = await api.stream_events()
    assert response.media_type == "text/event-stream"
    assert response.headers["x-accel-buffering"] == "no"
    assert response.headers["cache-control"] == "no-cache"
    stream = response.body_iterator
    assert "event: ping" in await anext(stream)

    await api.global_event_bus.emit(
        agent="ResearchAgent",
        event_type="recovery",
        message="retrying search",
    )
    frame = await asyncio.wait_for(anext(stream), timeout=1)
    assert "event: recovery" in frame
    assert '"agent": "ResearchAgent"' in frame
    assert '"type": "recovery"' in frame
    assert '"message": "retrying search"' in frame
    await stream.aclose()


@pytest.mark.asyncio
async def test_find_scholarships_forwards_run_events_to_stream_queue(monkeypatch):
    queue = api.global_event_bus.create_stream_queue()

    async def fake_pipeline(inputs, on_event, print_to_terminal):
        await on_event(
            PipelineEvent(
                agent="VerifierAgent",
                type="recovery",
                message="candidate timed out",
            )
        )
        return {"ranked_results": {"top_opportunities": []}}

    monkeypatch.setattr(api, "run_pipeline", fake_pipeline)
    try:
        response = await api.find_scholarships_endpoint(
            resume=None,
            marksheet=None,
            github_username=None,
            pick=1,
        )
        assert response.status_code == 200
        event = await asyncio.wait_for(queue.get(), timeout=1)
        assert event.model_dump()["agent"] == "VerifierAgent"
        assert event.model_dump()["type"] == "recovery"
        assert event.model_dump()["message"] == "candidate timed out"
    finally:
        api.global_event_bus.remove_stream_queue(queue)


@pytest.mark.asyncio
async def test_find_scholarships_falls_back_to_explicit_replay(tmp_path, monkeypatch):
    replay_file = tmp_path / "demo_result.json"
    replay_file.write_text('{"ranked_results": {"top_opportunities": []}}', encoding="utf-8")
    monkeypatch.setattr(api, "REPLAY_RESULT_PATH", replay_file)

    async def offline_startup():
        return {"llm_live": False, "tavily_live": False}

    monkeypatch.setattr(api, "run_startup_checks", offline_startup)
    response = await api.find_scholarships_endpoint()

    assert response.headers["X-Run-Mode"] == "REPLAY"
    assert response.body
    assert b'"run_mode":"REPLAY"' in response.body
    assert b'"replay_notice":"REPLAY:' in response.body
