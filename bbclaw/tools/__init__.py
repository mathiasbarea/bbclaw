from .registry import registry, ToolRegistry, ToolDefinition, ToolResult
from . import filesystem  # noqa: F401 – registra herramientas de filesystem
from . import terminal    # noqa: F401 – registra herramienta run_command
from . import self_improve  # noqa: F401 – registra herramientas de auto-mejora
from . import projects  # noqa: F401 – registra herramientas de gestión de proyectos
from . import artifacts  # noqa: F401 – registra herramientas de artifacts

# Importar módulos opcionales (pueden existir según la versión del sistema)
def _try_import(name: str) -> None:
    try:
        import importlib
        importlib.import_module(f"bbclaw.tools.{name}")
    except ImportError:
        pass

_try_import("memory")
_try_import("scheduling")
_try_import("skills_mgmt")

__all__ = ["registry", "ToolRegistry", "ToolDefinition", "ToolResult"]
