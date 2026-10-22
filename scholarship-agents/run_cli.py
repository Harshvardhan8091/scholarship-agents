import argparse
import asyncio
import json
import os
import shutil
import sys
from pathlib import Path

if sys.platform == "win32":
    os.environ.setdefault("PYTHONUTF8", "1")
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
from rich.table import Table

from config import settings
from pipeline import run_pipeline
from startup_check import run_startup_checks

console = Console(force_terminal=True, legacy_windows=False)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="AI Scholarship Finder - Autonomous Multi-Agent Academic Pipeline",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--resume",
        type=str,
        required=False,
        default=None,
        help="Path to student's resume PDF",
    )
    parser.add_argument(
        "--marksheet",
        type=str,
        required=False,
        default=None,
        help="Path to marksheet (PDF or TXT file)",
    )
    parser.add_argument(
        "--github",
        type=str,
        required=False,
        default=None,
        help="Student's public GitHub username",
    )
    parser.add_argument(
        "--pick",
        type=int,
        default=1,
        help="Index of ranked opportunity (1-5) to draft Statement of Purpose for",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=str(settings.OUTPUT_DIR / "result.json"),
        help="Path where output JSON result should be saved",
    )
    parser.add_argument(
        "--require-live",
        action="store_true",
        default=False,
        help="Fail loudly if the run is OFFLINE",
    )
    parser.add_argument(
        "--save-demo",
        action="store_true",
        help="Copy a successful live result to samples/demo_result.json",
    )
    return parser.parse_args()


async def main_async() -> None:
    args = parse_args()

    console.print(
        Panel.fit(
            "[bold cyan]🎓 AI Scholarship & University Program Finder[/bold cyan]\n"
            "[dim]Powered by Google Gemini, Tavily, and LangGraph Multi-Agent Team[/dim]",
            border_style="cyan",
        )
    )

    # Item 1 & 5: Startup liveness check
    startup_res = await run_startup_checks()
    working_llm_providers = startup_res.get("working_providers") or []
    is_startup_live = bool(working_llm_providers)

    if args.require_live and not is_startup_live:
        console.print(
            Panel.fit(
                "[bold red]❌ FATAL ERROR: --require-live was specified, but system startup check failed for LIVE mode![/bold red]\n"
                f"  Working LLM Providers: {', '.join(working_llm_providers) if working_llm_providers else 'none'}\n"
                f"  Tavily Live: {startup_res.get('tavily_live')}",
                border_style="red",
                title="Live Mode Required",
            )
        )
        sys.exit(1)

    if not startup_res.get("tavily_live"):
        settings.TAVILY_API_KEY = ""
        console.print(
            "[yellow]Search API unavailable; skipping repeated search requests and using curated candidates with live page verification.[/yellow]"
        )

    inputs = {
        "resume_path": args.resume,
        "marksheet_path": args.marksheet,
        "github_username": args.github,
        "chosen_pick": args.pick,
    }

    result = await run_pipeline(inputs, print_to_terminal=True)
    result["startup_llm_provider_calls"] = startup_res.get("llm_provider_calls", {})
    result["startup_llm_model_calls"] = startup_res.get("llm_model_calls", {})

    # 1. Print Student Profile Summary
    profile = result.get("student_profile")
    if profile:
        prof_table = Table(title="📋 Extracted Student Profile", border_style="magenta")
        prof_table.add_column("Field", style="bold cyan")
        prof_table.add_column("Value", style="white")

        prof_table.add_row("Name", str(profile.get("name") or "Unknown"))
        prof_table.add_row("Current Degree", str(profile.get("degree") or "N/A"))
        prof_table.add_row("Target Degree Level", str(profile.get("target_degree_level") or "Master's"))
        prof_table.add_row("Academic Field", str(profile.get("field") or "N/A"))
        prof_table.add_row("CGPA / Grades", str(profile.get("cgpa") or "N/A"))
        prof_table.add_row("Top Skills", ", ".join(profile.get("skills", [])[:8]) or "None")
        prof_table.add_row("Target Countries", ", ".join(profile.get("target_countries", [])) or "None specified")
        prof_table.add_row("Financial Need", str(profile.get("financial_need") or "Unstated"))
        if profile.get("github_bio"):
            prof_table.add_row("GitHub Bio", str(profile.get("github_bio")))
        if profile.get("gaps_or_missing_info"):
            prof_table.add_row(
                "Missing Info / Gaps",
                "\n".join(f"• {g}" for g in profile.get("gaps_or_missing_info", [])),
            )
        console.print(prof_table)

    # 2. Print Top Ranked Opportunities
    ranked_data = result.get("ranked_results")
    if ranked_data and ranked_data.get("top_opportunities"):
        opps_table = Table(title="🏆 Top 5 Verified Scholarships / Programs", border_style="green")
        opps_table.add_column("Rank", style="bold yellow", justify="center")
        opps_table.add_column("Scholarship & Provider", style="bold white")
        opps_table.add_column("Country", style="cyan")
        opps_table.add_column("Degree", style="cyan")
        opps_table.add_column("Fit Score", style="bold green", justify="center")
        opps_table.add_column("Official URL", style="blue")
        opps_table.add_column("Verified vs Unverified", style="white")
        opps_table.add_column("Matched / Unmet", style="dim")
        opps_table.add_column("Why It Fits", style="dim")

        for opp in ranked_data["top_opportunities"]:
            verified_data = opp.get("verified_data") or {}
            matched = verified_data.get("matched_criteria") or []
            unmet = verified_data.get("unmet_criteria") or []
            fields = [
                key for key, value in verified_data.items()
                if key in {"eligibility", "funding_amount_coverage", "deadline", "how_to_apply"}
                and isinstance(value, str)
                and "unverified" not in value.lower()
            ]
            if not fields:
                fields = ["unverified: eligibility, funding, deadline, how_to_apply"]
            matched_summary = "; ".join(str(v) for v in matched[:3]) if matched else "—"
            unmet_summary = "; ".join(str(v) for v in unmet[:3]) if unmet else "—"
            fit_reason = opp.get("reasoning") or verified_data.get("student_fit_reason") or "—"

            opps_table.add_row(
                f"#{opp['rank']}",
                f"{opp['name']}\n[dim]{opp['provider']}[/dim]",
                opp["country"],
                (opp.get("degree_level") or "unverified")[:16],
                f"{opp['fit_score']}%",
                opp.get("official_url", "N/A"),
                "; ".join(fields)[:120],
                f"Matched: {matched_summary[:80]}\nUnmet: {unmet_summary[:80]}",
                fit_reason[:180],
            )
        console.print(opps_table)

    # 3. Print Statement of Purpose Draft
    sop = result.get("sop_draft")
    if sop:
        console.print(
            Panel(
                sop["sop_markdown"],
                title=f"[bold yellow]📝 Statement of Purpose Draft (Pick #{args.pick}: {sop['opportunity_name']})[/bold yellow]",
                border_style="yellow",
                expand=False,
            )
        )

    # 4. Final Run Summary (counts of llm_live vs heuristic)
    top_opps = (ranked_data.get("top_opportunities") or []) if ranked_data else []
    llm_live_count = sum(1 for o in top_opps if o.get("verification_mode") == "llm_live")
    heuristic_count = sum(1 for o in top_opps if o.get("verification_mode") == "heuristic")
    unverified_count = sum(1 for o in top_opps if o.get("verification_mode") == "unverified")

    # Count aggregator-excluded candidates from verified pool (stored in all_verified if present)
    all_verified = result.get("all_verified_opportunities", [])
    aggregator_excluded_count = sum(
        1 for o in all_verified
        if o.get("match_status") == "aggregator_excluded"
    )

    # Count closed deadlines
    closed_deadline_count = sum(
        1 for o in top_opps
        if isinstance(o.get("deadline"), str) and "closed" in o.get("deadline", "").lower()
    )

    is_run_live = llm_live_count > 0 and is_startup_live
    status_label = "[bold green]LIVE[/bold green]" if is_run_live else "[bold yellow]OFFLINE[/bold yellow]"
    provider_counts = result.get("llm_provider_calls", {}) or {}
    model_counts = result.get("llm_model_calls", {}) or {}
    startup_provider_counts = result.get("startup_llm_provider_calls", {}) or {}
    startup_model_counts = result.get("startup_llm_model_calls", {}) or {}
    provider_summary = ", ".join(f"{name}={count}" for name, count in sorted(provider_counts.items())) or "none"
    model_summary = ", ".join(f"{escape(repr(name))}={count}" for name, count in sorted(model_counts.items())) or "none"
    startup_provider_summary = ", ".join(
        f"{name}={count}" for name, count in sorted(startup_provider_counts.items())
    ) or "none"
    startup_model_summary = ", ".join(
        f"{escape(repr(name))}={count}" for name, count in sorted(startup_model_counts.items())
    ) or "none"

    agg_line = f"  \u2022 aggregator_excluded: [dim]{aggregator_excluded_count}[/dim] (kept in candidates, NOT in top-5)\n" if aggregator_excluded_count else ""
    closed_line = f"  \u2022 [bold red]closed deadlines: {closed_deadline_count}[/bold red]\n" if closed_deadline_count else ""

    summary_panel = Panel.fit(
        f"[bold]Startup Model Answered:[/bold] [bold cyan]{escape(repr(startup_res.get('model') or 'none'))}[/bold cyan]\n"
        f"[bold]Verification Breakdown (Top 5):[/bold]\n"
        f"  \u2022 llm_live:   [bold green]{llm_live_count}[/bold green]\n"
        f"  \u2022 heuristic:  [bold yellow]{heuristic_count}[/bold yellow]\n"
        f"  \u2022 unverified: [dim]{unverified_count}[/dim]\n"
        f"{agg_line}"
        f"{closed_line}"
        f"[bold]Startup provider/model calls:[/bold] {startup_provider_summary} / {startup_model_summary}\n"
        f"[bold]LLM provider calls:[/bold] {provider_summary}\n"
        f"[bold]LLM model calls:[/bold] {model_summary}\n"
        f"[bold]Run Status:[/bold]           {status_label}",
        title="\U0001f3af Final Run Verification Audit",
        border_style="green" if is_run_live else "yellow",
    )
    console.print(summary_panel)

    # 5. Save to JSON file
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    console.print(f"\n[bold green]✔ Full JSON results successfully saved to:[/bold green] {output_path.resolve()}\n")

    if args.save_demo:
        if is_run_live:
            demo_path = settings.OUTPUT_DIR.parent / "samples" / "demo_result.json"
            demo_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(output_path, demo_path)
            console.print(
                f"[bold green]✔ Successful live result copied to:[/bold green] {demo_path.resolve()}"
            )
        else:
            console.print(
                "[bold yellow]Demo result not saved: this run did not complete with llm_live greater than zero.[/bold yellow]"
            )

    # Item 5: Fail loudly if run is OFFLINE and --require-live is passed
    if args.require_live and not is_run_live:
        console.print(
            "[bold red]❌ FATAL ERROR: Run completed in OFFLINE mode (llm_live=0) while --require-live was specified![/bold red]"
        )
        sys.exit(1)


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
