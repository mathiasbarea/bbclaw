@echo off
setlocal
cd /d "%~dp0"

:: ─── bbclaud launcher ─────────────────────────────────────────────────────
:: Uso: bb           → inicia el REPL
::      bb --verbose → logs detallados
::      bb --help    → ayuda
:: ──────────────────────────────────────────────────────────────────────────

:: 1. Crear entorno virtual si no existe
if not exist ".venv\Scripts\python.exe" (
    echo [bb] Creando entorno virtual...
    python -m venv .venv
    if errorlevel 1 (
        echo [bb] ERROR: no se pudo crear el entorno virtual. ^¿Tenés Python 3.12+ instalado?
        pause
        exit /b 1
    )
)

:: 2. Instalar dependencias si bbclaud no está instalado
if not exist ".venv\Scripts\bbclaud.exe" (
    echo [bb] Instalando bbclaud...
    .venv\Scripts\pip install -e ".[dev]" -q
    if errorlevel 1 (
        echo [bb] ERROR: falló la instalación de dependencias.
        pause
        exit /b 1
    )
    echo [bb] ✓ Listo.
)

:: 3. Lanzar bbclaud con los argumentos recibidos
.venv\Scripts\bbclaud %*
