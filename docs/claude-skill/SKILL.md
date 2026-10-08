---
name: local-gpu-render
description: Use whenever a task needs GPU work or heavy computation that this 2-core / 2 GB VPS is not suited for — rendering frames or videos (WebGL/GLSL shader animations, motion graphics), Blender/Cycles renders, GPU compute (WebGPU/WebGL2), video encoding of many frames, or CPU-heavy parallel jobs. Runs them on the user's local Windows PC (<GPU>, <CPU>) over SSH host `render-local`, and brings back only small results (keyframes, contact sheets, logs, JSON).
---

# Local GPU rendering and compute (render-local)

The user's Windows PC is reachable from this VPS over Tailscale as SSH host **`render-local`**
(account `render`, non-admin, key-only). Use it instead of grinding on the VPS for anything
GPU-shaped or long-running. Do **not** silently fall back to the VPS for heavy GPU work: if the PC
is unreachable, tell the user.

Tooling repo (VPS): `<path-to-this-repo>` — read its `README.md` and
`docs/简化方案-v1.md` (sections 7–12) when you need details. CLI: `render/rr` (below as `$RR`).

## 0. Before anything: is the PC available?

```bash
ssh -o ConnectTimeout=10 render-local whoami        # expect: <计算机名>\render
tailscale status | grep -w "<local-machine>"                # "offline" => PC is off/asleep: tell the user
```

## 1. Shader / WebGL animation → frames → video (Chrome on the PC)

A scene is a directory with `index.js` (ES module) exporting `setup(ctx)` and `renderFrame(i, t)`,
`t = i / fps`, output depending only on (i, seed). `ctx = {gl (WebGL2), canvas, width, height, fps, frames, seed}`.
Example: `projects/demo/index.js` in the tooling repo.

```bash
RR=<path-to-this-repo>/render/rr
export RR_OUT="$PWD/render-runs"                      # results for the current project
$RR tools                                             # only if render/local/* changed
$RR push ./my-scene                                   # any dir with index.js (name = basename)
log=$($RR render my-scene -Width 1920 -Height 1080 -Fps 30 -Frames 150 -Only "0,75,149")
$RR wait "$log"                                       # JSON summary (survives SSH drops)
$RR fetch <run_id> frames                             # then LOOK at the PNGs (Read tool), iterate
log=$($RR render my-scene -Width 1920 -Height 1080 -Fps 30 -Frames 900 -Encode)
$RR wait "$log"; $RR fetch <run_id> contact           # verifies video on the PC, fetches a 4-frame sheet
$RR fetch <run_id> video                              # only if the user needs the file on the VPS
```

Workflow: keyframes first, inspect, fix, re-render; full video only when the frames look right.
Encoding is libx264 slow / crf 18 on the PC; parallel PNG + concurrent encoding is bit-identical
to serial (`-VerifyMd5` proves it). Videos stay on the PC under `D:\ClaudeRender\runs\<run_id>\video.mp4`.

## 2. GPU compute

- **Native WebGPU (fastest, ~1.7 TFLOPS f32 matmul)**: a module exporting `async run({params, seed, log})`
  that uses `navigator.gpu` runs unchanged under Dawn/D3D12:
  `$RR push ./job && ssh render-local "D:\ClaudeRender\tools\webgpu\node.exe D:\ClaudeRender\tools\claude\run-webgpu-job.mjs job n=2048"`
  (prints one JSON line). Example: `projects/gpu-matmul`.
- **WebGL2 in Chrome**: same module style, `$RR render job -Job [-JobParams '{"n":2048}']` → `result.json`.
- Browser WebGPU does **not** work over SSH (no adapter in session 0) — use the native runtime.

## 3. Blender (Cycles + OptiX)

`D:\ClaudeRender\tools\blender\blender.exe` (4.5 LTS). Put .blend files / python under
`D:\ClaudeRender\projects\<name>\` (scp), write output under `D:\ClaudeRender\runs\<name>-<UTC>\`.
Enable the GPU in a `--python-expr`/script (see `D:\ClaudeRender\tools\blender-gpu-probe.py`):
`ssh render-local "D:\ClaudeRender\tools\blender\blender.exe -b D:\...\scene.blend --python D:\...\use_gpu.py -o D:\...\frame_#### -a"`.
Check the log says OPTIX/GPU, not CPU. Fetch a few frames or encode on the PC with ffmpeg.

## 4. Anything else

`ssh render-local "<command>"` — Node (`node`), ffmpeg/ffprobe (`D:\ClaudeRender\tools\ffmpeg\bin\`),
`nvidia-smi`. For PowerShell, scp a `.ps1` and run `powershell.exe -NoProfile -ExecutionPolicy Bypass -File ...`
(inline `-Command` quoting through SSH breaks easily). Output is often GBK: pipe through `iconv -f GBK -t UTF-8 -c`
unless the script sets UTF-8. Keep all files under `D:\ClaudeRender\` (render cannot read the user's profile).

## Rules and pitfalls

- **Bandwidth VPS↔PC is ~1–2 MiB/s**: never pull full frame sequences; fetch keyframes, contact sheets, logs.
- A command that is the SSH session's **foreground** process keeps running if SSH drops (rr relies on this);
  processes started in the background die when the SSH command exits. No WMI / scheduled tasks for `render`.
- Chrome in session 0 needs `--use-angle=vulkan --disable-gpu-compositing` (render.ps1 default).
- Tools the PC must run need read+execute for `render` (ask the user/Codex to `icacls ... /grant render:(OI)(CI)RX`).
- Each run gets its own `D:\ClaudeRender\runs\<id>` (never overwritten); `SUCCESS` is written last.
  `$RR runs` / `$RR status <id>` show succeeded | failed | running | interrupted.
- Disk: frames at 1080p are ~1.5 MB each; clean up large test runs (ask before deleting user results).
  Use `[IO.DriveInfo]` for free space (Get-PSDrive shows 0 for `render`).
- Installing new software on the PC is the user's/Codex's job; ask instead of doing it.
