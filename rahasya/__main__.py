"""CLI entry point for the Rahasya OSINT platform.

Usage:
    python -m rahasya scan --name "John Doe" --email "john@example.com"
    python -m rahasya dashboard
    python -m rahasya worker
    python -m rahasya init-db
"""

import asyncio
import sys
import os

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn

console = Console()

BANNER = r"""
[bold cyan]
 ██████╗  █████╗ ██╗  ██╗ █████╗ ███████╗██╗   ██╗ █████╗
 ██╔══██╗██╔══██╗██║  ██║██╔══██╗██╔════╝╚██╗ ██╔╝██╔══██╗
 ██████╔╝███████║███████║███████║███████╗  ╚████╔╝ ███████║
 ██╔══██╗██╔══██║██╔══██║██╔══██║╚════██║   ╚██╔╝  ██╔══██║
 ██║  ██║██║  ██║██║  ██║██║  ██║███████║    ██║   ██║  ██║
 ╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝╚══════╝    ╚═╝   ╚═╝  ╚═╝
[/bold cyan]
[dim]Digital Footprint Intelligence Platform — OSINT Recursive Engine[/dim]
"""

BANNER = """
[bold cyan]RAHASYA[/bold cyan]
[dim]Digital Footprint Intelligence Platform - OSINT Recursive Engine[/dim]
"""


@click.group()
@click.version_option(version="1.0.0", prog_name="rahasya")
def cli() -> None:
    """Rahasya — OSINT Digital Footprint Intelligence Platform."""
    console.print(BANNER)


@cli.command()
@click.option("--name", "-n", default=None, help="Target full name")
@click.option("--email", "-e", default=None, help="Target email address")
@click.option("--phone", "-p", default=None, help="Target phone number")
@click.option("--username", "-u", default=None, help="Target username/handle")
@click.option("--photo", default=None, type=click.Path(exists=True), help="Path to target photo")
@click.option("--location", "-l", default=None, help="Location hint")
@click.option("--dob", default=None, help="Date of birth (YYYY-MM-DD)")
@click.option("--age-range", default=None, help="Age range (e.g., 20-30)")
@click.option("--max-depth", default=None, type=int, help="Override max recursion depth")
@click.option("--max-entities", default=None, type=int, help="Override max entity count")
def scan(name, email, phone, username, photo, location, dob, age_range,
         max_depth, max_entities) -> None:
    """Run an OSINT scan against a target.

    At least one identifier (name, email, phone, or username) is required.

    Examples:
        python -m rahasya scan --name "John Doe" --email "john@example.com"
        python -m rahasya scan --username johndoe --max-depth 2
    """
    if not any([name, email, phone, username, photo]):
        console.print("[bold red]Error:[/bold red] At least one target identifier is required.")
        console.print("Use --name, --email, --phone, --username, or --photo")
        sys.exit(1)

    # Display scan configuration
    table = Table(title="Scan Target", show_header=False, border_style="cyan")
    table.add_column("Field", style="bold cyan")
    table.add_column("Value", style="white")
    if name:
        table.add_row("Name", name)
    if email:
        table.add_row("Email", email)
    if phone:
        table.add_row("Phone", phone)
    if username:
        table.add_row("Username", username)
    if photo:
        table.add_row("Photo", photo)
    if location:
        table.add_row("Location", location)
    if dob:
        table.add_row("DOB", dob)
    if age_range:
        table.add_row("Age Range", age_range)
    console.print(table)

    # Run scan
    from rahasya.core.models import ScanRequest
    from rahasya.core.orchestrator import Orchestrator
    from rahasya.config import settings

    # Apply CLI overrides
    if max_depth is not None:
        settings.scan.max_depth = max_depth
    if max_entities is not None:
        settings.scan.max_entities = max_entities

    request = ScanRequest(
        name=name,
        email=email,
        phone=phone,
        username=username,
        photo_path=photo,
        dob=dob,
        age_range=age_range,
        location=location,
    )

    async def _run_scan():
        orchestrator = Orchestrator(settings)
        scan_id = await orchestrator.start_scan(request)
        console.print(f"\n[bold green]✓ Scan initiated:[/bold green] {scan_id}")
        console.print("[dim]Scan is running asynchronously...[/dim]\n")

        # Poll for completion
        import time
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("Scanning...", total=None)
            while True:
                result = orchestrator.get_scan_result(scan_id)
                if result is None:
                    break
                if result.status.value in ("COMPLETED", "FAILED", "CANCELLED"):
                    break
                progress.update(
                    task,
                    description=f"Scanning... ({result.stats.total_entities} entities found)"
                )
                await asyncio.sleep(1)

        # Print results
        result = orchestrator.get_scan_result(scan_id)
        if result:
            console.print(Panel.fit(
                f"[bold]Status:[/bold] {result.status.value}\n"
                f"[bold]Entities:[/bold] {result.stats.total_entities}\n"
                f"[bold]Relationships:[/bold] {result.stats.total_relationships}\n"
                f"[bold]Depth:[/bold] {result.stats.depth_reached}\n"
                f"[bold]Duration:[/bold] {result.stats.duration_seconds:.1f}s\n"
                f"[bold]Modules Run:[/bold] {result.stats.modules_run}",
                title="[bold cyan]Scan Results[/bold cyan]",
                border_style="cyan",
            ))

            # Type breakdown
            if result.stats.by_type:
                type_table = Table(title="Entity Breakdown", border_style="green")
                type_table.add_column("Type", style="bold")
                type_table.add_column("Count", justify="right")
                for etype, count in sorted(result.stats.by_type.items()):
                    type_table.add_row(etype, str(count))
                console.print(type_table)

    asyncio.run(_run_scan())


@cli.command()
def dashboard() -> None:
    """Launch the Streamlit CIA Web dashboard."""
    import subprocess
    app_path = os.path.join(os.path.dirname(__file__), "dashboard", "app.py")

    console.print("[bold cyan]Launching Rahasya Dashboard...[/bold cyan]")
    console.print(f"[dim]App: {app_path}[/dim]")

    try:
        subprocess.run(
            ["streamlit", "run", app_path,
             "--theme.base=dark",
             "--theme.primaryColor=#00d4ff",
             "--theme.backgroundColor=#0a0e17",
             "--theme.secondaryBackgroundColor=#111827",
             "--theme.textColor=#e2e8f0"],
            check=True,
        )
    except FileNotFoundError:
        console.print(
            "[bold red]Error:[/bold red] Streamlit not found. "
            "Install with: pip install streamlit"
        )
        sys.exit(1)
    except KeyboardInterrupt:
        console.print("\n[dim]Dashboard stopped.[/dim]")


@cli.command()
@click.option("--concurrency", "-c", default=4, help="Worker concurrency")
def worker(concurrency) -> None:
    """Start the Celery background worker."""
    import subprocess

    console.print(f"[bold cyan]Starting Celery worker (concurrency={concurrency})...[/bold cyan]")

    try:
        subprocess.run(
            ["celery", "-A", "rahasya.celery_app", "worker",
             f"--concurrency={concurrency}",
             "--loglevel=info",
             "-Q", "default,orchestration,discovery,social,breach,darkweb,correlation"],
            check=True,
        )
    except FileNotFoundError:
        console.print(
            "[bold red]Error:[/bold red] Celery not found. "
            "Install with: pip install celery[redis]"
        )
        sys.exit(1)
    except KeyboardInterrupt:
        console.print("\n[dim]Worker stopped.[/dim]")


@cli.command("init-db")
def init_db() -> None:
    """Upgrade the database schema using Alembic migrations."""
    console.print("[bold cyan]Initializing Rahasya database...[/bold cyan]")

    async def _init():
        from rahasya.storage.database import db_manager
        from rahasya.config import settings
        db_manager.initialize(settings)
        await db_manager.init_db()
        console.print("[bold green]Database migrations applied successfully.[/bold green]")
        await db_manager.close_db()

    try:
        asyncio.run(_init())
    except Exception as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        console.print("[dim]Make sure PostgreSQL is running and DB__URL is set in .env[/dim]")
        sys.exit(1)


if __name__ == "__main__":
    cli()
