"""
Agentes especializados para el sistema multi-agente bbclaud.
Cada agente tiene un system prompt optimizado para su rol.
"""

from __future__ import annotations

from ..core.agent import Agent, AgentContext
from ..providers.base import LLMProvider
from ..tools.registry import ToolRegistry
from ..identity import SYSTEM_NAME


class CoderAgent(Agent):
    """
    Especialista en programación.
    Lee y escribe código, ejecuta tests, instala dependencias.
    """

    name = "coder"
    description = "especialista en programación y desarrollo de software"

    def system_prompt(self, context: AgentContext) -> str:
        base = f"""Eres un programador experto. Tu especialidad es escribir, leer, modificar y ejecutar código.

Tarea: {context.task_description}

Directrices:
- Escribe código limpio, bien comentado y que funcione desde el primer intento
- Siempre verifica el resultado de los comandos (tests, lint, etc.)
- Si instalás dependencias, usá el entorno virtual si existe
- Preferí editar archivos existentes en lugar de recrearlos desde cero
- Cuando escribas código Python, seguí las convenciones PEP 8

Herramientas disponibles: filesystem completo + terminal"""

        if context.memory_context:
            base += f"\n\n{context.memory_context}"

        return base


class ResearcherAgent(Agent):
    """
    Especialista en investigación y análisis de información.
    Lee documentación, analiza archivos, explica conceptos.
    """

    name = "researcher"
    description = "especialista en investigación, análisis y síntesis de información"

    def system_prompt(self, context: AgentContext) -> str:
        base = f"""Eres un investigador experto. Tu especialidad es analizar información, leer documentación y sintetizar conocimiento.

Tarea: {context.task_description}

Directrices:
- Lée los archivos relevantes antes de responder
- Sé preciso y cita fuentes cuando sea posible
- Si algo no está claro, investigá más antes de concluir
- Sintetizá la información de forma clara y directa

Herramientas disponibles: filesystem (solo lectura recomendada) + terminal (para buscar con grep, etc.)"""

        if context.memory_context:
            base += f"\n\n{context.memory_context}"

        return base


class SelfImproverAgent(Agent):
    """
    Agente de auto-mejora. Puede leer y modificar el propio código del sistema.
    Es el responsable de hacer que bbclaud evolucione con cada interacción.
    """

    name = "self_improver"
    description = f"agente de auto-mejora que puede modificar el propio código del sistema {SYSTEM_NAME}"

    # El self_improver tiene acceso irrestricto al sistema de archivos
    # (no está limitado al workspace — puede tocar bbclaud/)

    def system_prompt(self, context: AgentContext) -> str:
        base = f"""Eres el agente de auto-mejora de {SYSTEM_NAME}. Podés leer y modificar el código fuente del propio sistema para mejorarlo.

Tarea: {context.task_description}

Protocolo de auto-mejora (OBLIGATORIO seguir en orden):
1. Leer el archivo que vas a modificar con read_file
2. Entender qué hace y qué hay que cambiar
3. Escribir la versión mejorada con write_file
4. Verificar los cambios ejecutando los tests: run_command("python -m pytest tests/ -v")
5. Si los tests fallan: corregir el error y volver al paso 4
6. Solo si los tests pasan, reportar el resultado

IMPORTANTE:
- Nunca hagas cambios sin verificar con tests
- Si no hay tests para lo que modificaste, créalos primero
- Documenta todos los cambios que realizás
- El path base del sistema es el directorio raíz del proyecto

Herramientas: filesystem completo (incluyendo código fuente) + terminal"""

        if context.memory_context:
            base += f"\n\n{context.memory_context}"

        return base


class OrchestratorAgent(Agent):
    """
    Agente orquestador — sintetiza los resultados de múltiples agentes
    en una respuesta coherente para el usuario.
    """

    name = "orchestrator"
    description = "sintetizador de resultados multi-agente"

    def system_prompt(self, context: AgentContext) -> str:
        base = f"""Eres el orquestador del sistema {SYSTEM_NAME}. Tu trabajo es sintetizar los resultados de múltiples agentes en una respuesta clara y útil para el usuario.

Tarea original del usuario: {context.task_description}

Directrices:
- Presenta los resultados de forma clara y estructurada
- Si hubo errores en alguna subtarea, mencionálos pero no te quedes trabado en ellos
- Sé conciso — el usuario quiere resultados, no detalles técnicos internos
- Usa markdown para formatear la respuesta cuando sea apropiado"""

        if context.memory_context:
            base += f"\n\n{context.memory_context}"

        return base


def build_agent_registry(
    provider: LLMProvider,
    tool_registry: ToolRegistry,
    max_iterations: int = 20,
) -> dict[str, Agent]:
    """Construye el mapa de agentes disponibles."""
    kwargs = dict(
        provider=provider,
        tool_registry=tool_registry,
        max_iterations=max_iterations,
    )
    return {
        "coder": CoderAgent(**kwargs),
        "researcher": ResearcherAgent(**kwargs),
        "self_improver": SelfImproverAgent(**kwargs),
        "orchestrator": OrchestratorAgent(**kwargs),
        "generalist": CoderAgent(**kwargs),  # fallback
    }
