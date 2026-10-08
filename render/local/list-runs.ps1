<# list-runs.ps1 [-RunId <id>]: one line per run, or the full run.json of one run, with a computed state:
   succeeded | failed | running (runner process alive) | interrupted (no runner, no terminal status) #>
param([string]$RunId = '', [string]$Root = 'D:\ClaudeRender')
[Console]::OutputEncoding = [Text.Encoding]::UTF8
. (Join-Path $PSScriptRoot 'runs-lib.ps1')
Set-Alias State Get-RunState
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
