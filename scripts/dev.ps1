# (--timeout-graceful-shutdown: open SSE streams otherwise stall uvicorn's auto-reload on Windows)
# Oracle Arena dev: uvicorn (8000, hot reload) + vite (5173). MOCK_LLM=true by default (฿0).
#   scripts\dev.ps1            # mock mode
#   scripts\dev.ps1 -Real      # real LLM calls (budget guard applies)
param([switch]$Real)
$root = Split-Path -Parent $PSScriptRoot
$env:MOCK_LLM = if ($Real) { "false" } else { "true" }
Write-Host "MOCK_LLM=$($env:MOCK_LLM)  API http://localhost:8000  UI http://localhost:5173"
$api = Start-Process -PassThru -NoNewWindow -WorkingDirectory $root `
  -FilePath "$root\.venv\Scripts\python.exe" -ArgumentList "-m","uvicorn","api.main:app","--port","8000","--reload","--reload-dir","api","--reload-dir","core","--reload-dir","agents","--timeout-graceful-shutdown","3"
try {
  Push-Location "$root\web"
  npm run dev
} finally {
  Pop-Location
  # kill the whole tree: stopping only the reloader would orphan its uvicorn worker (still holding :8000)
  if ($api -and -not $api.HasExited) { taskkill /T /F /PID $api.Id | Out-Null }
}
