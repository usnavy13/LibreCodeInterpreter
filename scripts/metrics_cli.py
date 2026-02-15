#!/usr/bin/env python3
"""
Metrics CLI - Interactive dashboard for execution metrics.

Usage:
  python scripts/metrics_cli.py              # Interactive mode
  python scripts/metrics_cli.py summary      # Quick summary
  python scripts/metrics_cli.py watch        # Auto-refresh dashboard

Commands:
  (no args)    Interactive menu
  summary      Show metrics summary
  languages    Per-language breakdown
  pool         Sandbox pool stats
  watch        Auto-refresh dashboard
"""

import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, List

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

# Load .env file if it exists
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich.prompt import Prompt
from rich import box

from src.services.metrics import metrics_service

console = Console()


async def ensure_started():
    """Start the metrics service if not already running."""
    if not metrics_service._running:
        await metrics_service.start()


def format_duration(ms: float) -> str:
    """Format milliseconds to human readable."""
    if ms < 1000:
        return f"{ms:.0f}ms"
    elif ms < 60000:
        return f"{ms/1000:.2f}s"
    else:
        return f"{ms/60000:.1f}m"


def format_rate(rate: float, good_threshold: float = 80, bad_threshold: float = 50) -> Text:
    """Format percentage with color coding."""
    text = f"{rate:.1f}%"
    if rate >= good_threshold:
        return Text(text, style="green")
    elif rate >= bad_threshold:
        return Text(text, style="yellow")
    else:
        return Text(text, style="red")


async def build_summary_panel(hours: int = 24) -> Panel:
    """Build summary panel from SQLite data."""
    now = datetime.now(timezone.utc)
    start = now - timedelta(hours=hours)
    summary = await metrics_service.get_summary_stats(start=start, end=now)

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="white")

    table.add_row("Total Executions", str(summary.get("total_executions", 0)))
    table.add_row("Success Rate", format_rate(summary.get("success_rate", 0)))
    table.add_row("Avg Exec Time", format_duration(summary.get("avg_execution_time_ms", 0)))
    table.add_row("Pool Hit Rate", format_rate(summary.get("pool_hit_rate", 0)))
    table.add_row("Active API Keys", str(summary.get("active_api_keys", 0)))

    return Panel(table, title=f"[bold]Summary[/bold] (last {hours}h)", border_style="blue")


async def build_languages_table(hours: int = 24) -> Table:
    """Build languages table from SQLite data."""
    now = datetime.now(timezone.utc)
    start = now - timedelta(hours=hours)
    lang_data = await metrics_service.get_language_usage(start=start, end=now)
    by_language = lang_data.get("by_language", {})

    table = Table(title=f"Language Metrics (last {hours}h)", box=box.ROUNDED)
    table.add_column("Language", style="cyan", justify="center")
    table.add_column("Executions", justify="right")

    sorted_langs = sorted(by_language.items(), key=lambda x: x[1], reverse=True)

    for lang, count in sorted_langs:
        table.add_row(lang.upper(), str(count))

    if not sorted_langs:
        table.add_row("[dim]No data[/dim]", "")

    return table


async def build_pool_panel() -> Panel:
    """Build pool stats panel from in-memory data."""
    pool_stats = metrics_service.get_pool_stats()

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="white")

    table.add_row("Total Acquisitions", str(pool_stats["total_acquisitions"]))
    table.add_row("Pool Hits", Text(str(pool_stats["pool_hits"]), style="green"))
    table.add_row("Pool Misses", Text(str(pool_stats["pool_misses"]), style="yellow"))
    table.add_row("Hit Rate", format_rate(pool_stats["hit_rate"]))
    table.add_row("Avg Acquire Time", format_duration(pool_stats["avg_acquire_time_ms"]))
    table.add_row("Exhaustion Events", Text(
        str(pool_stats["exhaustion_events"]),
        style="red" if pool_stats["exhaustion_events"] > 0 else "green"
    ))

    return Panel(table, title="[bold]Sandbox Pool[/bold]", border_style="magenta")


async def cmd_summary(args):
    """Show summary."""
    await ensure_started()
    panel = await build_summary_panel(getattr(args, "hours", 24))
    console.print()
    console.print(panel)
    console.print()


async def cmd_languages(args):
    """Show per-language metrics."""
    await ensure_started()
    table = await build_languages_table(args.hours)
    console.print()
    console.print(table)
    console.print()


async def cmd_pool(args):
    """Show pool stats."""
    await ensure_started()
    panel = await build_pool_panel()
    console.print()
    console.print(panel)
    console.print()


async def cmd_watch(args):
    """Auto-refresh dashboard."""
    await ensure_started()

    console.print("\n[bold cyan]Live Metrics Dashboard[/bold cyan]")
    console.print("[dim]Press Ctrl+C to exit[/dim]\n")

    try:
        while True:
            console.clear()
            console.print(Panel.fit(
                "[bold cyan]Code Interpreter Metrics[/bold cyan]\n"
                f"[dim]Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}[/dim]",
                border_style="cyan"
            ))
            console.print()

            summary_panel = await build_summary_panel()
            pool_panel = await build_pool_panel()
            console.print(summary_panel)
            console.print()
            console.print(pool_panel)
            console.print()

            lang_table = await build_languages_table(24)
            console.print(lang_table)
            console.print()

            console.print(f"[dim]Refreshing in {args.interval}s... (Ctrl+C to exit)[/dim]")
            await asyncio.sleep(args.interval)

    except KeyboardInterrupt:
        console.print("\n[yellow]Dashboard stopped.[/yellow]")


async def cmd_interactive(args):
    """Interactive menu."""
    await ensure_started()

    while True:
        console.clear()
        console.print(Panel.fit(
            "[bold cyan]Code Interpreter Metrics[/bold cyan]\n"
            "[dim]Interactive Dashboard[/dim]",
            border_style="cyan"
        ))
        console.print()

        now = datetime.now(timezone.utc)
        start = now - timedelta(hours=24)
        summary = await metrics_service.get_summary_stats(start=start, end=now)
        console.print(
            f"  [cyan]Executions (24h):[/cyan] {summary.get('total_executions', 0)}  "
            f"[cyan]Success rate:[/cyan] {summary.get('success_rate', 0):.1f}%  "
            f"[cyan]Avg time:[/cyan] {format_duration(summary.get('avg_execution_time_ms', 0))}"
        )
        console.print()

        console.print("[bold]Commands:[/bold]")
        console.print("  [cyan]1[/cyan]  Summary")
        console.print("  [cyan]2[/cyan]  Language breakdown")
        console.print("  [cyan]3[/cyan]  Sandbox pool stats")
        console.print("  [cyan]4[/cyan]  Live dashboard (auto-refresh)")
        console.print("  [cyan]q[/cyan]  Quit")
        console.print()

        choice = Prompt.ask("Select", choices=["1", "2", "3", "4", "q"], default="1")

        if choice == "q":
            console.print("[yellow]Goodbye![/yellow]")
            break
        elif choice == "1":
            panel = await build_summary_panel()
            console.print()
            console.print(panel)
        elif choice == "2":
            table = await build_languages_table(24)
            console.print()
            console.print(table)
        elif choice == "3":
            panel = await build_pool_panel()
            console.print()
            console.print(panel)
        elif choice == "4":
            args.interval = 5
            await cmd_watch(args)
            continue

        console.print()
        Prompt.ask("[dim]Press Enter to continue[/dim]", default="")


def main():
    parser = argparse.ArgumentParser(
        description="Metrics CLI - Interactive execution metrics dashboard",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command")

    # summary
    subparsers.add_parser("summary", help="Show metrics summary")

    # languages
    lang_p = subparsers.add_parser("languages", help="Per-language metrics")
    lang_p.add_argument("--hours", type=int, default=24)

    # pool
    subparsers.add_parser("pool", help="Sandbox pool stats")

    # watch
    watch_p = subparsers.add_parser("watch", help="Auto-refresh dashboard")
    watch_p.add_argument("--interval", type=int, default=5, help="Refresh interval in seconds")

    args = parser.parse_args()

    handlers = {
        "summary": cmd_summary,
        "languages": cmd_languages,
        "pool": cmd_pool,
        "watch": cmd_watch,
        None: cmd_interactive,
    }

    try:
        asyncio.run(handlers[args.command](args))
    except KeyboardInterrupt:
        console.print("\n[yellow]Cancelled.[/yellow]")
        sys.exit(0)


if __name__ == "__main__":
    main()
