<#
  render.ps1 - render one run of a scene with headless Chrome (Vulkan) and optionally encode it.
  Runs in the foreground until rendering (and encoding) finished; prints one JSON summary line.

  .\render.ps1 -Project demo -Width 1280 -Height 720 -Fps 30 -Frames 150 [-Only "0,75,149"] [-Encode]
               [-Mode png|stream] [-Inflight 4] [-Keep "0,75,149"] [-VerifyMd5]

  Modes (identical pixels reach the encoder, identical encoder settings):
    png     frames saved as PNG files (inspectable); -Inflight frames are PNG-compressed in parallel and,
            with -Encode, fed to ffmpeg in frame order while rendering continues (concurrent encoding)
    stream  raw RGBA frames piped straight into ffmpeg while rendering (no PNG round trip);
            frames listed in -Keep are additionally saved as PNG.  Requires -Encode and a full run.
  -VerifyMd5 writes logs\input.framemd5: MD5 of every frame exactly as handed to the encoder.

  Layout (all under D:\ClaudeRender):
    projects\<Project>\index.js   scene module: export setup(ctx), renderFrame(i, t)
    runs\<RunId>\scene\            snapshot of the project used for this run
    runs\<RunId>\frames\00000.png  frames
    runs\<RunId>\logs\              runner / browser / ffmpeg logs
    runs\<RunId>\video.mp4          only with -Encode, full runs
    runs\<RunId>\run.json           parameters, timings, status
    runs\<RunId>\SUCCESS            written last (after encoding when -Encode)
#>
param(
  [Parameter(Mandatory = $true)][string]$Project,
  [string]$RunId = '',
  [int]$Width = 1280, [int]$Height = 720, [int]$Fps = 30, [int]$Frames = 150, [int]$Seed = 42,
  [string]$Only = '',
  [switch]$Encode,
  [int]$Crf = 18,
  [int]$TimeoutSec = 1800,
  [string]$Root = 'D:\ClaudeRender',
  [string]$Ffmpeg = 'D:\ClaudeRender\tools\ffmpeg\bin\ffmpeg.exe',
  [string]$Angle = 'vulkan',
  [string]$ExtraArgs = '--disable-gpu-compositing',
  [ValidateSet('png', 'stream')][string]$Mode = 'png',
  [int]$Inflight = 4,
  [string]$Keep = '',
  [switch]$VerifyMd5,
  [switch]$Job,                     # run the module's run(ctx) once (GPU compute etc.); result -> result.json
  [string]$JobParams = '{}'
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$sw = [Diagnostics.Stopwatch]::StartNew()

if ($Project -notmatch '^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$') { throw 'bad project name' }
$projectDir = Join-Path $Root "projects\$Project"
if (-not (Test-Path (Join-Path $projectDir 'index.js'))) { throw "project not found: $Project" }
$browser = @('C:\Program Files\Google\Chrome\Application\chrome.exe',
             'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe') | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $browser) { throw 'no browser' }
if ($Mode -eq 'stream' -and -not $Encode) { throw 'stream mode needs -Encode' }
if ($Encode -and $Only) { throw '-Encode needs a full run (no -Only)' }
if (($Encode -or $VerifyMd5) -and -not (Test-Path $Ffmpeg)) { throw 'ffmpeg missing' }
$toolDir = $PSScriptRoot

# ---- independent run directory (never reused) ----------------------------------------
if ($RunId) {
  if ($RunId -notmatch ('^' + [regex]::Escape($Project) + '-\d{8}T\d{6}Z-[0-9a-f]{4}$')) { throw 'bad run id' }
  $runId = $RunId
} else {
  $runId = '{0}-{1}-{2}' -f $Project, (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ'), ([Guid]::NewGuid().ToString('N').Substring(0, 4))
}
$run = Join-Path $Root "runs\$runId"
if (Test-Path $run) { throw "run dir exists: $runId" }
New-Item -ItemType Directory -Path $run, "$run\frames", "$run\logs" | Out-Null
Copy-Item -Recurse -Path $projectDir -Destination "$run\scene"
Set-Content -Path "$run\runner.pid" -Value $PID
# Keep Windows awake while this run is active (released automatically when the process exits).
Add-Type -Namespace Win32 -Name Power -MemberDefinition '[DllImport("kernel32.dll")] public static extern uint SetThreadExecutionState(uint esFlags);'
$keepAwake = [Win32.Power]::SetThreadExecutionState([uint32]'0x80000001')   # ES_CONTINUOUS | ES_SYSTEM_REQUIRED; 0 = failed
$runLog = "$run\logs\runner.log"
function Log($m) { Add-Content -Path $runLog -Value ('{0:HH:mm:ss.fff} {1}' -f (Get-Date), $m) -Encoding UTF8 }

$info = [ordered]@{
  run_id = $runId; project = $Project; status = 'rendering'; width = $Width; height = $Height; fps = $Fps;
  frames_total = $Frames; only = $Only; seed = $Seed; encode = [bool]$Encode; browser = (Split-Path $browser -Leaf)
  mode = $Mode; inflight = $Inflight; keep = $Keep; crf = $Crf; keep_awake_requested = ($keepAwake -ne 0)
}
# One encoder configuration for both modes (quality must not depend on the transport).
$encArgs = @('-c:v', 'libx264', '-preset', 'slow', '-crf', "$Crf", '-pix_fmt', 'yuv420p',
             '-movflags', '+faststart', '-map_metadata', '-1', '-fflags', '+bitexact', '-flags:v', '+bitexact')
$md5Args = @('-pix_fmt', 'rgba', '-f', 'framemd5', "$run\logs\input.framemd5")
function Save-Info { $info | ConvertTo-Json -Depth 5 | Set-Content -Path "$run\run.json" -Encoding UTF8 }
Save-Info
Log "run $runId started"

# ---- local HTTP endpoint (localhost only) ----------------------------------------------
$port = Get-Random -Minimum 20000 -Maximum 40000
$listener = [Net.HttpListener]::new()
$listener.Prefixes.Add("http://localhost:$port/")
$listener.Start()
$types = @{ '.html' = 'text/html'; '.js' = 'text/javascript'; '.mjs' = 'text/javascript'; '.json' = 'application/json';
            '.png' = 'image/png'; '.jpg' = 'image/jpeg'; '.glsl' = 'text/plain'; '.txt' = 'text/plain'; '.wasm' = 'application/wasm' }
$sceneRoot = [IO.Path]::GetFullPath("$run\scene")

function Send($ctx, [int]$code, [byte[]]$bytes, [string]$type) {
  $ctx.Response.StatusCode = $code
  if ($type) { $ctx.Response.ContentType = $type }
  $ctx.Response.Headers['Cache-Control'] = 'no-store'
  if ($bytes) { $ctx.Response.OutputStream.Write($bytes, 0, $bytes.Length) }
  $ctx.Response.Close()
}
function ReadBody($ctx) {
  $ms = [IO.MemoryStream]::new(); $ctx.Request.InputStream.CopyTo($ms); return ,$ms.ToArray()
}

$query = "w=$Width&h=$Height&fps=$Fps&frames=$Frames&seed=$Seed&mode=$Mode&inflight=$Inflight"
if ($Only) { $query += "&only=$Only" }
if ($Keep) { $query += "&keep=$Keep" }
if ($Job) { $query += '&job=1&params=' + [Uri]::EscapeDataString($JobParams) }

# ---- with -Encode the encoder starts first; frames are piped into its stdin in order ----
$ff = $null; $ffIn = $null; $frameBytes = [int64]$Width * $Height * 4
$pngQueue = @{}; $nextFrame = 0
if ($Encode) {
  if ($Mode -eq 'stream') {
    $inArgs = @('-hide_banner', '-loglevel', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgba', '-s', "${Width}x${Height}",
                '-framerate', "$Fps", '-i', '-')
    $outArgs = @('-vf', 'vflip') + $encArgs + @("$run\video.mp4")    # readPixels rows are bottom-up
    if ($VerifyMd5) { $outArgs += @('-vf', 'vflip') + $md5Args }
  } else {
    $inArgs = @('-hide_banner', '-loglevel', 'error', '-f', 'image2pipe', '-c:v', 'png', '-framerate', "$Fps", '-i', '-')
    $outArgs = $encArgs + @("$run\video.mp4")
    if ($VerifyMd5) { $outArgs += $md5Args }
  }
  $cmdLine = '""' + $Ffmpeg + '" ' + (($inArgs + $outArgs | ForEach-Object { if ($_ -match '[\s"]') { '"' + $_ + '"' } else { $_ } }) -join ' ') +
             ' 2>"' + "$run\logs\ffmpeg.txt" + '""'
  $psi = [Diagnostics.ProcessStartInfo]::new('cmd.exe', "/d /s /c $cmdLine")
  $psi.UseShellExecute = $false; $psi.RedirectStandardInput = $true; $psi.CreateNoWindow = $true
  $ff = [Diagnostics.Process]::Start($psi)
  $ffIn = $ff.StandardInput.BaseStream
  $info.status = 'rendering+encoding'; Save-Info
  Log "encoder pid $($ff.Id) ($Mode)"
}
$chromeArgs = @('--headless=new', "--use-angle=$Angle", '--ignore-gpu-blocklist', '--no-first-run', '--no-default-browser-check',
                '--disable-background-networking', '--disable-sync', '--disable-extensions', "--user-data-dir=$run\chrome-profile",
                "http://localhost:$port/harness.html?$query")
if ($ExtraArgs) { $chromeArgs = @($ExtraArgs -split ' ' | Where-Object { $_ }) + $chromeArgs }
$info.chrome_args = ($chromeArgs | Select-Object -SkipLast 1) -join ' '
$smi = 'C:\Windows\System32\nvidia-smi.exe'
$smiProc = $null
if (Test-Path $smi) {
  $smiProc = Start-Process -FilePath $smi -PassThru -WindowStyle Hidden -RedirectStandardOutput "$run\logs\gpu-util.csv" `
    -ArgumentList @('--query-gpu=utilization.gpu,memory.used', '--format=csv,noheader,nounits', '-lms', '500')
}
$chrome = Start-Process -FilePath $browser -ArgumentList $chromeArgs -PassThru -WindowStyle Hidden `
          -RedirectStandardError "$run\logs\chrome-stderr.txt" -RedirectStandardOutput "$run\logs\chrome-stdout.txt"
Log "chrome pid $($chrome.Id) port $port"

$done = $null; $failure = $null; $received = 0; $kept = 0; $firstFrameMs = $null; $gotResult = $false
$deadline = (Get-Date).AddSeconds($TimeoutSec)
try {
  while (-not $done -and -not $failure) {
    if ((Get-Date) -gt $deadline) { $failure = 'timeout'; break }
    $async = $listener.BeginGetContext($null, $null)
    while (-not $async.AsyncWaitHandle.WaitOne(1000)) {
      if ($chrome.HasExited) { $failure = 'browser_exited'; break }
      if ((Get-Date) -gt $deadline) { $failure = 'timeout'; break }
    }
    if ($failure) { break }
    $ctx = $listener.EndGetContext($async)
    $path = $ctx.Request.Url.AbsolutePath
    if ($ctx.Request.HttpMethod -eq 'GET') {
      if ($path -eq '/harness.html' -or $path -eq '/harness.js') {
        $f = Join-Path $toolDir $path.TrimStart('/')
      } elseif ($path.StartsWith('/scene/')) {
        $f = [IO.Path]::GetFullPath((Join-Path $sceneRoot ([Uri]::UnescapeDataString($path.Substring(7)) -replace '/', '\')))
        if (-not $f.StartsWith($sceneRoot + '\')) { $f = $null }
      } else { $f = $null }
      if ($f -and (Test-Path -LiteralPath $f -PathType Leaf)) {
        Send $ctx 200 ([IO.File]::ReadAllBytes($f)) $types[[IO.Path]::GetExtension($f).ToLower()]
      } else { Send $ctx 404 $null 'text/plain' }
      continue
    }
    switch ($path) {
      '/raw' {
        if ($ctx.Request.ContentLength64 -ne $frameBytes) { $failure = 'bad_frame_size'; Send $ctx 400 $null $null; break }
        if ($ff.HasExited) { $failure = 'encode_failed'; Send $ctx 500 $null $null; break }
        $ctx.Request.InputStream.CopyTo($ffIn)
        $received++
        if ($null -eq $firstFrameMs) { $firstFrameMs = $sw.ElapsedMilliseconds; Log "first frame at $firstFrameMs ms" }
        Send $ctx 204 $null $null
      }
      '/frame' {
        $i = [int]$ctx.Request.QueryString['i']
        $png = ReadBody $ctx
        [IO.File]::WriteAllBytes(('{0}\frames\{1:D5}.png' -f $run, $i), $png)
        if ($Mode -eq 'stream') { $kept++; Send $ctx 204 $null $null; break }
        if ($ff) {                                   # reorder: frames may arrive out of order
          $pngQueue[$i] = $png
          while ($pngQueue.ContainsKey($nextFrame)) {
            if ($ff.HasExited) { $failure = 'encode_failed'; break }
            $b = $pngQueue[$nextFrame]; $ffIn.Write($b, 0, $b.Length); $pngQueue.Remove($nextFrame); $nextFrame++
          }
        }
        $received++
        if ($null -eq $firstFrameMs) { $firstFrameMs = $sw.ElapsedMilliseconds; Log "first frame at $firstFrameMs ms" }
        Send $ctx 204 $null $null
      }
      '/result' { [IO.File]::WriteAllBytes("$run\result.json", (ReadBody $ctx)); $gotResult = $true; Send $ctx 204 $null $null }
      '/log'   { Add-Content "$run\logs\browser.log" ([Text.Encoding]::UTF8.GetString((ReadBody $ctx))); Send $ctx 204 $null $null }
      '/done'  { $done = [Text.Encoding]::UTF8.GetString((ReadBody $ctx)) | ConvertFrom-Json; $doneMs = $sw.ElapsedMilliseconds; Send $ctx 204 $null $null }
      '/error' { $failure = 'page_error'; Set-Content "$run\logs\page-error.txt" ([Text.Encoding]::UTF8.GetString((ReadBody $ctx))); Send $ctx 204 $null $null }
      default  { Send $ctx 404 $null 'text/plain' }
    }
  }
} finally {
  $listener.Stop()
  if (-not $chrome.HasExited) {
    try { $chrome.Kill(); [void]$chrome.WaitForExit(10000) } catch { Log "browser kill failed: $($_.Exception.Message)" }
  }
}
if ($smiProc -and -not $smiProc.HasExited) { try { $smiProc.Kill(); [void]$smiProc.WaitForExit(5000) } catch { } }
$browserMs = $sw.ElapsedMilliseconds
$util = @(Get-Content "$run\logs\gpu-util.csv" -ErrorAction SilentlyContinue | ForEach-Object { ($_ -split ',')[0].Trim() } | Where-Object { $_ -match '^\d+$' } | ForEach-Object { [int]$_ })
Log "browser phase finished: received=$received failure=$failure"

$info.frames_received = $received
if ($Mode -eq 'stream') { $info.keyframes_saved = $kept }
$info.timings_ms = [ordered]@{ browser_start_to_first_frame = $firstFrameMs; browser_phase = $browserMs }
$info.gpu_util = [ordered]@{ samples = $util.Count; peak = $(if ($util.Count) { ($util | Measure-Object -Maximum).Maximum } else { $null }) }
if ($done) {
  $info.renderer = $done.renderer; $info.software_fallback = $done.software_fallback
  $info.timings_ms.render = $done.render_ms; $info.timings_ms.frame_export = $done.export_ms
}
if (-not $failure -and $done.software_fallback) { $failure = 'software_fallback' }
if (-not $failure -and $Job -and -not $gotResult) { $failure = 'no_result' }
if ($Job) { $info.mode = 'job'; $info.timings_ms.job = $done.render_ms; $info.timings_ms.Remove('render'); $info.timings_ms.Remove('frame_export') }

# ---- finish the encoder (it has been encoding while frames arrived) -----------------------
if ($ff) {
  try { $ffIn.Close() } catch { }
  [void]$ff.WaitForExit(600000)
  $info.timings_ms.encode_tail_after_last_frame = $(if ($doneMs) { $sw.ElapsedMilliseconds - $doneMs } else { $null })
  if (-not $failure) {
    if ($received -ne $Frames) { $failure = 'missing_frames' }
    elseif ($ff.ExitCode -ne 0) { $failure = 'encode_failed' }
    else { $info.video_bytes = (Get-Item "$run\video.mp4").Length }
  }
}

$info.timings_ms.total = $sw.ElapsedMilliseconds
if ($failure) { $info.status = 'failed'; $info.failure = $failure } else { $info.status = 'succeeded' }
Save-Info
if (-not $failure) { Set-Content -Path "$run\SUCCESS" -Value (Get-Date).ToUniversalTime().ToString('o') }
Log "status $($info.status)"
$info | ConvertTo-Json -Depth 5 -Compress
if ($failure) { exit 1 }
