---
name: local-gpu-render
description: Use whenever a task needs GPU work or heavy computation that this 2-core / 2 GB VPS is not suited for — rendering frames or videos (WebGL/GLSL shader animations, motion graphics), Blender/Cycles renders, GPU compute (WebGPU/WebGL2), video encoding of many frames, or CPU-heavy parallel jobs. Runs them on the user's local Windows PC (<GPU>, <CPU>) over SSH host `render-local`, publishes finished videos to D:\ClaudeRender\output, and brings back only small results (keyframes, contact sheets, logs, JSON).
---

# Local GPU rendering and compute (render-local)

The user's Windows PC is reachable from this VPS over Tailscale as SSH host **`render-local`**
(account `render`, non-admin, key-only). Use it instead of grinding on the VPS for anything
GPU-shaped or long-running. Do **not** silently fall back to the VPS for heavy GPU work: if the PC
is unreachable, tell the user.

Tooling repo (VPS): `<path-to-this-repo>` — `README.md` and
`docs/简化方案-v1.md` (sections 7–13) hold the details. CLI: `render/rr` (below as `$RR`).

```bash
RR=<path-to-this-repo>/render/rr
export RR_OUT="$PWD/render-runs"        # where fetched results + VPS-side logs go, per current project
```

## Folder layout on the PC (D:\ClaudeRender)

| Folder | Purpose | Lifetime |
|---|---|---|
| `projects\<name>\` | scene / job code pushed with `$RR push` | replaced on each push |
| `runs\<name>-<UTC>-<rand>\` | one working dir per run: scene snapshot, logs, run.json, keyframes, video | temporary — pruned |
| `output\<name>\` | **finished videos** for the user: `<name>_<yyyy-MM-dd_HHmm>[_<title>]_<W>x<H>_<fps>fps_<dur>s.mp4` + `.json` (params, sha256) + `.scene.zip` (exact code); `output\index.csv` lists them | permanent — never cleaned automatically |
| `tools\` | claude (rr side), ffmpeg, blender, webgpu | managed by user/Codex |

## Standard workflow for a video (follow these steps)

0. **Check the PC**: `ssh -o ConnectTimeout=10 render-local whoami` (expect `<计算机名>\render`);
   if it fails, `tailscale status | grep -w "<local-machine>"` — "offline" means off/asleep → tell the user.
1. **Write the scene**: a directory with `index.js` (ES module) exporting `setup(ctx)` and
   `renderFrame(i, t)`, `t = i / fps`, output depending only on (i, seed).
   `ctx = {gl (WebGL2), canvas, width, height, fps, frames, seed}`. Template: `projects/demo/index.js`.
   Decimal params as strings (no float JSON). Avoid `sin()`-based hashes (precision artefacts on Vulkan).
2. **Keyframes first**: `$RR push ./scene` → `log=$($RR render scene -Width W -Height H -Fps F -Frames N -Only "0,<mid>,<last>")`
   → `$RR wait "$log"` → `$RR fetch <run_id> frames` → **look at the PNGs** (Read tool). Fix and repeat until right.
3. **Final video**: `log=$($RR render scene ... -Encode [-Title short-label])` → `$RR wait "$log"`
   → `$RR fetch <run_id> contact` (verifies on the PC with ffprobe, fetches a 4-frame sheet) → look at it.
   On success the video is published to `D:\ClaudeRender\output\<scene>\…mp4` automatically
   (hard link, plus `.json` + `.scene.zip`, row in `index.csv`). Tell the user the output file name.
   Use `-NoPublish` for test/benchmark runs so `output` only holds real deliverables.
4. **Only if the user needs it on the VPS**: `$RR fetch <run_id> video`.
5. **Close the round** (no need to ask): as soon as the final is published and its contact sheet checked,
   delete that project's working runs — `$RR prune --project <scene> --apply` (all ages; keyframe previews,
   failed runs, `-NoPublish` tests). They have no further value: changes mean new renders, review keyframes
   already live on the VPS under `$RR_OUT`, the deliverable + params + exact code stay in `output`, and code
   history belongs on the VPS side. Only exceptions: a render still running, or a failure not yet diagnosed —
   prune after fixing it. The 14-day prune is just a safety net for forgotten runs.

Quality is fixed: libx264 preset slow, CRF 18 (change only with `-Crf` when asked). Parallel PNG + concurrent
encoding is bit-identical to serial (`-VerifyMd5`), and re-rendering the same code + params reproduces the
video byte for byte — so deleting runs never loses anything that the scene code cannot recreate.

## Housekeeping (standard, keep disk usage low)

- Automatic per run: the temporary Chrome profile is deleted; after a successful `-Encode`, PNG frames are
  deleted except first / middle / last (`-Keep` to choose, `-KeepFrames` to keep all — rarely needed).
- `$RR runs` lists runs with state (succeeded | failed | running | interrupted); `$RR status <id>` for one.
- What piles up between rounds: keyframe-only previews (a few MB), failed/interrupted runs (their partial frames
  are kept for debugging and can be hundreds of MB at 1080p), `-NoPublish` test encodes (video only in runs).
- `$RR prune --project <p> [--apply]`: clear one project's runs at the end of a round (standard, step 5).
- `$RR prune [days] [--apply]`: safety net for everything older than `days` (default 14).
  Both are dry runs without `--apply`, skip running runs, never touch `output` or `_`-prefixed diagnostic dirs.
  Ask the user before deleting anything inside `output`.
- Free space: use `[IO.DriveInfo]::new('D')` (Get-PSDrive shows 0 for `render`). 1080p PNG ≈ 1.5 MB/frame.

## GPU compute

- **Native WebGPU (fastest, ~1.7 TFLOPS f32 matmul)**: a module exporting `async run({params, seed, log})`
  using `navigator.gpu` runs unchanged under Dawn/D3D12:
  `$RR push ./job && ssh render-local "D:\ClaudeRender\tools\webgpu\node.exe D:\ClaudeRender\tools\claude\run-webgpu-job.mjs job n=2048"`
  (one JSON line). Example: `projects/gpu-matmul`.
- **WebGL2 in Chrome**: same module style, `$RR render job -Job [-JobParams '{"n":2048}']` → `result.json`.
- Browser WebGPU does **not** work over SSH (no adapter in session 0) — use the native runtime.

## Blender (Cycles + OptiX)

`D:\ClaudeRender\tools\blender\blender.exe` (4.5 LTS). Put .blend files / python under
`D:\ClaudeRender\projects\<name>\` (scp), write frames under a new `D:\ClaudeRender\runs\<name>-<UTC>\`.
Enable the GPU in a script (see `D:\ClaudeRender\tools\blender-gpu-probe.py`):
`ssh render-local "D:\ClaudeRender\tools\blender\blender.exe -b D:\...\scene.blend --python D:\...\use_gpu.py -o D:\...\frame_#### -a"`.
Check the log says OPTIX/GPU, not CPU. Encode on the PC with `D:\ClaudeRender\tools\ffmpeg\bin\ffmpeg.exe`
(libx264 slow, CRF 18) and save the final file under `D:\ClaudeRender\output\<name>\` with the same naming.

## Anything else

`ssh render-local "<command>"` — Node (`node`), ffmpeg/ffprobe (`D:\ClaudeRender\tools\ffmpeg\bin\`),
`nvidia-smi`. For PowerShell, scp a `.ps1` and run `powershell.exe -NoProfile -ExecutionPolicy Bypass -File ...`
(inline `-Command` quoting through SSH breaks easily). Output is often GBK: pipe through `iconv -f GBK -t UTF-8 -c`
unless the script sets UTF-8. Keep all files under `D:\ClaudeRender\` (render cannot read the user's profile).

## Pitfalls

- **Bandwidth VPS↔PC is ~1–2 MiB/s**: never pull full frame sequences; fetch keyframes, contact sheets, logs.
- A command that is the SSH session's **foreground** process keeps running if SSH drops (rr relies on this);
  processes started in the background die when the SSH command exits. No WMI / scheduled tasks for `render`.
- Chrome in session 0 needs `--use-angle=vulkan --disable-gpu-compositing` (render.ps1 default).
- Tools the PC must run need read+execute for `render` (ask the user/Codex to grant `(OI)(CI)RX`).
- Windows sleep is blocked during runs (`keep_awake_requested` in run.json; verified with powercfg).
- Installing new software on the PC is the user's/Codex's job; ask instead of doing it.
- When `render/local/*` changes, run `$RR tools` to upload it; keep the repo template
  `docs/claude-skill/SKILL.md` in sync with this file.
