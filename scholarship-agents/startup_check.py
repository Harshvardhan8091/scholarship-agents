"""
Startup liveness check (Item 1).
Makes one tiny test call to Gemini and one to Tavily.
Prints LIVE MODE or OFFLINE FALLBACK MODE with reasons.
Never prints API keys.
"""
from __future__ import annotations

import asyncio
import sys
from typing import Dict

if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from rich.console import Console
from rich.panel import Panel

from config import settings

console = Console(force_terminal=True, legacy_windows=False)


async def run_startup_checks() -> Dict[str, bool]:
    """
    Returns {"gemini_live": bool, "tavily_live": bool}.
    Prints a clear LIVE / OFFLINE banner.
    """
    gemini_live = False
    tavily_live = False
    gemini_reason = ""
    tavily_reason = ""

    # ── Gemini check ──
    if not settings.GEMINI_API_KEY:
        gemini_reason = "GEMINI_API_KEY is not set"
    else:
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

            active_model = settings.GEMINI_MODEL
            for m in models_to_try:
                try:
                    def _ping(model_name=m):
                        return client.models.generate_content(
                            model=model_name,
                            contents="Respond with the single word OK",
                            config=types.GenerateContentConfig(
                                temperature=0.0,
                                max_output_tokens=5,
                            ),
                        )

                    resp = await asyncio.to_thread(_ping)
                    if resp and resp.text:
                        gemini_live = True
                        active_model = m
                        break
                    else:
                        gemini_reason = f"Gemini returned empty response for {m}"
                except Exception as e:
                    gemini_reason = f"{m} failed: {str(e)[:120]}"
            chosen_model = active_model
        except Exception as e:
            gemini_reason = f"Gemini init failed: {str(e)[:120]}"
            chosen_model = settings.GEMINI_MODEL

    # ── Tavily check ──
    if not settings.TAVILY_API_KEY:
        tavily_reason = "TAVILY_API_KEY is not set"
    else:
        try:
            from tavily import TavilyClient

            tc = TavilyClient(api_key=settings.TAVILY_API_KEY)

            def _ping_tavily():
                return tc.search(query="test", max_results=1)

            res = await asyncio.to_thread(_ping_tavily)
            if res and "results" in res:
                tavily_live = True
            else:
                tavily_reason = "Tavily returned unexpected response"
        except Exception as e:
            tavily_reason = f"Tavily test call failed: {str(e)[:120]}"

    # ── Print banner ──
    both_live = gemini_live and tavily_live
    display_model = chosen_model if 'chosen_model' in locals() else settings.GEMINI_MODEL
    if both_live:
        console.print(
            Panel.fit(
                "[bold green]✅ LIVE MODE[/bold green]\n"
                f"  Exact Model in use: [bold cyan]{display_model}[/bold cyan] ([green]connected[/green])\n"
                f"  Tavily Search:      [green]connected[/green]",
                border_style="green",
                title="Startup Check",
            )
        )
    else:
        lines = ["[bold yellow]⚠️  OFFLINE FALLBACK MODE[/bold yellow]"]
        lines.append(f"  Exact Model in use: [bold cyan]{display_model}[/bold cyan]")
        if gemini_live:
            lines.append("  Gemini API:         [green]connected[/green]")
        else:
            lines.append(f"  Gemini API:         [red]offline[/red] — {gemini_reason}")
        if tavily_live:
            lines.append("  Tavily Search:      [green]connected[/green]")
        else:
            lines.append(f"  Tavily Search:      [red]offline[/red] — {tavily_reason}")
        console.print(
            Panel.fit(
                "\n".join(lines),
                border_style="yellow",
                title="Startup Check",
            )
        )

    return {"gemini_live": gemini_live, "tavily_live": tavily_live, "model": display_model}


if __name__ == "__main__":
    asyncio.run(run_startup_checks())
