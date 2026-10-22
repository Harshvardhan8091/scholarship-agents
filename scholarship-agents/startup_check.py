"""
Startup liveness check.
Validates each configured LLM provider with a tiny call, then checks Tavily.
Prints LIVE MODE when at least one provider works and never prints API keys.
"""
from __future__ import annotations

import asyncio
import sys
from typing import Dict, List, Tuple

if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel

from config import settings

console = Console(force_terminal=True, legacy_windows=False)
STARTUP_LLM_CALL_COUNTS: Dict[str, Dict[str, int]] = {"providers": {}, "models": {}}


def _count_startup_call(provider: str, model: str) -> None:
    STARTUP_LLM_CALL_COUNTS["providers"][provider] = (
        STARTUP_LLM_CALL_COUNTS["providers"].get(provider, 0) + 1
    )
    STARTUP_LLM_CALL_COUNTS["models"][model] = (
        STARTUP_LLM_CALL_COUNTS["models"].get(model, 0) + 1
    )


async def _check_groq() -> Tuple[bool, str]:
    if not settings.GROQ_API_KEY:
        return False, "key not set"
    try:
        from openai import OpenAI

        client = OpenAI(api_key=settings.GROQ_API_KEY, base_url="https://api.groq.com/openai/v1")
        models = [settings.GROQ_MODEL or "openai/gpt-oss-120b"]
        models.extend(
            item.strip()
            for item in settings.GROQ_MODEL_FALLBACKS.split(",")
            if item.strip()
        )
        models = list(dict.fromkeys(models))
        last_reason = "request failed"
        for model_index, model in enumerate(models):
            payload = {
                "model": model,
                "messages": [{"role": "user", "content": "Respond with only OK"}],
                "temperature": 0.0,
            }
            if model.lower().startswith("openai/gpt-oss"):
                payload["reasoning_effort"] = "low"
                payload["max_completion_tokens"] = 4096
            else:
                payload["max_tokens"] = 8

            try:
                try:
                    _count_startup_call("groq", model)
                    response = await asyncio.to_thread(
                        lambda: client.chat.completions.create(**payload)
                    )
                except Exception as exc:
                    unsupported = _unsupported_parameter(
                        str(exc), list(payload.keys())
                    )
                    if not unsupported or unsupported not in payload:
                        raise
                    payload.pop(unsupported)
                    console.print(
                        f"  • recovery: Groq startup request rejected {unsupported!r}; retrying without it",
                        markup=False,
                    )
                    _count_startup_call("groq", model)
                    response = await asyncio.to_thread(
                        lambda: client.chat.completions.create(**payload)
                    )
                text = (
                    getattr(response.choices[0].message, "content", "")
                    if getattr(response, "choices", None)
                    else ""
                )
                if text:
                    return True, model
                last_reason = "empty response"
            except Exception as exc:
                last_reason = _classify_llm_error(str(exc), model)
                if last_reason.startswith("401") or last_reason.startswith("timeout"):
                    return False, last_reason
                if model_index + 1 < len(models):
                    console.print(
                        f"  • recovery: Groq model {model!r} failed ({last_reason}); trying {models[model_index + 1]!r}",
                        markup=False,
                    )
        return False, last_reason
    except Exception as exc:
        return False, _classify_llm_error(str(exc), settings.GROQ_MODEL)


def _unsupported_parameter(error: str, parameters: List[str]) -> str:
    lowered = error.lower()
    if not any(
        phrase in lowered
        for phrase in (
            "unsupported",
            "not supported",
            "unrecognized",
            "unknown parameter",
            "unexpected keyword",
        )
    ):
        return ""
    for parameter in parameters:
        if parameter.lower() in lowered:
            return parameter
    return "unknown"


def _classify_llm_error(error: str, model: str = "") -> str:
    lower = error.lower()
    if "401" in error or "invalid key" in lower or "unauthorized" in lower:
        return "401 unauthorized"
    unsupported = _unsupported_parameter(
        error, ["response_format", "reasoning_effort", "max_completion_tokens", "max_tokens", "response_mime_type"]
    )
    if unsupported:
        return f"unsupported parameter: {unsupported}"
    if "404" in error or ("model" in lower and ("not found" in lower or "does not exist" in lower)):
        return f"model not found: {model!r}"
    if (
        "429" in error
        or "quota" in lower
        or "rate limit" in lower
        or "too many requests" in lower
        or "excessive requests" in lower
        or "blocked due to" in lower
    ):
        return "429 rate limited"
    if "timeout" in lower or "timed out" in lower:
        return "timeout"
    return "request failed"


async def _check_gemini() -> Tuple[bool, str]:
    if not settings.GEMINI_API_KEY:
        return False, "key not set"
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=settings.GEMINI_API_KEY)
        models_to_try = [settings.GEMINI_MODEL]
        if settings.GEMINI_MODEL_FALLBACKS:
            for fb in settings.GEMINI_MODEL_FALLBACKS.split(","):
                fb = fb.strip()
                if fb and fb not in models_to_try:
                    models_to_try.append(fb)

        last_reason = "request failed"
        for model_index, model_name in enumerate(models_to_try):
            try:
                def _ping(model_name=model_name):
                    _count_startup_call("gemini", model_name)
                    return client.models.generate_content(
                        model=model_name,
                        contents="Respond with the single word OK",
                        config=types.GenerateContentConfig(
                            temperature=0.0,
                            max_output_tokens=5,
                        ),
                    )

                resp = await asyncio.to_thread(_ping)
                if resp and getattr(resp, "text", None):
                    return True, model_name
                last_reason = "empty response"
            except Exception as e:
                last_reason = _classify_llm_error(str(e), model_name)
                if last_reason.startswith("401") or last_reason == "timeout":
                    return False, last_reason
                if model_index + 1 < len(models_to_try):
                    console.print(
                        f"  • recovery: Gemini model {model_name!r} failed ({last_reason}); trying {models_to_try[model_index + 1]!r}",
                        markup=False,
                    )
        return False, last_reason
    except Exception as e:
        return False, _classify_llm_error(str(e), settings.GEMINI_MODEL)



async def run_startup_checks() -> Dict[str, object]:
    STARTUP_LLM_CALL_COUNTS["providers"].clear()
    STARTUP_LLM_CALL_COUNTS["models"].clear()
    configured_providers = [p.strip().lower() for p in settings.LLM_PROVIDERS.split(",") if p.strip()]
    if not configured_providers:
        configured_providers = ["groq", "gemini"]

    working_providers: List[str] = []
    provider_models: Dict[str, str] = {}
    status_lines: List[str] = []

    for provider in configured_providers:
        if provider == "groq":
            model_ids = [
                settings.GROQ_MODEL or "openai/gpt-oss-120b",
                *settings.GROQ_MODEL_FALLBACKS.split(","),
            ]
        elif provider == "gemini":
            model_ids = [settings.GEMINI_MODEL, *settings.GEMINI_MODEL_FALLBACKS.split(",")]
        else:
            continue
        model_ids = list(dict.fromkeys(model.strip() for model in model_ids if model.strip()))
        model_id_summary = ", ".join(escape(repr(model_id)) for model_id in model_ids)
        for model_id in filter(None, (model.strip() for model in model_ids)):
            if any(char.isspace() for char in model_id):
                status_lines.append(f"  • WARNING: {provider} model ID contains whitespace: {escape(repr(model_id))}")
            if not model_id.isascii():
                status_lines.append(f"  • WARNING: {provider} model ID contains non-ASCII characters: {escape(repr(model_id))}")
        if provider == "groq" and not settings.GROQ_API_KEY:
            status_lines.append(
                f"  • groq skipped: key not set (configured models: {model_id_summary})"
            )
            continue
        if provider == "gemini" and not settings.GEMINI_API_KEY:
            status_lines.append(
                f"  • gemini skipped: key not set (configured models: {model_id_summary})"
            )
            continue
        if provider == "groq":
            ok, detail = await _check_groq()
        elif provider == "gemini":
            ok, detail = await _check_gemini()
        else:
            continue
        if ok:
            working_providers.append(provider)
            provider_models[provider] = detail
            status_lines.append(f"  • {provider}: answered by model {escape(repr(detail))}")
        else:
            status_lines.append(
                f"  • {provider}: offline — {detail} (configured models: {model_id_summary})"
            )

    provider_call_summary = ", ".join(
        f"{name}={count}"
        for name, count in sorted(STARTUP_LLM_CALL_COUNTS["providers"].items())
    ) or "none"
    model_call_summary = ", ".join(
        f"{escape(repr(name))}={count}"
        for name, count in sorted(STARTUP_LLM_CALL_COUNTS["models"].items())
    ) or "none"
    status_lines.append(f"  • Startup LLM provider calls: {provider_call_summary}")
    status_lines.append(f"  • Startup LLM model calls: {model_call_summary}")

    # Tavily still checked separately for the pipeline's live mode gating
    tavily_live = False
    tavily_reason = "TAVILY_API_KEY is not set"
    if settings.TAVILY_API_KEY:
        try:
            from tavily import TavilyClient

            tc = TavilyClient(api_key=settings.TAVILY_API_KEY)

            def _ping_tavily():
                return tc.search(query="test", max_results=1)

            res = await asyncio.to_thread(_ping_tavily)
            if res and "results" in res:
                tavily_live = True
                tavily_reason = "connected"
        except Exception as e:
            tavily_reason = _classify_llm_error(str(e))

    if working_providers:
        settings.LLM_PROVIDERS = ",".join(working_providers)
        console.print(
            Panel.fit(
                "[bold green]✅ LIVE MODE[/bold green]\n"
                + ("\n".join(status_lines) if status_lines else "")
                + (
                    f"\n  Search: [green]{tavily_reason}[/green]"
                    if tavily_live
                    else f"\n  Search: [yellow]offline — {tavily_reason}[/yellow]"
                ),
                border_style="green",
                title="Startup Check",
            )
        )
    else:
        lines = ["[bold yellow]⚠️ OFFLINE FALLBACK MODE[/bold yellow]"]
        lines.extend(status_lines)
        lines.append(f"  Search: [yellow]{tavily_reason}[/yellow]")
        console.print(
            Panel.fit(
                "\n".join(lines),
                border_style="yellow",
                title="Startup Check",
            )
        )

    result = {
        "llm_live": bool(working_providers),
        "gemini_live": "gemini" in working_providers,
        "tavily_live": tavily_live,
        "working_providers": working_providers,
        "provider_models": provider_models,
        "model": provider_models.get("gemini") or provider_models.get("groq") or "",
        "llm_provider_calls": dict(STARTUP_LLM_CALL_COUNTS["providers"]),
        "llm_model_calls": dict(STARTUP_LLM_CALL_COUNTS["models"]),
    }
    return result


if __name__ == "__main__":
    asyncio.run(run_startup_checks())
