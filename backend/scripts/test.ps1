# Run the backend tests with the project's virtual environment, never the global Python.
$python = Join-Path $PSScriptRoot "..\.venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Error "No .venv found. Run: python -m venv .venv; .venv\Scripts\pip install -r requirements.txt"
    exit 1
}
Push-Location (Join-Path $PSScriptRoot "..")
try { & $python -m pytest @args; exit $LASTEXITCODE } finally { Pop-Location }
