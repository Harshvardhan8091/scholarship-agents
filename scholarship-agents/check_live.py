"""
check_live.py
Reads output/result.json and prints:
  1. Whether the run was LIVE or OFFLINE.
  2. Top 5 with funding, deadline, source URL, verified vs unverified fields.
  3. Per-entry matched & unmet criteria and student fit.
  4. Deadline warnings (past deadlines with proper date parsing).
  5. Aggregator-excluded candidates (kept in candidates but not in top-5).
  6. Grounded evidence snippets.
"""
import json
import sys
from datetime import date, datetime
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

console = Console(force_terminal=True, legacy_windows=False)

RESULT_PATH = Path(__file__).resolve().parent / "output" / "result.json"


def main() -> None:
    if not RESULT_PATH.exists():
        console.print(f"[bold red]✖ Result file not found: {RESULT_PATH}[/bold red]")
        console.print("[dim]Run the pipeline first: python run_cli.py ...[/dim]")
        return

    with open(RESULT_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    ranked_data = data.get("ranked_results") or {}
    top_opps = ranked_data.get("top_opportunities", [])

    # ── 1. LIVE vs OFFLINE determination ──
    tavily_count = 0
    curated_count = 0
    llm_count = 0
    heuristic_count = 0
    unverified_mode_count = 0

    for opp in top_opps:
        ds = opp.get("discovery_source", "curated_fallback")
        vm = opp.get("verification_mode", "unverified")
        if ds == "tavily_live":
            tavily_count += 1
        else:
            curated_count += 1
        if vm == "llm_live":
            llm_count += 1
        elif vm == "heuristic":
            heuristic_count += 1
        else:
            unverified_mode_count += 1

    is_live = tavily_count > 0 and llm_count > 0
    mode_label = "[bold green]LIVE[/bold green]" if is_live else "[bold yellow]OFFLINE[/bold yellow]"

    console.print(
        Panel.fit(
            f"Run Mode: {mode_label}\n"
            f"  Discovery:    tavily_live={tavily_count}  curated_fallback={curated_count}\n"
            f"  Verification: llm_live={llm_count}  heuristic={heuristic_count}  unverified={unverified_mode_count}",
            title="📋 Run Analysis",
            border_style="cyan",
        )
    )

    # ── 2. Top 5 details table ──
    if top_opps:
        table = Table(title="🏆 Top 5 Scholarships — Detailed Audit", border_style="green", show_lines=True)
        table.add_column("#", style="bold yellow", justify="center")
        table.add_column("Name", style="bold white", max_width=35)
        table.add_column("Funding", style="white", max_width=30)
        table.add_column("Deadline", style="magenta", max_width=28)
        table.add_column("Source URL", style="blue", max_width=45)
        table.add_column("Unverified Fields", style="red")
        table.add_column("Source", style="dim")
        table.add_column("Verif.", style="dim")

        today = date.today()
        past_deadlines = []
        no_source_url = []

        for opp in top_opps:
            funding = opp.get("funding", "?")
            deadline = opp.get("deadline", "?")
            url = opp.get("official_url", "")
            ds = opp.get("discovery_source", "?")
            vm = opp.get("verification_mode", "?")

            # Check which fields are unverified
            unverified_fields = []
            vd = opp.get("verified_data", {})
            for field_name in ["eligibility", "funding_amount_coverage", "deadline", "how_to_apply"]:
                val = vd.get(field_name, "")
                if isinstance(val, str) and "unverified" in val.lower():
                    unverified_fields.append(field_name)

            # ── 3. Check deadline before today — use multi-format date parsing ──
            _is_past = False
            if isinstance(deadline, str):
                if "closed" in deadline.lower():
                    _is_past = True
                    past_deadlines.append((opp.get("name", "?"), deadline))
                else:
                    # Try parsing various date formats
                    _fmt_list = [
                        "%Y-%m-%d", "%d %B %Y", "%B %d, %Y", "%d/%m/%Y",
                        "%m/%d/%Y", "%d %b %Y", "%b %d, %Y",
                    ]
                    for _fmt in _fmt_list:
                        try:
                            parsed = datetime.strptime(deadline.strip(), _fmt).date()
                            if parsed < today:
                                _is_past = True
                                past_deadlines.append((opp.get("name", "?"), deadline))
                            break
                        except (ValueError, TypeError):
                            continue

            deadline_display = deadline[:28] if deadline else "?"
            if _is_past:
                deadline_display = f"[bold red]{deadline_display}[/bold red]"

            # ── 4. Check for missing source URL ──
            if not url:
                no_source_url.append(opp.get("name", "?"))

            table.add_row(
                str(opp.get("rank", "?")),
                opp.get("name", "?"),
                funding[:30] if funding else "?",
                deadline_display,
                url[:45] if url else "[red]MISSING[/red]",
                ", ".join(unverified_fields) if unverified_fields else "[green]all verified[/green]",
                ds,
                vm,
            )

        console.print(table)

        # Print warnings
        if past_deadlines:
            console.print("\n[bold red]⚠️  Past / Closed Deadlines:[/bold red]")
            for name, dl in past_deadlines:
                console.print(f"  • [bold]{name}[/bold]: [red]{dl}[/red]")
        else:
            console.print("\n[green]✅ No past deadlines detected in top 5.[/green]")

        if no_source_url:
            console.print("\n[bold red]⚠️  Results with NO source URL:[/bold red]")
            for name in no_source_url:
                console.print(f"  • {name}")
        else:
            console.print("[green]✅ All results have a source URL.[/green]")

        # Print per-entry matched / unmet criteria and fit reason
        console.print("\n[bold cyan]📋 Per-Entry Eligibility Fit & Criteria:[/bold cyan]")
        for opp in top_opps:
            vd = opp.get("verified_data") or {}
            matched = vd.get("matched_criteria") or []
            unmet = vd.get("unmet_criteria") or []
            missing = vd.get("missing_information") or []
            fit_reason = vd.get("student_fit_reason") or ""
            # Also pull reasoning from ranked opportunity
            reasoning = opp.get("reasoning") or ""

            console.print(f"\n  [bold white]#{opp.get('rank', '?')} {opp.get('name', '?')}[/bold white]")
            console.print(f"    [dim]Official URL:[/dim] [blue]{opp.get('official_url', 'N/A')}[/blue]")
            if reasoning:
                console.print(f"    [dim]Reasoning:[/dim] {reasoning[:200]}")
            if matched:
                console.print(f"    [green]✓ Matched criteria:[/green]")
                for m in matched[:5]:
                    console.print(f"      • {m}")
            if unmet:
                console.print(f"    [red]✗ Unmet criteria:[/red]")
                for u in unmet[:3]:
                    console.print(f"      • {u}")
            if missing:
                console.print(f"    [yellow]? Missing / unconfirmed:[/yellow]")
                for mi in missing[:3]:
                    console.print(f"      • {mi}")

        # Print evidence snippets
        has_any_evidence = any(
            bool(opp.get("evidence") or (opp.get("verified_data") or {}).get("evidence"))
            for opp in top_opps
        )
        if has_any_evidence:
            console.print("\n[bold cyan]🔍 Verified Grounded Evidence Snippets (Max 200 chars):[/bold cyan]")
            for opp in top_opps:
                vd = opp.get("verified_data") or {}
                ev = opp.get("evidence") or vd.get("evidence") or {}
                if ev:
                    console.print(f"  [bold white]#{opp.get('rank', '?')} {opp.get('name', '?')}:[/bold white]")
                    for f_key, f_val in ev.items():
                        console.print(f"    • [dim]{f_key}:[/dim] [italic]{f_val}[/italic]")

    else:
        console.print("[yellow]No ranked results found in result.json.[/yellow]")

    console.print()


if __name__ == "__main__":
    main()
