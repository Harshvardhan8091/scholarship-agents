"""
run_all_profiles.py (Item 7)
Runs the pipeline for all 3 fictional profiles and prints a comparison table
showing the top 5 differ by field, CGPA, and target country.
"""
import asyncio
import json
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

PROFILES = [
    {
        "label": "CS Student (High CGPA)",
        "resume": "samples/sample_resume.pdf",
        "marksheet": "samples/sample_marksheet.pdf",
        "github": "alexrivera-dev",
    },
    {
        "label": "Mech Engineering (Lower CGPA)",
        "resume": "samples/mech_resume.pdf",
        "marksheet": "samples/mech_marksheet.pdf",
        "github": "priyamehta-eng",
    },
    {
        "label": "Commerce / Business",
        "resume": "samples/biz_resume.pdf",
        "marksheet": "samples/biz_marksheet.pdf",
        "github": "",
    },
]


async def main() -> None:
    console.print(
        Panel.fit(
            "[bold cyan]🎓 Multi-Profile Comparison Run[/bold cyan]\n"
            "[dim]Running 3 different student profiles through the pipeline[/dim]",
            border_style="cyan",
        )
    )

    await run_startup_checks()

    all_results = []

    for i, prof in enumerate(PROFILES, 1):
        console.print(f"\n[bold yellow]═══ Profile {i}/{len(PROFILES)}: {prof['label']} ═══[/bold yellow]")
        inputs = {
            "resume_path": prof["resume"],
            "marksheet_path": prof["marksheet"],
            "github_username": prof["github"] or None,
            "chosen_pick": 1,
        }
        result = await run_pipeline(inputs, print_to_terminal=True)
        all_results.append((prof, result))

        # Save individual result
        out_path = settings.OUTPUT_DIR / f"result_profile_{i}.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        console.print(f"[dim]Saved: {out_path}[/dim]")

    # ── Comparison Table ──
    console.print("\n")
    cmp_table = Table(
        title="📊 Top-5 Comparison Across Profiles",
        border_style="cyan",
        show_lines=True,
    )
    cmp_table.add_column("Profile", style="bold magenta")
    cmp_table.add_column("Field", style="cyan")
    cmp_table.add_column("CGPA", style="green")
    cmp_table.add_column("Countries", style="yellow")
    cmp_table.add_column("Top 5 Scholarships", style="white")

    for prof, result in all_results:
        profile_data = result.get("student_profile") or {}
        ranked = result.get("ranked_results") or {}
        top_opps = ranked.get("top_opportunities", [])

        field = profile_data.get("field", "N/A")
        cgpa = str(profile_data.get("cgpa", "N/A"))
        countries = ", ".join(profile_data.get("target_countries", [])) or "Not specified"

        opp_lines = []
        for opp in top_opps[:5]:
            src = opp.get("discovery_source", "?")
            ver = opp.get("verification_mode", "?")
            opp_lines.append(
                f"#{opp['rank']} {opp['name'][:45]}\n"
                f"    [{opp['country']}] Score:{opp['fit_score']}% | {src}/{ver}"
            )
        opps_str = "\n".join(opp_lines) if opp_lines else "No results"

        cmp_table.add_row(
            prof["label"],
            field or "N/A",
            cgpa,
            countries,
            opps_str,
        )

    console.print(cmp_table)
    console.print("[bold green]✔ Multi-profile comparison complete![/bold green]\n")


if __name__ == "__main__":
    asyncio.run(main())
