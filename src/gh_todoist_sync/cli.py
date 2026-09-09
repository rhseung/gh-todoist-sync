"""Command line entry points."""

from __future__ import annotations

from typing import Annotated

import typer

from . import agent, gh, state, todoist
from .reconcile import COMPLETE_CAP, GRACE_DAYS, SyncError, reconcile

app = typer.Typer(help="Mirror GitHub work assigned to me into Todoist.")


@app.callback(invoke_without_command=True)
def default(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        ctx.invoke(sync)


@app.command()
def sync(
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Print the plan, change nothing.")
    ] = False,
    force: Annotated[bool, typer.Option("--force", help="Lift the bulk-completion guard.")] = False,
    grace: Annotated[
        int, typer.Option(help="Days a section or sub-project may sit empty before deletion.")
    ] = GRACE_DAYS,
) -> None:
    """Bring Todoist in line with GitHub."""
    # GitHub is read first and on its own: if it fails, nothing is written, so a
    # degraded response can never be mistaken for "all my work is done".
    current = state.load()
    with gh.client() as api:
        items = gh.desired(api)
        discarded = gh.discarded(api, set(current.tasks) - {i.gh_id for i in items})

    with todoist.client() as api:
        try:
            cap = 10**9 if force else COMPLETE_CAP
            ops = reconcile(
                items, todoist.snapshot(api, current), cap=cap, grace=grace, discarded=discarded
            )
        except SyncError as error:
            typer.secho(str(error), fg=typer.colors.RED, err=True)
            raise typer.Exit(1) from error
        if dry_run:
            for op in ops:
                typer.echo(f"{type(op).__name__:18} {op}")
        else:
            todoist.apply(api, current, ops)

    typer.echo(f"{len(items)} github items, {len(ops)} ops{' (dry run)' if dry_run else ''}")


@app.command()
def install(
    interval: Annotated[int, typer.Option(help="Seconds between runs.")] = agent.DEFAULT_INTERVAL,
) -> None:
    """Install and load the launchd agent."""
    path = agent.install(interval)
    typer.echo(f"loaded {path} (every {interval}s, log: {agent.LOG})")


@app.command()
def uninstall() -> None:
    """Unload and remove the launchd agent."""
    agent.uninstall()
    typer.echo("removed")


@app.command()
def status() -> None:
    """Show whether the agent is running."""
    typer.echo(agent.describe())
