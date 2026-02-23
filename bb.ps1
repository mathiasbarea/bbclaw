# bbclaw launcher - PowerShell
# Uso: .\bb              -> inicia el REPL + abre dashboard en browser
#      .\bb --no-dash    -> solo el REPL, sin abrir el browser
#      .\bb --verbose    -> logs detallados

Set-Location $PSScriptRoot

# Parse --no-dash
$noDash      = $args -contains '--no-dash'
$filteredArgs = @($args | Where-Object { $_ -ne '--no-dash' })

# 1. Crear entorno virtual si no existe
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "[bb] Creando entorno virtual..." -ForegroundColor Cyan
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[bb] ERROR: no se pudo crear el entorno virtual." -ForegroundColor Red
        exit 1
    }
}

# 2. Instalar bbclaw si no esta instalado
if (-not (Test-Path ".venv\Scripts\bbclaw.exe")) {
    Write-Host "[bb] Instalando bbclaw..." -ForegroundColor Cyan
    & .venv\Scripts\pip install -e ".[dev]" -q
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[bb] ERROR: fallo la instalacion." -ForegroundColor Red
        exit 1
    }
    Write-Host "[bb] OK. Listo." -ForegroundColor Green
}

# 3. Rebuild dashboard si src/ es más nuevo que dist/
if (Test-Path "dashboard\src") {
    $needBuild = -not (Test-Path "dashboard\dist\index.html")
    if (-not $needBuild) {
        $srcTime = (Get-ChildItem "dashboard\src" -Recurse | Sort-Object LastWriteTime -Descending | Select-Object -First 1).LastWriteTime
        $distTime = (Get-Item "dashboard\dist\index.html").LastWriteTime
        if ($srcTime -gt $distTime) { $needBuild = $true }
    }
    if ($needBuild) {
        Write-Host "[bb] Dashboard desactualizado, rebuilding..." -ForegroundColor Cyan
        Push-Location dashboard
        & npx vite build 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) {
            Write-Host "[bb] WARN: fallo el build del dashboard, usando dist anterior." -ForegroundColor Yellow
        } else {
            Write-Host "[bb] Dashboard build OK." -ForegroundColor Green
        }
        Pop-Location
    }
}

# 4. Abrir dashboard en el browser después de 3s (si no esta --no-dash)
if (-not $noDash) {
    Start-Job -ScriptBlock {
        Start-Sleep 3
        Start-Process "http://127.0.0.1:8765/"
    } | Out-Null
}

# 5. Restart loop — exit code 42 = reiniciar pedido por el sistema
do {
    & .venv\Scripts\bbclaw @filteredArgs
} while ($LASTEXITCODE -eq 42)
