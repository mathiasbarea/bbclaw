"""
Configuración de identidad del sistema.
El nombre se toma de la variable de entorno SYSTEM_NAME.
"""

import os

SYSTEM_NAME: str = os.environ.get("SYSTEM_NAME", "BBCLAW")
