# bbclaud launcher — PowerShell
# Uso: .\bb           → inicia el REPL
#      .\bb --verbose  → logs detallados
#      .\bb --help     → ayuda

Set-Location $PSScriptRoot

# 1. Crear entorno virtual si no existe
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "[bb] Creando entorno virtual..." -ForegroundColor Cyan
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[bb] ERROR: no se pudo crear el entorno virtual. ¿Tenés Python 3.12+?" -ForegroundColor Red
        exit 1
    }
}

# 2. Instalar si bbclaud no está
if (-not (Test-Path ".venv\Scripts\bbclaud.exe")) {
    Write-Host "[bb] Instalando bbclaud..." -ForegroundColor Cyan
    & .venv\Scripts\pip install -e ".[dev]" -q
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[bb] ERROR: falló la instalación." -ForegroundColor Red
        exit 1
    }
    Write-Host "[bb] ✓ Listo." -ForegroundColor Green
}

# 3. Lanzar
& .venv\Scripts\bbclaud @args
