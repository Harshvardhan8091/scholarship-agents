import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

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
    is_startup_live = bool(startup_res.get("gemini_live") and startup_res.get("tavily_live"))

    if args.require_live and not is_startup_live:
        console.print(
            Panel.fit(
                "[bold red]❌ FATAL ERROR: --require-live was specified, but system startup check failed for LIVE mode![/bold red]\n"
                f"  Exact Model: {settings.GEMINI_MODEL}\n"
                f"  Gemini Live: {startup_res.get('gemini_live')} | Tavily Live: {startup_res.get('tavily_live')}",
                border_style="red",
                title="Live Mode Required",
            )
        )
        sys.exit(1)

    inputs = {
        "resume_path": args.resume,
        "marksheet_path": args.marksheet,
        "github_username": args.github,
        "chosen_pick": args.pick,
    }

    result = await run_pipeline(inputs, print_to_terminal=True)

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
        opps_table.add_column("Fit Score", style="bold green", justify="center")
        opps_table.add_column("Funding", style="white")
        opps_table.add_column("Deadline", style="magenta")
        opps_table.add_column("Source", style="dim")
        opps_table.add_column("Verification", style="dim")
        opps_table.add_column("Official Source URL", style="blue")

        for opp in ranked_data["top_opportunities"]:
            opps_table.add_row(
                f"#{opp['rank']}",
                f"{opp['name']}\n[dim]{opp['provider']}[/dim]",
                opp["country"],
                f"{opp['fit_score']}%",
                opp["funding"][:40],
                opp["deadline"][:30],
                opp.get("discovery_source", "unknown"),
                opp.get("verification_mode", "unknown"),
                opp["official_url"],
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

    agg_line = f"  \u2022 aggregator_excluded: [dim]{aggregator_excluded_count}[/dim] (kept in candidates, NOT in top-5)\n" if aggregator_excluded_count else ""
    closed_line = f"  \u2022 [bold red]closed deadlines: {closed_deadline_count}[/bold red]\n" if closed_deadline_count else ""

    summary_panel = Panel.fit(
        f"[bold]Exact Model in Use:[/bold]  [bold cyan]{settings.GEMINI_MODEL}[/bold cyan]\n"
        f"[bold]Verification Breakdown (Top 5):[/bold]\n"
        f"  \u2022 llm_live:   [bold green]{llm_live_count}[/bold green]\n"
        f"  \u2022 heuristic:  [bold yellow]{heuristic_count}[/bold yellow]\n"
        f"  \u2022 unverified: [dim]{unverified_count}[/dim]\n"
        f"{agg_line}"
        f"{closed_line}"
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
