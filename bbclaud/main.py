"""
Entry point CLI del sistema bbclaud.
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

from bbclaud.core.orchestrator import Orchestrator
from bbclaud.identity import SYSTEM_NAME

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
- `/help`    — muestra esta ayuda
- `/tools`   — lista herramientas disponibles
- `/history` — muestra últimas conversaciones
- `/logout`  — elimina el token OAuth guardado
- `/exit`    — salir del sistema

**Tutto lo demás** se envía al agente.
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

    while True:
        try:
            user_input = Prompt.ask("[bold blue]Tú[/bold blue]")
        except (EOFError, KeyboardInterrupt):
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
            from bbclaud.tools.registry import registry
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
