<# inspect-run.ps1 -RunId <id>: verify a finished run on the local machine and build a small
   contact sheet (contact.jpg, 4 frames from video.mp4) so only small files travel to the VPS. #>
param([Parameter(Mandatory = $true)][string]$RunId, [string]$Root = 'D:\ClaudeRender',
      [string]$Ffmpeg = 'D:\ClaudeRender\tools\ffmpeg\bin\ffmpeg.exe', [string]$Ffprobe = 'D:\ClaudeRender\tools\ffmpeg\bin\ffprobe.exe')
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$run = Join-Path $Root "runs\$RunId"
$video = Join-Path $run 'video.mp4'
$out = [ordered]@{ run_id = $RunId; video = (Test-Path $video); success_marker = (Test-Path "$run\SUCCESS") }
if ($out.video) {
  $probe = & $Ffprobe -v error -select_streams v:0 -count_frames -show_entries stream=codec_name,width,height,r_frame_rate,nb_read_frames -show_entries format=duration,size -of json $video | ConvertFrom-Json
  $s = $probe.streams[0]
  $out.codec = $s.codec_name; $out.width = $s.width; $out.height = $s.height; $out.fps = $s.r_frame_rate
  $out.frames = [int]$s.nb_read_frames; $out.duration_s = [double]$probe.format.duration; $out.bytes = [int64]$probe.format.size
  if ($out.success_marker) { $out.success_after_video = (Get-Item "$run\SUCCESS").LastWriteTimeUtc -ge (Get-Item $video).LastWriteTimeUtc }
  $last = $out.frames - 1; $mid1 = [int]($out.frames / 3); $mid2 = [int](2 * $out.frames / 3)
  $vf = "select='eq(n\,0)+eq(n\,$mid1)+eq(n\,$mid2)+eq(n\,$last)',scale=640:-1,tile=2x2"
  & $Ffmpeg -hide_banner -loglevel error -y -i $video -vf $vf -frames:v 1 -q:v 3 "$run\contact.jpg"
  $out.contact_bytes = (Get-Item "$run\contact.jpg").Length
}
$out | ConvertTo-Json -Compress
