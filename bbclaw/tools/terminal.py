"""
Herramienta de ejecución de comandos en la terminal.
Ejecuta en el directorio del workspace con timeout configurable.
"""

from __future__ import annotations

import asyncio
import logging
import shlex
from pathlib import Path

from .registry import registry
from .filesystem import _WORKSPACE_ROOT

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 60  # segundos


async def _run_command(
    command: str,
    timeout: int = _DEFAULT_TIMEOUT,
    working_dir: str = ".",
) -> str:
    """
    Ejecuta un comando de shell en el workspace del agente.
    - stdout y stderr se capturan y devuelven combinados.
    - Si el proceso supera el timeout, se termina.
    - El working_dir es relativo al workspace root.
    """
    # Resolver directorio de trabajo
    cwd = (_WORKSPACE_ROOT / working_dir).resolve()
    if not str(cwd).startswith(str(_WORKSPACE_ROOT.resolve())):
        raise ValueError(f"working_dir '{working_dir}' está fuera del workspace")
    cwd.mkdir(parents=True, exist_ok=True)

    logger.info("Ejecutando: %s (cwd=%s, timeout=%ds)", command, cwd, timeout)

    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(cwd),
        )

        try:
            stdout_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return f"⏱ Timeout ({timeout}s) alcanzado. El proceso fue terminado."

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        exit_code = proc.returncode

        result = f"[exit code: {exit_code}]\n{stdout}"
        if exit_code != 0:
            logger.warning("Comando salió con código %d: %s", exit_code, command)
        return result

    except Exception as e:
        raise RuntimeError(f"Error al ejecutar comando: {e}") from e


registry.register(
    name="run_command",
    description=(
        "Ejecuta un comando de shell en el workspace del agente. "
        "Devuelve stdout+stderr y el código de salida. "
        "Úsalo para instalar paquetes, correr scripts, tests, git, etc."
    ),
    func=_run_command,
    parameters={
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "Comando a ejecutar (se pasa a un shell)",
            },
            "timeout": {
                "type": "integer",
                "description": "Tiempo máximo de ejecución en segundos (default: 60)",
                "default": 60,
            },
            "working_dir": {
                "type": "string",
                "description": "Directorio de trabajo relativo al workspace (default: raíz del workspace)",
                "default": ".",
            },
        },
        "required": ["command"],
    },
)
