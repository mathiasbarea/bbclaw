from .registry import registry, ToolRegistry, ToolDefinition, ToolResult
from . import filesystem  # noqa: F401 – registra herramientas de filesystem
from . import terminal    # noqa: F401 – registra herramienta run_command
from . import self_improve  # noqa: F401 – registra herramientas de auto-mejora

__all__ = ["registry", "ToolRegistry", "ToolDefinition", "ToolResult"]
