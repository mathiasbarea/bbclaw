@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

:: ─── bbclaw launcher ─────────────────────────────────────────────────────
:: Uso: bb              → inicia el REPL + abre dashboard en browser
::      bb --no-dash    → solo el REPL, sin abrir el browser
::      bb --verbose    → logs detallados
:: ──────────────────────────────────────────────────────────────────────────

:: Parse --no-dash de los args
set "NODASH=0"
set "BBARGS="
for %%a in (%*) do (
    if /i "%%a"=="--no-dash" (
        set "NODASH=1"
    ) else (
        set "BBARGS=%BBARGS% %%a"
    )
)

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

:: 2. Instalar dependencias si bbclaw no está instalado
if not exist ".venv\Scripts\bbclaw.exe" (
    echo [bb] Instalando bbclaw...
    .venv\Scripts\pip install -e ".[dev]" -q
    if errorlevel 1 (
        echo [bb] ERROR: falló la instalación de dependencias.
        pause
        exit /b 1
    )
    echo [bb] OK. Listo.
)

:: 3. Rebuild dashboard si src/ es más nuevo que dist/
if exist "dashboard\src" (
    set "NEED_BUILD=0"
    if not exist "dashboard\dist\index.html" set "NEED_BUILD=1"
    if "!NEED_BUILD!"=="0" (
        for /f %%i in ('powershell -NoProfile -Command "(Get-ChildItem dashboard\src -Recurse | Sort-Object LastWriteTime -Descending | Select-Object -First 1).LastWriteTime.Ticks"') do set "SRC_T=%%i"
        for /f %%i in ('powershell -NoProfile -Command "(Get-Item dashboard\dist\index.html).LastWriteTime.Ticks"') do set "DIST_T=%%i"
        if !SRC_T! GTR !DIST_T! set "NEED_BUILD=1"
    )
    if "!NEED_BUILD!"=="1" (
        echo [bb] Dashboard desactualizado, rebuilding...
        pushd dashboard
        call npx vite build >nul 2>&1
        if errorlevel 1 (
            echo [bb] WARN: fallo el build del dashboard, usando dist anterior.
        ) else (
            echo [bb] Dashboard build OK.
        )
        popd
    )
)

:: 4. Abrir dashboard en el browser después de 3s (si no está --no-dash)
if "%NODASH%"=="0" (
    start "" /b cmd /c "ping -n 4 127.0.0.1 >nul 2>&1 && start http://127.0.0.1:8765/"
)

:: 5. Restart loop — exit code 42 = reiniciar pedido por el sistema
:restart_loop
.venv\Scripts\bbclaw%BBARGS%
if %errorlevel% EQU 42 (
    echo [bb] Reiniciando sistema...
    goto restart_loop
)
