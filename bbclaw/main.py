"""
Entry point CLI del sistema bbclaw.
REPL interactivo con comandos especiales (/exit, /tools, /history, /logout, /help).
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt
from rich.text import Text

from bbclaw.core.orchestrator import Orchestrator
from bbclaw.identity import SYSTEM_NAME

console = Console()

def _make_banner() -> str:
    name_line = f"🤖  {SYSTEM_NAME} — Sistema de Agentes      "
    pad = max(0, 42 - len(name_line))
    name_line = f"║   {name_line}{' ' * pad}║"
    return f"""
╔══════════════════════════════════════════╗
{name_line}
║         Auto-Mejorable v0.1             ║
╚══════════════════════════════════════════╝
Comandos: /help /tools /history /logout /exit
"""

HELP_TEXT = """
**Comandos disponibles:**
- `/help`              — muestra esta ayuda
- `/tools`             — lista herramientas disponibles
- `/history`           — muestra últimas conversaciones
- `/objective`             — ver objective del proyecto activo
- `/objective set <texto>` — definir objective del proyecto activo
- `/objective clear`       — eliminar objective del proyecto activo
- `/improvements [N]`  — últimos N improvement attempts (default 5)
- `/schedule list`     — listar tareas/reminders programados
- `/schedule upcoming` — próximas ejecuciones
- `/schedule cancel <id>` — cancelar item
- `/schedule pause <id>`  — pausar item
- `/schedule resume <id>` — resumir item
- `/logout`            — elimina el token OAuth guardado
- `/exit`              — salir del sistema

**Todo lo demás** se envía al agente.
"""


def setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


async def repl(orchestrator: Orchestrator, verbose: bool) -> None:
    """Loop interactivo principal."""
    setup_logging(verbose)

    console.print(_make_banner(), style="bold cyan")
    console.print("Iniciando sistema...", style="dim")

    await orchestrator.start()
    console.print("✓ Sistema listo.\n", style="bold green")

    # Show missed reminders (fired while offline)
    try:
        if orchestrator.db:
            from bbclaw.core.scheduler import to_iso, now_utc
            missed = await orchestrator.db.get_due_items(to_iso(now_utc()))
            for item in missed:
                if item.get("item_type") == "reminder":
                    console.print(Panel(
                        f"{item['title']}\n[dim]Programado: {item.get('next_run_at', '?')}[/dim]",
                        title="🔔 Recordatorio perdido",
                        border_style="magenta",
                    ))
    except Exception:
        pass

    while True:
        # Display pending reminders before prompt
        try:
            reminders = orchestrator.get_and_clear_reminders()
            for rem in reminders:
                console.print(Panel(
                    f"{rem['title']}\n[dim]{rem.get('fired_at', '')}[/dim]",
                    title="🔔 Recordatorio",
                    border_style="magenta",
                ))
        except Exception:
            pass

        try:
            user_input = await asyncio.to_thread(
                Prompt.ask, "[bold blue]Tú[/bold blue]"
            )
        except (EOFError, KeyboardInterrupt, asyncio.CancelledError):
            console.print("\n👋 Hasta luego.", style="bold")
            break

        user_input = user_input.strip()
        if not user_input:
            continue

        # Comandos especiales
        if user_input.lower() in ("/exit", "/quit", "/q"):
            console.print("👋 Hasta luego.", style="bold")
            break

        if user_input.lower() == "/help":
            console.print(Markdown(HELP_TEXT))
            continue

        if user_input.lower() == "/tools":
            from bbclaw.tools.registry import registry
            tools = registry.list_tools()
            console.print(
                Panel(
                    "\n".join(f"• {t}" for t in tools),
                    title="🔧 Herramientas disponibles",
                    border_style="cyan",
                )
            )
            continue

        if user_input.lower() == "/history":
            if orchestrator.db:
                convs = await orchestrator.db.get_recent_conversations(10)
                if convs:
                    lines = []
                    for c in reversed(convs):
                        lines.append(f"**Usuario:** {c['user_msg'][:100]}")
                        if c.get("agent_msg"):
                            lines.append(f"**Agente:** {c['agent_msg'][:200]}\n")
                    console.print(Markdown("\n".join(lines)))
                else:
                    console.print("Sin historial aún.", style="dim")
            continue

        if user_input.lower() == "/logout":
            if orchestrator.provider and hasattr(orchestrator.provider, "logout"):
                await orchestrator.provider.logout()
            continue

        # /objective [show|set <texto>|clear]
        if user_input.lower().startswith("/objective"):
            parts = user_input.split(maxsplit=2)
            sub = parts[1].lower() if len(parts) > 1 else "show"

            if not orchestrator.db:
                console.print("DB no disponible.", style="red")
                continue

            # Obtener sesión y proyecto activo
            session = getattr(orchestrator, "_session", None)
            active_id = getattr(session, "active_project_id", None) if session else None
            if not active_id:
                console.print("No hay proyecto activo. Usá /project switch <nombre> primero.", style="yellow")
                continue

            project = await orchestrator.db.fetchone(
                "SELECT * FROM projects WHERE id = ?", (active_id,)
            )
            if not project:
                console.print("Proyecto activo no encontrado en la DB.", style="red")
                continue

            if sub == "show":
                obj = project.get("objective") or ""
                if obj:
                    console.print(Panel(
                        f"**{project['name']}**\n\n{obj}",
                        title="🎯 Objective",
                        border_style="cyan",
                    ))
                else:
                    console.print(f"Proyecto '{project['name']}' no tiene objective. Usá /objective set <texto>", style="dim")
            elif sub == "set" and len(parts) > 2:
                text = parts[2]
                await orchestrator.db.update_project_objective(project["id"], text)
                console.print(f"✓ Objective actualizado para '{project['name']}'", style="bold green")
            elif sub == "clear":
                await orchestrator.db.update_project_objective(project["id"], "")
                console.print(f"✓ Objective eliminado de '{project['name']}'", style="bold green")
            else:
                console.print("Uso: /objective | /objective set <texto> | /objective clear", style="yellow")
            continue

        # /schedule list | upcoming | cancel | pause | resume
        if user_input.lower().startswith("/schedule"):
            parts = user_input.split(maxsplit=2)
            sub = parts[1].lower() if len(parts) > 1 else "list"

            if sub == "list":
                if orchestrator.db:
                    from bbclaw.core.scheduler import describe_schedule
                    items = await orchestrator.db.get_scheduled_items()
                    if items:
                        lines = []
                        for item in items:
                            sched = item["schedule"] if isinstance(item["schedule"], dict) else {}
                            icon = "🔔" if item["item_type"] == "reminder" else "📋"
                            st_icon = {"active": "🟢", "paused": "⏸️", "done": "✅", "cancelled": "❌"}.get(item["status"], "⚪")
                            lines.append(
                                f"{icon} {st_icon} **{item['id']}** — {item['title']}\n"
                                f"   {describe_schedule(sched)} | Próx: {item.get('next_run_at', 'N/A')} | Runs: {item.get('run_count', 0)}"
                            )
                        console.print(Panel(
                            Markdown("\n".join(lines)),
                            title="📅 Items programados",
                            border_style="cyan",
                        ))
                    else:
                        console.print("Sin items programados.", style="dim")
            elif sub == "upcoming":
                if orchestrator.db:
                    from bbclaw.core.scheduler import describe_schedule
                    items = await orchestrator.db.get_scheduled_items(status="active")
                    if items:
                        lines = []
                        for item in items[:10]:
                            sched = item["schedule"] if isinstance(item["schedule"], dict) else {}
                            icon = "🔔" if item["item_type"] == "reminder" else "📋"
                            lines.append(
                                f"{icon} **{item['id']}** — {item['title']}\n"
                                f"   Próx: {item.get('next_run_at', 'N/A')} | {describe_schedule(sched)}"
                            )
                        console.print(Panel(
                            Markdown("\n".join(lines)),
                            title="📅 Próximas ejecuciones",
                            border_style="cyan",
                        ))
                    else:
                        console.print("Sin items activos.", style="dim")
            elif sub in ("cancel", "pause", "resume") and len(parts) > 2:
                item_id = parts[2].strip()
                if orchestrator.db:
                    item = await orchestrator.db.get_scheduled_item(item_id)
                    if not item:
                        console.print(f"Item no encontrado: {item_id}", style="red")
                    elif sub == "cancel":
                        await orchestrator.db.update_scheduled_item(item_id, status="cancelled", next_run_at=None)
                        console.print(f"✓ Cancelado: {item_id}", style="bold green")
                    elif sub == "pause":
                        if item["status"] != "active":
                            console.print(f"Solo se puede pausar items activos (actual: {item['status']})", style="yellow")
                        else:
                            await orchestrator.db.update_scheduled_item(item_id, status="paused")
                            console.print(f"✓ Pausado: {item_id}", style="bold green")
                    elif sub == "resume":
                        if item["status"] != "paused":
                            console.print(f"Solo se puede resumir items pausados (actual: {item['status']})", style="yellow")
                        else:
                            from bbclaw.core.scheduler import compute_next_run
                            sched = item["schedule"] if isinstance(item["schedule"], dict) else {}
                            next_run = compute_next_run(sched)
                            if next_run:
                                await orchestrator.db.update_scheduled_item(item_id, status="active", next_run_at=next_run)
                                console.print(f"✓ Resumido: {item_id} — Próx: {next_run}", style="bold green")
                            else:
                                await orchestrator.db.update_scheduled_item(item_id, status="done", next_run_at=None)
                                console.print(f"Item {item_id} no tiene más ejecuciones. Marcado como done.", style="yellow")
            else:
                console.print("Uso: /schedule list | upcoming | cancel <id> | pause <id> | resume <id>", style="yellow")
            continue

        # /improvements [N]
        if user_input.lower().startswith("/improvements"):
            parts = user_input.split()
            limit = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 5
            if orchestrator.db:
                attempts = await orchestrator.db.get_recent_improvement_attempts(limit)
                if attempts:
                    import json as _json
                    lines = []
                    for a in attempts:
                        merged = "✅" if a.get("merged") else "❌"
                        files = _json.loads(a.get("changed_files", "[]"))
                        files_str = ", ".join(files[:3]) if files else "ninguno"
                        err = f" — Error: {a['error'][:60]}" if a.get("error") else ""
                        lines.append(
                            f"{merged} Cycle {a['cycle']} | Branch: {a.get('branch', '?')} | "
                            f"Archivos: {files_str}{err}"
                        )
                    console.print(Panel(
                        "\n".join(lines),
                        title="🔧 Improvement Attempts",
                        border_style="blue",
                    ))
                else:
                    console.print("Sin improvement attempts aún.", style="dim")
            continue

        # Llamada al agente
        with console.status("[bold yellow]Pensando...[/bold yellow]", spinner="dots"):
            try:
                response = await orchestrator.run(user_input)
            except Exception as e:
                console.print(f"[bold red]Error:[/bold red] {e}")
                if verbose:
                    import traceback
                    traceback.print_exc()
                continue

        console.print()
        console.print(
            Panel(
                Markdown(response),
                title=f"[bold green]{SYSTEM_NAME}[/bold green]",
                border_style="green",
                padding=(1, 2),
            )
        )
        console.print()


@click.command()
@click.option(
    "--config",
    default="config/default.toml",
    show_default=True,
    help="Ruta al archivo de configuración TOML",
)
@click.option(
    "--verbose", "-v",
    is_flag=True,
    default=False,
    help="Mostrar logs detallados",
)
def cli(config: str, verbose: bool) -> None:
    f"""
    {SYSTEM_NAME} — Sistema de agentes auto-mejorable.

    Inicia el REPL interactivo. La primera vez abrira el browser
    para autenticacion OAuth con OpenAI Codex.
    """
    orchestrator = Orchestrator(config_path=config)
    try:
        asyncio.run(repl(orchestrator, verbose))
    finally:
        asyncio.run(orchestrator.stop())


if __name__ == "__main__":
    cli()
