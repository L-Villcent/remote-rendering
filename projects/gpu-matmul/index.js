// Compute job (not a video): N x N float32 matrix multiplication on the GPU.
//   backend 'webgl2' (default): fragment shader writing an R32F texture (works in the SSH session)
//   backend 'webgpu': compute shader (no WebGPU adapter is available in session 0 headless Chrome)
// Verifies sampled entries against a float64 CPU reference.  params: { n, runs, samples, backend }
const WGSL = /* wgsl */ `
struct Dims { n : u32 }
@group(0) @binding(0) var<storage, read> A : array<f32>;
@group(0) @binding(1) var<storage, read> B : array<f32>;
@group(0) @binding(2) var<storage, read_write> C : array<f32>;
@group(0) @binding(3) var<uniform> dims : Dims;
const T : u32 = 16u;
var<workgroup> As : array<array<f32, 16>, 16>;
var<workgroup> Bs : array<array<f32, 16>, 16>;

@compute @workgroup_size(16, 16)
fn main(@builtin(global_invocation_id) g : vec3<u32>, @builtin(local_invocation_id) l : vec3<u32>) {
  let n = dims.n;
  let row = g.y; let col = g.x;
  var acc = 0.0;
  for (var t = 0u; t < n; t = t + T) {
    As[l.y][l.x] = A[row * n + t + l.x];
    Bs[l.y][l.x] = B[(t + l.y) * n + col];
    workgroupBarrier();
    for (var k = 0u; k < T; k = k + 1u) { acc = acc + As[l.y][k] * Bs[k][l.x]; }
    workgroupBarrier();
  }
  C[row * n + col] = acc;
}`;

function lcg(seed) {                     // deterministic inputs
  let s = seed >>> 0;
  return () => ((s = (Math.imul(s, 1664525) + 1013904223) >>> 0) / 4294967296) * 2 - 1;
}

function verify(A, B, C, n, samples, seed) {
  const pick = lcg(seed ^ 0x9e3779b9);
  let maxRel = 0;
  for (let s = 0; s < samples; s++) {
    const i = Math.floor((pick() + 1) / 2 * n) % n, j = Math.floor((pick() + 1) / 2 * n) % n;
    let ref = 0, mag = 0;
    for (let k = 0; k < n; k++) { const p = A[i * n + k] * B[k * n + j]; ref += p; mag += Math.abs(p); }
    maxRel = Math.max(maxRel, Math.abs(C[i * n + j] - ref) / mag);   // relative to the sum of |terms|
  }
  return maxRel;
}

function summary(task, n, runs, ms, samples, maxRel, adapter) {
  return {
    task, n, runs, ms_per_matmul: +ms.toFixed(3), gflops: +((2 * n ** 3) / (ms / 1000) / 1e9).toFixed(1),
    verified_entries: samples, max_rel_error: maxRel, verified: maxRel < 1e-5, adapter,
    software_fallback: /swiftshader|llvmpipe|microsoft basic/i.test(adapter),
  };
}

async function runWebGL2(n, runs, samples, seed) {
  const canvas = document.createElement('canvas');
  const gl = canvas.getContext('webgl2');
  if (!gl) throw new Error('webgl2_unavailable');
  if (!gl.getExtension('EXT_color_buffer_float')) throw new Error('no_float_render_target');
  const dbg = gl.getExtension('WEBGL_debug_renderer_info');
  const adapter = dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER);
  const rnd = lcg(seed);
  const A = new Float32Array(n * n).map(rnd), B = new Float32Array(n * n).map(rnd);
  const tex = (data) => {
    const t = gl.createTexture(); gl.bindTexture(gl.TEXTURE_2D, t);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.R32F, n, n, 0, gl.RED, gl.FLOAT, data); return t;
  };
  const tA = tex(A), tB = tex(B), tC = tex(null);
  const fb = gl.createFramebuffer(); gl.bindFramebuffer(gl.FRAMEBUFFER, fb);
  gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, tC, 0);
  if (gl.checkFramebufferStatus(gl.FRAMEBUFFER) !== gl.FRAMEBUFFER_COMPLETE) throw new Error('fbo_incomplete');
  const sh = (type, src) => { const x = gl.createShader(type); gl.shaderSource(x, src); gl.compileShader(x);
    if (!gl.getShaderParameter(x, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(x) || 'compile'); return x; };
  const pr = gl.createProgram();
  gl.attachShader(pr, sh(gl.VERTEX_SHADER, '#version 300 es\nin vec2 p; void main(){ gl_Position = vec4(p, 0.0, 1.0); }'));
  gl.attachShader(pr, sh(gl.FRAGMENT_SHADER, `#version 300 es
precision highp float; precision highp int; precision highp sampler2D;
uniform sampler2D A, B; uniform int n; out float c;
void main() {                         // texel (x=col, y=row); A[row][k] at (k,row), B[k][col] at (col,k)
  ivec2 o = ivec2(gl_FragCoord.xy); float acc = 0.0;
  for (int k = 0; k < n; k++) acc += texelFetch(A, ivec2(k, o.y), 0).r * texelFetch(B, ivec2(o.x, k), 0).r;
  c = acc;
}`));
  gl.linkProgram(pr); gl.useProgram(pr);
  const vb = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, vb);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
  const loc = gl.getAttribLocation(pr, 'p'); gl.enableVertexAttribArray(loc); gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
  gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, tA); gl.uniform1i(gl.getUniformLocation(pr, 'A'), 0);
  gl.activeTexture(gl.TEXTURE1); gl.bindTexture(gl.TEXTURE_2D, tB); gl.uniform1i(gl.getUniformLocation(pr, 'B'), 1);
  gl.uniform1i(gl.getUniformLocation(pr, 'n'), n); gl.viewport(0, 0, n, n);
  const one = new Float32Array(4);
  const draw = (count) => { for (let r = 0; r < count; r++) gl.drawArrays(gl.TRIANGLES, 0, 3); gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.FLOAT, one); };
  draw(1);                                            // warm-up
  const t0 = performance.now(); draw(runs); const ms = (performance.now() - t0) / runs;
  const rgba = new Float32Array(n * n * 4); gl.readPixels(0, 0, n, n, gl.RGBA, gl.FLOAT, rgba);
  const C = new Float32Array(n * n); for (let i = 0; i < n * n; i++) C[i] = rgba[i * 4];   // row-major: index = row*n + col
  return summary('webgl2-matmul-f32', n, runs, ms, samples, verify(A, B, C, n, samples, seed), adapter);
}

export async function run({ params, seed, log }) {
  const n = params.n || 2048, runs = params.runs || 10, samples = params.samples || 64;
  if ((params.backend || 'webgl2') === 'webgl2') return runWebGL2(n, runs, samples, seed);
  if (n % 16) throw new Error('n must be a multiple of 16');
  if (!navigator.gpu) throw new Error('webgpu_unavailable');
  const adapter = await navigator.gpu.requestAdapter({ powerPreference: 'high-performance' });
  if (!adapter) throw new Error('no_webgpu_adapter');
  const ai = adapter.info || {};
  const device = await adapter.requestDevice();

  const rnd = lcg(seed);
  const A = new Float32Array(n * n).map(rnd), B = new Float32Array(n * n).map(rnd);
  const usage = GPUBufferUsage.STORAGE | GPUBufferUsage.COPY_DST;
  const mk = (data) => { const b = device.createBuffer({ size: data.byteLength, usage }); device.queue.writeBuffer(b, 0, data); return b; };
  const bA = mk(A), bB = mk(B);
  const bC = device.createBuffer({ size: n * n * 4, usage: GPUBufferUsage.STORAGE | GPUBufferUsage.COPY_SRC });
  const bD = device.createBuffer({ size: 16, usage: GPUBufferUsage.UNIFORM | GPUBufferUsage.COPY_DST });
  device.queue.writeBuffer(bD, 0, new Uint32Array([n, 0, 0, 0]));
  const pipeline = device.createComputePipeline({ layout: 'auto', compute: { module: device.createShaderModule({ code: WGSL }), entryPoint: 'main' } });
  const bind = device.createBindGroup({ layout: pipeline.getBindGroupLayout(0), entries: [bA, bB, bC, bD].map((buffer, binding) => ({ binding, resource: { buffer } })) });

  const dispatch = (count) => {
    const enc = device.createCommandEncoder();
    for (let r = 0; r < count; r++) {
      const pass = enc.beginComputePass();
      pass.setPipeline(pipeline); pass.setBindGroup(0, bind); pass.dispatchWorkgroups(n / 16, n / 16); pass.end();
    }
    device.queue.submit([enc.finish()]);
    return device.queue.onSubmittedWorkDone();
  };
  await dispatch(1);                                   // warm-up (pipeline compile, clocks)
  const t0 = performance.now();
  await dispatch(runs);
  const ms = (performance.now() - t0) / runs;

  const read = device.createBuffer({ size: n * n * 4, usage: GPUBufferUsage.MAP_READ | GPUBufferUsage.COPY_DST });
  const enc = device.createCommandEncoder(); enc.copyBufferToBuffer(bC, 0, read, 0, n * n * 4); device.queue.submit([enc.finish()]);
  await read.mapAsync(GPUMapMode.READ);
  const C = new Float32Array(read.getMappedRange().slice(0)); read.unmap();

  return summary('webgpu-matmul-f32', n, runs, ms, samples, verify(A, B, C, n, samples, seed),
                 [ai.vendor, ai.architecture, ai.description].filter(Boolean).join(' / ') || 'unknown');
}
