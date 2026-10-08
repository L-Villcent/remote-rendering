<# prune-runs.ps1 [-Days 14] [-Project <name>] [-Apply]
   Deletes run directories under runs\ older than -Days (by last write time) that are not running.
   With -Project only that project's runs are considered (use -Days 0 to clear a finished round).
   Without -Apply it only lists what would be deleted.  Never touches output\ or folders starting with '_'.
   Finished videos live in output\ (hard links), so deleting a run does not delete its published video. #>
param([ValidateRange(0, 365000)][int]$Days = 14, [string]$Project = '', [switch]$Apply, [string]$Root = 'D:\ClaudeRender')
$ErrorActionPreference = 'Stop'
if ($Project -and $Project -notmatch '^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$') { throw 'bad project name' }
[Console]::OutputEncoding = [Text.Encoding]::UTF8
. (Join-Path $PSScriptRoot 'runs-lib.ps1')
$runsRoot = [IO.Path]::GetFullPath((Join-Path $Root 'runs')).TrimEnd('\')
if (-not (Test-Path -LiteralPath $runsRoot -PathType Container)) { throw 'runs directory does not exist' }
if ((Get-Item -LiteralPath $runsRoot).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'runs directory must not be a link' }
$runPrefix = $runsRoot + '\'
$cut = (Get-Date).AddDays(-$Days)
$total = 0; $n = 0
Get-ChildItem -LiteralPath $runsRoot -Directory | Where-Object {
  $_.Name -match '^[A-Za-z0-9][A-Za-z0-9_-]{0,63}-\d{8}T\d{6}Z-[0-9a-f]{4}$' -and
  -not ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) -and $_.LastWriteTime -lt $cut -and
  (-not $Project -or $_.Name -match ('^' + [regex]::Escape($Project) + '-\d{8}T\d{6}Z-[0-9a-f]{4}$'))
} | Sort-Object Name | ForEach-Object {
  $candidate = [IO.Path]::GetFullPath($_.FullName)
  if (-not $candidate.StartsWith($runPrefix, [StringComparison]::OrdinalIgnoreCase)) { throw 'run path escapes runs directory' }
  # Do not recurse into junctions or symlinks when measuring or deleting a run.
  $pending = New-Object 'System.Collections.Generic.Stack[string]'
  $pending.Push($candidate)
  $hasLink = $false
  while ($pending.Count -gt 0 -and -not $hasLink) {
    foreach ($entry in Get-ChildItem -LiteralPath $pending.Pop() -Force) {
      if ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) { $hasLink = $true; break }
      if ($entry.PSIsContainer) { $pending.Push($entry.FullName) }
    }
  }
  if ($hasLink) { 'skipped: linked content in ' + $_.Name; return }
  $state = Get-RunState $candidate
  if ($state -eq 'running') { '{0,-12} {1}  (skipped: running)' -f $state, $_.Name; return }
  $b = Get-DirBytes $_.FullName; $total += $b; $n++
  '{0,-12} {1,8:N1} MB  {2}' -f $state, ($b / 1MB), $_.Name
  if ($Apply) { Remove-Item -LiteralPath $candidate -Recurse -Force }
}
'{0} {1} run(s), {2:N1} MB{3}' -f $(if ($Apply) { 'deleted' } else { 'would delete' }), $n, ($total / 1MB), $(if ($Apply) { '' } else { '  (dry run; add -Apply)' })
