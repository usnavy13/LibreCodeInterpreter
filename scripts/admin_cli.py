#!/usr/bin/env python3
"""
Code Interpreter Admin CLI - Unified management dashboard.

Usage:
  python scripts/admin_cli.py              # Interactive mode
  python scripts/admin_cli.py metrics      # Metrics dashboard
  python scripts/admin_cli.py keys         # API key management

Features:
  - Real-time metrics dashboard
  - API key management (create, list, revoke, update)
  - Per-language and per-API-key usage stats
  - Sandbox pool monitoring
"""

import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

# Load .env file if it exists
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.columns import Columns
from rich.text import Text
from rich.prompt import Prompt, Confirm, IntPrompt
from rich import box

from src.config import settings
from src.core.pool import redis_pool
from src.services.metrics import metrics_service
from src.services.api_key_manager import ApiKeyManagerService
from src.models.api_key import RateLimits

console = Console()


# ============================================================================
# Service Initialization
# ============================================================================

async def get_redis():
    """Get Redis client."""
    redis_client = redis_pool.get_client()
    try:
        await redis_client.ping()
    except Exception as e:
        console.print(f"[red]Error:[/red] Cannot connect to Redis: {e}")
        console.print("\nEnsure Redis is running and accessible.")
        sys.exit(1)
    return redis_client


async def ensure_metrics_started():
    """Ensure metrics service is running."""
    if not metrics_service._running:
        await metrics_service.start()


async def get_key_manager() -> ApiKeyManagerService:
    """Get API key manager instance."""
    redis_client = await get_redis()
    return ApiKeyManagerService(redis_client)


# ============================================================================
# Formatting Helpers
# ============================================================================

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


def format_error_rate(rate: float) -> Text:
    """Format error rate (lower is better)."""
    text = f"{rate:.1f}%"
    if rate <= 5:
        return Text(text, style="green")
    elif rate <= 20:
        return Text(text, style="yellow")
    else:
        return Text(text, style="red")


def format_limit(value: Optional[int]) -> str:
    """Format rate limit value."""
    return str(value) if value else "unlimited"


# ============================================================================
# Metrics Panels
# ============================================================================

async def build_summary_panel(hours: int = 24) -> Panel:
    """Build summary panel."""
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

    return Panel(table, title=f"[bold]Metrics Summary[/bold] (last {hours}h)", border_style="blue")


async def build_languages_table(hours: int = 24) -> Table:
    """Build languages table."""
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

    if sorted_langs:
        total = sum(count for _, count in sorted_langs)
        table.add_row("", "", style="dim")
        table.add_row("[bold]TOTAL[/bold]", f"[bold]{total}[/bold]")

    if not sorted_langs:
        table.add_row("[dim]No data[/dim]", "")

    return table


async def build_pool_panel() -> Panel:
    """Build pool stats panel."""
    pool_stats = metrics_service.get_pool_stats()

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="white")

    table.add_row("Total Acquisitions", str(pool_stats["total_acquisitions"]))
    table.add_row("Pool Hits", Text(str(pool_stats["pool_hits"]), style="green"))
    table.add_row("Pool Misses", Text(str(pool_stats["pool_misses"]), style="yellow"))
    table.add_row("Hit Rate", format_rate(pool_stats["hit_rate"]))
    table.add_row("Avg Acquire Time", format_duration(pool_stats["avg_acquire_time_ms"]))
    table.add_row("Exhaustion Events", Text(str(pool_stats["exhaustion_events"]),
                                            style="red" if pool_stats["exhaustion_events"] > 0 else "green"))

    return Panel(table, title="[bold]Sandbox Pool[/bold]", border_style="magenta")


async def build_hourly_table(hours: int = 12) -> Table:
    """Build hourly breakdown table using time series from SQLite."""
    now = datetime.now(timezone.utc)
    start = now - timedelta(hours=hours)
    data = await metrics_service.get_time_series(
        start=start, end=now, granularity="hour"
    )

    table = Table(title=f"Hourly Breakdown (last {hours}h)", box=box.ROUNDED)
    table.add_column("Hour", style="dim")
    table.add_column("Executions", justify="right")
    table.add_column("Success Rate", justify="right")
    table.add_column("Avg Time", justify="right")

    for i, ts in enumerate(data.get("timestamps", [])):
        table.add_row(
            ts,
            str(data["executions"][i]),
            format_rate(data["success_rate"][i]),
            format_duration(data["avg_duration"][i]),
        )

    if not data.get("timestamps"):
        table.add_row("[dim]No data[/dim]", "", "", "")

    return table


# ============================================================================
# API Key Panels
# ============================================================================

async def build_keys_table(manager: ApiKeyManagerService) -> Table:
    """Build API keys table."""
    keys = await manager.list_keys()

    table = Table(title="API Keys", box=box.ROUNDED)
    table.add_column("Prefix", style="cyan")
    table.add_column("Name", style="white")
    table.add_column("Status", justify="center")
    table.add_column("Hourly", justify="right")
    table.add_column("Daily", justify="right")
    table.add_column("Monthly", justify="right")
    table.add_column("Uses", justify="right")
    table.add_column("Last Used", style="dim")

    if not keys:
        table.add_row("[dim]No API keys found[/dim]", "", "", "", "", "", "", "")
    else:
        for key in keys:
            status = Text("Active", style="green") if key.enabled else Text("Disabled", style="red")
            last_used = key.last_used_at.strftime('%Y-%m-%d %H:%M') if key.last_used_at else "never"

            table.add_row(
                key.key_prefix,
                key.name[:20],
                status,
                format_limit(key.rate_limits.hourly),
                format_limit(key.rate_limits.daily),
                format_limit(key.rate_limits.monthly),
                str(key.usage_count),
                last_used
            )

    return table


async def build_key_detail_panel(manager: ApiKeyManagerService, key_hash: str) -> Panel:
    """Build detailed API key panel."""
    record = await manager.get_key(key_hash)
    if not record:
        return Panel("[red]Key not found[/red]", title="API Key Details")

    usage = await manager.get_usage(key_hash)
    statuses = await manager.get_rate_limit_status(key_hash)

    # Key info table
    info_table = Table(show_header=False, box=None, padding=(0, 2))
    info_table.add_column("Field", style="cyan")
    info_table.add_column("Value", style="white")

    info_table.add_row("Prefix", record.key_prefix)
    info_table.add_row("Name", record.name)
    info_table.add_row("Status", Text("Active", style="green") if record.enabled else Text("Disabled", style="red"))
    info_table.add_row("Created", record.created_at.strftime('%Y-%m-%d %H:%M:%S UTC'))
    info_table.add_row("Last Used", record.last_used_at.strftime('%Y-%m-%d %H:%M:%S UTC') if record.last_used_at else "never")
    info_table.add_row("Total Uses", str(record.usage_count))
    info_table.add_row("", "")
    info_table.add_row("[bold]Rate Limits[/bold]", "")

    # Rate limit status
    for status in statuses:
        limit_str = format_limit(status.limit)
        if status.limit:
            remaining = f" ({status.remaining} remaining)"
            if status.is_exceeded:
                info_table.add_row(
                    f"  {status.period.capitalize()}",
                    Text(f"{status.used}/{limit_str} [EXCEEDED]", style="red")
                )
            else:
                info_table.add_row(
                    f"  {status.period.capitalize()}",
                    f"{status.used}/{limit_str}{remaining}"
                )
        else:
            info_table.add_row(f"  {status.period.capitalize()}", f"{status.used} (unlimited)")

    return Panel(info_table, title=f"[bold]API Key: {record.key_prefix}[/bold]", border_style="green")


# ============================================================================
# Interactive Menus
# ============================================================================

async def metrics_menu():
    """Metrics sub-menu."""
    await ensure_metrics_started()

    while True:
        console.clear()
        console.print(Panel.fit(
            "[bold cyan]Metrics Dashboard[/bold cyan]",
            border_style="cyan"
        ))
        console.print()

        # Quick stats
        now = datetime.now(timezone.utc)
        start = now - timedelta(hours=24)
        summary = await metrics_service.get_summary_stats(start=start, end=now)
        console.print(f"  [cyan]Today:[/cyan] {summary.get('total_executions', 0)} executions  "
                     f"[cyan]Success:[/cyan] {summary.get('success_rate', 0):.1f}%  "
                     f"[cyan]Avg:[/cyan] {format_duration(summary.get('avg_execution_time_ms', 0))}")
        console.print()

        console.print("[bold]Options:[/bold]")
        console.print("  [cyan]1[/cyan]  Summary")
        console.print("  [cyan]2[/cyan]  Language breakdown")
        console.print("  [cyan]3[/cyan]  Hourly breakdown")
        console.print("  [cyan]4[/cyan]  Sandbox pool stats")
        console.print("  [cyan]5[/cyan]  Live dashboard (auto-refresh)")
        console.print("  [cyan]b[/cyan]  Back to main menu")
        console.print()

        choice = Prompt.ask("Select", choices=["1", "2", "3", "4", "5", "b"], default="1")

        if choice == "b":
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
            table = await build_hourly_table(12)
            console.print()
            console.print(table)
        elif choice == "4":
            panel = await build_pool_panel()
            console.print()
            console.print(panel)
        elif choice == "5":
            await live_dashboard()
            continue

        console.print()
        Prompt.ask("[dim]Press Enter to continue[/dim]", default="")


async def live_dashboard():
    """Auto-refresh dashboard."""
    console.print("\n[bold cyan]Live Dashboard[/bold cyan] [dim](Ctrl+C to exit)[/dim]\n")

    try:
        while True:
            console.clear()
            console.print(Panel.fit(
                "[bold cyan]Code Interpreter - Live Dashboard[/bold cyan]\n"
                f"[dim]Updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}[/dim]",
                border_style="cyan"
            ))
            console.print()

            summary_panel = await build_summary_panel()
            pool_panel = await build_pool_panel()
            console.print(Columns([summary_panel, pool_panel], equal=True, expand=True))
            console.print()

            lang_table = await build_languages_table(24)
            console.print(lang_table)
            console.print()

            console.print("[dim]Refreshing in 5s... (Ctrl+C to exit)[/dim]")
            await asyncio.sleep(5)

    except KeyboardInterrupt:
        console.print("\n[yellow]Dashboard stopped.[/yellow]")


async def keys_menu():
    """API Keys sub-menu."""
    manager = await get_key_manager()

    while True:
        console.clear()
        console.print(Panel.fit(
            "[bold green]API Key Management[/bold green]",
            border_style="green"
        ))
        console.print()

        # Show keys table
        table = await build_keys_table(manager)
        console.print(table)
        console.print()

        console.print("[bold]Options:[/bold]")
        console.print("  [cyan]1[/cyan]  Create new key")
        console.print("  [cyan]2[/cyan]  View key details")
        console.print("  [cyan]3[/cyan]  Enable/disable key")
        console.print("  [cyan]4[/cyan]  Update rate limits")
        console.print("  [cyan]5[/cyan]  Revoke key")
        console.print("  [cyan]b[/cyan]  Back to main menu")
        console.print()

        choice = Prompt.ask("Select", choices=["1", "2", "3", "4", "5", "b"], default="b")

        if choice == "b":
            break
        elif choice == "1":
            await create_key_flow(manager)
        elif choice == "2":
            await view_key_flow(manager)
        elif choice == "3":
            await toggle_key_flow(manager)
        elif choice == "4":
            await update_limits_flow(manager)
        elif choice == "5":
            await revoke_key_flow(manager)

        console.print()
        Prompt.ask("[dim]Press Enter to continue[/dim]", default="")


async def create_key_flow(manager: ApiKeyManagerService):
    """Create new API key flow."""
    console.print()
    console.print("[bold]Create New API Key[/bold]")
    console.print()

    name = Prompt.ask("Key name")
    if not name:
        console.print("[yellow]Cancelled[/yellow]")
        return

    console.print()
    console.print("[dim]Leave blank for unlimited[/dim]")
    hourly_str = Prompt.ask("Hourly limit", default="")
    daily_str = Prompt.ask("Daily limit", default="")
    monthly_str = Prompt.ask("Monthly limit", default="")

    hourly = int(hourly_str) if hourly_str else None
    daily = int(daily_str) if daily_str else None
    monthly = int(monthly_str) if monthly_str else None

    rate_limits = RateLimits(hourly=hourly, daily=daily, monthly=monthly)

    full_key, record = await manager.create_key(
        name=name,
        rate_limits=rate_limits,
        metadata={"created_by": "admin_cli", "created_at": datetime.now().isoformat()}
    )

    console.print()
    console.print(Panel(
        f"[bold green]API Key Created![/bold green]\n\n"
        f"[cyan]Key:[/cyan]     [bold]{full_key}[/bold]\n"
        f"[cyan]Name:[/cyan]    {record.name}\n"
        f"[cyan]Prefix:[/cyan]  {record.key_prefix}\n\n"
        f"[cyan]Limits:[/cyan]\n"
        f"  Hourly:  {format_limit(rate_limits.hourly)}\n"
        f"  Daily:   {format_limit(rate_limits.daily)}\n"
        f"  Monthly: {format_limit(rate_limits.monthly)}\n\n"
        f"[bold yellow]Save this key now - it cannot be retrieved later![/bold yellow]",
        border_style="green"
    ))


async def view_key_flow(manager: ApiKeyManagerService):
    """View key details flow."""
    console.print()
    prefix = Prompt.ask("Enter key prefix (e.g., sk-abc12345)")
    if not prefix:
        return

    key_hash = await manager.find_key_by_prefix(prefix)
    if not key_hash:
        console.print(f"[red]Key not found: {prefix}[/red]")
        return

    panel = await build_key_detail_panel(manager, key_hash)
    console.print()
    console.print(panel)


async def toggle_key_flow(manager: ApiKeyManagerService):
    """Enable/disable key flow."""
    console.print()
    prefix = Prompt.ask("Enter key prefix")
    if not prefix:
        return

    key_hash = await manager.find_key_by_prefix(prefix)
    if not key_hash:
        console.print(f"[red]Key not found: {prefix}[/red]")
        return

    record = await manager.get_key(key_hash)
    if not record:
        console.print(f"[red]Key not found[/red]")
        return

    current = "enabled" if record.enabled else "disabled"
    new_state = not record.enabled
    action = "enable" if new_state else "disable"

    if Confirm.ask(f"Key is currently {current}. {action.capitalize()} it?"):
        await manager.update_key(key_hash, enabled=new_state)
        console.print(f"[green]Key {action}d successfully[/green]")


async def update_limits_flow(manager: ApiKeyManagerService):
    """Update rate limits flow."""
    console.print()
    prefix = Prompt.ask("Enter key prefix")
    if not prefix:
        return

    key_hash = await manager.find_key_by_prefix(prefix)
    if not key_hash:
        console.print(f"[red]Key not found: {prefix}[/red]")
        return

    record = await manager.get_key(key_hash)
    if not record:
        console.print(f"[red]Key not found[/red]")
        return

    console.print()
    console.print(f"Current limits for [cyan]{record.key_prefix}[/cyan]:")
    console.print(f"  Hourly:  {format_limit(record.rate_limits.hourly)}")
    console.print(f"  Daily:   {format_limit(record.rate_limits.daily)}")
    console.print(f"  Monthly: {format_limit(record.rate_limits.monthly)}")
    console.print()
    console.print("[dim]Enter new values (blank to keep, 0 for unlimited)[/dim]")

    hourly_str = Prompt.ask("New hourly limit", default="")
    daily_str = Prompt.ask("New daily limit", default="")
    monthly_str = Prompt.ask("New monthly limit", default="")

    hourly = record.rate_limits.hourly
    daily = record.rate_limits.daily
    monthly = record.rate_limits.monthly

    if hourly_str:
        hourly = int(hourly_str) if int(hourly_str) > 0 else None
    if daily_str:
        daily = int(daily_str) if int(daily_str) > 0 else None
    if monthly_str:
        monthly = int(monthly_str) if int(monthly_str) > 0 else None

    new_limits = RateLimits(hourly=hourly, daily=daily, monthly=monthly)
    await manager.update_key(key_hash, rate_limits=new_limits)
    console.print("[green]Rate limits updated successfully[/green]")


async def revoke_key_flow(manager: ApiKeyManagerService):
    """Revoke key flow."""
    console.print()
    prefix = Prompt.ask("Enter key prefix to revoke")
    if not prefix:
        return

    key_hash = await manager.find_key_by_prefix(prefix)
    if not key_hash:
        console.print(f"[red]Key not found: {prefix}[/red]")
        return

    record = await manager.get_key(key_hash)
    if not record:
        console.print(f"[red]Key not found[/red]")
        return

    console.print()
    console.print(f"[bold red]WARNING:[/bold red] This will permanently delete the API key:")
    console.print(f"  Prefix: {record.key_prefix}")
    console.print(f"  Name:   {record.name}")
    console.print()

    if Confirm.ask("Are you sure you want to revoke this key?", default=False):
        await manager.revoke_key(key_hash)
        console.print("[green]Key revoked successfully[/green]")
    else:
        console.print("[yellow]Cancelled[/yellow]")


async def main_menu():
    """Main interactive menu."""
    while True:
        console.clear()

        # Header
        console.print(Panel.fit(
            "[bold cyan]Code Interpreter Admin[/bold cyan]\n"
            "[dim]Management & Monitoring Dashboard[/dim]",
            border_style="cyan"
        ))
        console.print()

        # Quick stats
        try:
            await ensure_metrics_started()
            now = datetime.now(timezone.utc)
            start = now - timedelta(hours=24)
            summary = await metrics_service.get_summary_stats(start=start, end=now)
            manager = await get_key_manager()
            keys = await manager.list_keys()

            success_rate = summary.get("success_rate", 0)
            stats_table = Table(show_header=False, box=None)
            stats_table.add_column("", style="dim")
            stats_table.add_column("")
            stats_table.add_row("Executions (24h):", f"[cyan]{summary.get('total_executions', 0)}[/cyan]")
            stats_table.add_row("Success Rate:", f"[{'green' if success_rate >= 80 else 'yellow'}]{success_rate:.1f}%[/]")
            stats_table.add_row("API Keys:", f"[cyan]{len(keys)}[/cyan] active")

            console.print(stats_table)
            console.print()
        except Exception:
            pass

        console.print("[bold]Main Menu:[/bold]")
        console.print("  [cyan]1[/cyan]  Metrics Dashboard")
        console.print("  [cyan]2[/cyan]  API Key Management")
        console.print("  [cyan]3[/cyan]  Quick: Create API Key")
        console.print("  [cyan]4[/cyan]  Quick: View Live Dashboard")
        console.print("  [cyan]q[/cyan]  Quit")
        console.print()

        choice = Prompt.ask("Select", choices=["1", "2", "3", "4", "q"], default="1")

        if choice == "q":
            console.print("[yellow]Goodbye![/yellow]")
            break
        elif choice == "1":
            await metrics_menu()
        elif choice == "2":
            await keys_menu()
        elif choice == "3":
            manager = await get_key_manager()
            await create_key_flow(manager)
            console.print()
            Prompt.ask("[dim]Press Enter to continue[/dim]", default="")
        elif choice == "4":
            await ensure_metrics_started()
            await live_dashboard()


# ============================================================================
# CLI Commands
# ============================================================================

async def cmd_metrics(args):
    """Direct metrics command."""
    await metrics_menu()


async def cmd_keys(args):
    """Direct keys command."""
    await keys_menu()


async def cmd_create_key(args):
    """Create key from command line."""
    manager = await get_key_manager()

    rate_limits = RateLimits(
        hourly=args.hourly,
        daily=args.daily,
        monthly=args.monthly
    )

    full_key, record = await manager.create_key(
        name=args.name,
        rate_limits=rate_limits,
        metadata={"created_by": "admin_cli", "created_at": datetime.now().isoformat()}
    )

    console.print()
    console.print(Panel(
        f"[bold green]API Key Created![/bold green]\n\n"
        f"[cyan]Key:[/cyan]     [bold]{full_key}[/bold]\n"
        f"[cyan]Name:[/cyan]    {record.name}\n"
        f"[cyan]Prefix:[/cyan]  {record.key_prefix}\n\n"
        f"[cyan]Limits:[/cyan]  H:{format_limit(rate_limits.hourly)} D:{format_limit(rate_limits.daily)} M:{format_limit(rate_limits.monthly)}\n\n"
        f"[bold yellow]Save this key now![/bold yellow]",
        border_style="green"
    ))


async def cmd_list_keys(args):
    """List all keys."""
    manager = await get_key_manager()
    table = await build_keys_table(manager)
    console.print()
    console.print(table)
    console.print()


async def cmd_revoke(args):
    """Revoke a key."""
    manager = await get_key_manager()

    key_hash = await manager.find_key_by_prefix(args.prefix)
    if not key_hash:
        console.print(f"[red]Key not found: {args.prefix}[/red]")
        return

    if not args.force:
        record = await manager.get_key(key_hash)
        if not Confirm.ask(f"Revoke key {record.key_prefix} ({record.name})?", default=False):
            console.print("[yellow]Cancelled[/yellow]")
            return

    await manager.revoke_key(key_hash)
    console.print(f"[green]Key revoked: {args.prefix}[/green]")


async def cmd_summary(args):
    """Show quick summary."""
    await ensure_metrics_started()
    panel = await build_summary_panel()
    console.print()
    console.print(panel)
    console.print()


def main():
    parser = argparse.ArgumentParser(
        description="Code Interpreter Admin CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                           # Interactive mode
  %(prog)s metrics                   # Metrics dashboard
  %(prog)s keys                      # API key management
  %(prog)s create --name "My App"    # Create unlimited key
  %(prog)s create --name "App" --hourly 1000 --daily 10000
  %(prog)s list                      # List all keys
  %(prog)s revoke sk-abc12345        # Revoke a key
  %(prog)s summary                   # Quick metrics summary
"""
    )
    subparsers = parser.add_subparsers(dest="command")

    # metrics
    subparsers.add_parser("metrics", help="Metrics dashboard")

    # keys
    subparsers.add_parser("keys", help="API key management")

    # create
    create_p = subparsers.add_parser("create", help="Create new API key")
    create_p.add_argument("--name", required=True, help="Key name")
    create_p.add_argument("--hourly", type=int, help="Hourly limit")
    create_p.add_argument("--daily", type=int, help="Daily limit")
    create_p.add_argument("--monthly", type=int, help="Monthly limit")

    # list
    subparsers.add_parser("list", help="List all API keys")

    # revoke
    revoke_p = subparsers.add_parser("revoke", help="Revoke an API key")
    revoke_p.add_argument("prefix", help="Key prefix (e.g., sk-abc12345)")
    revoke_p.add_argument("-f", "--force", action="store_true", help="Skip confirmation")

    # summary
    subparsers.add_parser("summary", help="Quick metrics summary")

    args = parser.parse_args()

    handlers = {
        "metrics": cmd_metrics,
        "keys": cmd_keys,
        "create": cmd_create_key,
        "list": cmd_list_keys,
        "revoke": cmd_revoke,
        "summary": cmd_summary,
        None: lambda _: main_menu(),
    }

    try:
        asyncio.run(handlers[args.command](args))
    except KeyboardInterrupt:
        console.print("\n[yellow]Cancelled.[/yellow]")
        sys.exit(0)


if __name__ == "__main__":
    main()
