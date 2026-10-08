// Frame harness: deterministic time (t = i / fps).
//   mode=png     each frame -> canvas.toBlob(PNG) -> POST /frame?i=N; up to `inflight` frames in flight
//   mode=stream  each frame -> readPixels(RGBA, bottom-up rows) -> POST /raw?i=N, strictly in order;
//                the runner pipes the bytes into ffmpeg. Frames listed in `keep` are also saved as PNG.
// Both modes hand the encoder exactly the same 8-bit RGBA pixels; only the transport differs.
const q = new URLSearchParams(location.search);
const W = +q.get('w'), H = +q.get('h'), FPS = +q.get('fps'), N = +q.get('frames'), SEED = +q.get('seed');
const MODE = q.get('mode') || 'png';
const INFLIGHT = Math.max(1, +(q.get('inflight') || 1));
const only = q.get('only') ? q.get('only').split(',').map(Number) : [...Array(N).keys()];
const keep = new Set(q.get('keep') ? q.get('keep').split(',').map(Number) : []);

async function post(path, body, type) {
  const r = await fetch(path, { method: 'POST', body, headers: { 'content-type': type } });
  if (!r.ok) throw new Error(`${path} -> ${r.status}`);
}

function toPng(canvas) {
  return new Promise((res) => canvas.toBlob(res, 'image/png'));
}

// Compute jobs: a module exporting run(ctx) is executed once; its JSON result goes to /result.
async function job() {
  const mod = await import('/scene/index.js');
  const params = JSON.parse(q.get('params') || '{}');
  const t0 = performance.now();
  const result = await mod.run({ params, seed: SEED, log: (m) => post('/log', String(m), 'text/plain') });
  const ms = performance.now() - t0;
  await post('/result', JSON.stringify(result), 'application/json');
  await post('/done', JSON.stringify({
    mode: 'job', frames: 0, render_ms: Math.round(ms), export_ms: 0,
    renderer: result.adapter || 'n/a', software_fallback: result.software_fallback === true,
  }), 'application/json');
}

async function main() {
  if (q.get('job') === '1') return job();
  const canvas = document.createElement('canvas');
  canvas.width = W; canvas.height = H;
  document.body.appendChild(canvas);
  canvas.addEventListener('webglcontextlost', () => { post('/log', 'webglcontextlost', 'text/plain').catch(() => {}); });
  const gl = canvas.getContext('webgl2', { preserveDrawingBuffer: true, antialias: false, premultipliedAlpha: false });
  if (!gl) throw new Error('webgl2_unavailable');
  const dbg = gl.getExtension('WEBGL_debug_renderer_info');
  const renderer = dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER);
  const scene = await import('/scene/index.js');
  await scene.setup({ gl, canvas, width: W, height: H, fps: FPS, frames: N, seed: SEED });

  let renderMs = 0, exportMs = 0;
  const px = new Uint8Array(4);
  const full = MODE === 'stream' ? new Uint8Array(W * H * 4) : null;
  const pending = [];
  const t0all = performance.now();

  for (const i of only) {
    const t0 = performance.now();
    scene.renderFrame(i, i / FPS);
    gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, px);   // forces GPU completion: render time is real
    const t1 = performance.now();
    renderMs += t1 - t0;
    if (MODE === 'stream') {
      gl.readPixels(0, 0, W, H, gl.RGBA, gl.UNSIGNED_BYTE, full);
      await post(`/raw?i=${i}`, full, 'application/octet-stream');   // body is copied at call time
      if (keep.has(i)) await post(`/frame?i=${i}`, await toPng(canvas), 'image/png');
      exportMs += performance.now() - t1;
    } else {
      // toBlob snapshots the canvas now and encodes off the main thread; keep several in flight.
      const job = toPng(canvas).then((blob) => post(`/frame?i=${i}`, blob, 'image/png'));
      pending.push(job);
      if (pending.length >= INFLIGHT) await pending.shift();
    }
  }
  await Promise.all(pending);
  if (MODE !== 'stream') exportMs = performance.now() - t0all - renderMs;   // overlapped with rendering

  await post('/done', JSON.stringify({
    mode: MODE, frames: only.length, render_ms: Math.round(renderMs), export_ms: Math.round(exportMs), renderer,
    software_fallback: /swiftshader|llvmpipe|softpipe|software|basic render/i.test(renderer),
  }), 'application/json');
}

main().catch(async (e) => {
  try { await post('/error', JSON.stringify({ error: String((e && e.stack) || e) }), 'application/json'); } catch (_) { /* runner times out */ }
});
