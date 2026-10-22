import pytest
import httpx
import api
from api import app

@pytest.mark.asyncio
async def test_health_check_endpoint():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "online"
        assert "gemini_model" in data


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
