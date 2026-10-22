# Run the back end (port 8000) and the front end dev server (port 5173) together on Windows.
# Usage (from the repo root):  .\scripts\dev.ps1
$root = Split-Path -Parent $PSScriptRoot
$backend = Start-Process -PassThru -NoNewWindow -WorkingDirectory "$root\backend" -FilePath "python" -ArgumentList "-m", "uvicorn", "app.main:app", "--reload", "--port", "8000"
try {
  Set-Location "$root\frontend"
  npm run dev
} finally {
  if ($backend -and -not $backend.HasExited) { Stop-Process -Id $backend.Id }
}
