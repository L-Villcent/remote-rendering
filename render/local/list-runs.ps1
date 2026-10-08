<# list-runs.ps1 [-RunId <id>]: one line per run, or the full run.json of one run, with a computed state:
   succeeded | failed | running (runner process alive) | interrupted (no runner, no terminal status) #>
param([string]$RunId = '', [string]$Root = 'D:\ClaudeRender')
[Console]::OutputEncoding = [Text.Encoding]::UTF8
function State($dir) {
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
if ($RunId) {
  $dir = Join-Path $Root "runs\$RunId"
  if (-not (Test-Path $dir)) { '{"state":"missing"}'; exit 0 }
  $info = [ordered]@{ state = (State $dir) }
  try { (Get-Content -Raw "$dir\run.json" | ConvertFrom-Json).psobject.Properties | ForEach-Object { $info[$_.Name] = $_.Value } } catch { }
  $info | ConvertTo-Json -Depth 5 -Compress
} else {
  Get-ChildItem "$Root\runs" -Directory | Where-Object { $_.Name -notlike '_*' } | Sort-Object Name | ForEach-Object {
    '{0,-12} {1}' -f (State $_.FullName), $_.Name
  }
}
