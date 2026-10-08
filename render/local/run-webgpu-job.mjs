// Run a project's run(ctx) on native WebGPU (Dawn for Node, d3d12) instead of Chrome.
//   D:\ClaudeRender\tools\webgpu\node.exe run-webgpu-job.mjs <project> [key=value ...]
// Values that look numeric are passed as numbers. Prints one JSON line; exit code 0 = success.
import { pathToFileURL } from 'node:url';
import { create, globals } from 'file:///D:/ClaudeRender/tools/webgpu/node_modules/webgpu/index.js';

Object.assign(globalThis, globals);
const [project, ...kv] = process.argv.slice(2);
if (!/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/.test(project || '')) { console.error('usage: run-webgpu-job.mjs <project> [k=v...]'); process.exit(2); }
const params = Object.fromEntries(kv.map((p) => { const [k, v] = p.split('='); return [k, v !== '' && !isNaN(+v) ? +v : v]; }));
const gpu = create(['backend=' + (params.dawn_backend || 'd3d12'), 'adapter=NVIDIA']);
Object.defineProperty(globalThis, 'navigator', { value: { gpu }, configurable: true });

try {
  const mod = await import(pathToFileURL(`D:\\ClaudeRender\\projects\\${project}\\index.js`).href);
  const t0 = performance.now();
  const result = await mod.run({ params: { backend: 'webgpu', ...params }, seed: params.seed ?? 42, log: (m) => console.error(m) });
  console.log(JSON.stringify({ runtime: 'dawn-node', job_ms: Math.round(performance.now() - t0), ...result }));
  process.exit(result.verified === false || result.software_fallback ? 1 : 0);
} catch (e) {
  console.log(JSON.stringify({ runtime: 'dawn-node', error: String(e && e.message || e) }));
  process.exit(1);
}
