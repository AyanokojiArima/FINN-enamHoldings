# FINN - one-command start on Windows (PowerShell 5+). Needs Python 3.10+.
# Creates backend\.venv, installs dependencies, and serves the app + API on port 8000.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location (Join-Path $root "backend")

$py = if (Get-Command py -ErrorAction SilentlyContinue) { "py" } else { "python" }
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "Creating virtual environment..."
    & $py -m venv .venv
}
Write-Host "Installing dependencies..."
& .\.venv\Scripts\python.exe -m pip install --quiet --disable-pip-version-check -r requirements.txt
if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env" }

if (-not (Test-Path "..\frontend\dist\index.html")) {
    Write-Host "Building the frontend (needs Node 20+)..."
    Push-Location ..\frontend; npm ci; npm run build; Pop-Location
}

$ip = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -notmatch '^(127\.|169\.254\.)' -and $_.InterfaceAlias -notmatch 'vEthernet|Loopback' } |
    Select-Object -First 1 -ExpandProperty IPAddress
Write-Host ""
Write-Host "FINN is starting:"
Write-Host "  this computer : http://localhost:8000"
if ($ip) { Write-Host "  your phone    : http://${ip}:8000  (same Wi-Fi; allow Python through the firewall if asked)" }
Write-Host ""
& .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
