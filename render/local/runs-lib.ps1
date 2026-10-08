# Shared helpers for list-runs.ps1 / prune-runs.ps1 (dot-source).
function Get-RunState($dir) {
  if (Test-Path (Join-Path $dir 'SUCCESS')) { return 'succeeded' }
  $info = $null
  try { $info = Get-Content -Raw (Join-Path $dir 'run.json') | ConvertFrom-Json } catch { }
  if ($info -and $info.status -eq 'failed') { return 'failed' }
  $pidFile = Join-Path $dir 'runner.pid'
  if (Test-Path $pidFile) {
    $p = Get-Process -Id ([int](Get-Content $pidFile)) -ErrorAction SilentlyContinue
    if ($p -and $p.ProcessName -eq 'powershell') { return 'running' }
  }
  return 'interrupted'
}
function Get-DirBytes($dir) { (Get-ChildItem $dir -Recurse -File -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum }
