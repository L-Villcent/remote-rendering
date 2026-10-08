// Demo scene: domain-warped fbm "liquid" with orbiting light orbs.
// Contract: export setup(ctx) and renderFrame(i, t); everything depends only on (t, seed).

let gl, prog, uT, uRes, uSeed, uDur;
let duration = 5;

const VS = `#version 300 es
in vec2 p;
void main() { gl_Position = vec4(p, 0.0, 1.0); }`;

const FS = `#version 300 es
precision highp float;
uniform float t, seed, dur;
uniform vec2 res;
out vec4 o;

// Sine-free hash (Dave Hoskins): stable precision on every GPU backend.
float hash(vec2 p) {
  vec3 p3 = fract(vec3(p.xyx + seed * 17.13) * 0.1031);
  p3 += dot(p3, p3.yzx + 33.33);
  return fract((p3.x + p3.y) * p3.z);
}
float noise(vec2 p) {
  vec2 i = floor(p), f = fract(p);
  vec2 u = f * f * (3.0 - 2.0 * f);
  return mix(mix(hash(i), hash(i + vec2(1, 0)), u.x), mix(hash(i + vec2(0, 1)), hash(i + vec2(1, 1)), u.x), u.y);
}
float fbm(vec2 p) {
  float v = 0.0, a = 0.5;
  mat2 r = mat2(0.8, 0.6, -0.6, 0.8);
  for (int k = 0; k < 6; k++) { v += a * noise(p); p = r * p * 2.02; a *= 0.5; }
  return v;
}
vec3 palette(float x) {                          // deep indigo -> magenta -> amber
  return vec3(0.52, 0.42, 0.55) + vec3(0.48, 0.42, 0.45) * cos(6.28318 * (vec3(1.0, 1.0, 0.9) * x + vec3(0.02, 0.38, 0.62)));
}
void main() {
  vec2 uv = (gl_FragCoord.xy - 0.5 * res) / res.y;
  float ph = 6.28318 * t / dur;                    // one full loop over the clip
  vec2 drift = 0.35 * vec2(cos(ph), sin(ph));
  vec2 q = vec2(fbm(uv * 1.6 + drift), fbm(uv * 1.6 - drift + 3.1));
  vec2 r = vec2(fbm(uv * 1.6 + 2.2 * q + vec2(1.7, 9.2) + 0.15 * vec2(cos(ph), sin(ph))),
                fbm(uv * 1.6 + 2.2 * q + vec2(8.3, 2.8)));
  float f = fbm(uv * 1.6 + 2.6 * r);
  vec3 col = palette(f * 1.25 + 0.15 * length(q) + 0.1 * sin(ph));
  col *= 0.15 + 1.3 * f * f;
  for (int k = 0; k < 4; k++) {                    // orbs
    float a = ph + float(k) * 1.5708;
    vec2 c = 0.55 * vec2(cos(a), sin(2.0 * a) * 0.5);
    float d = length(uv - c);
    col += palette(float(k) * 0.25 + 0.1) * 0.012 / (d * d + 0.004);
  }
  col = col / (1.0 + col);                         // tone map
  col = pow(col, vec3(0.4545));
  float vig = smoothstep(1.25, 0.35, length(uv));
  o = vec4(col * vig, 1.0);
}`;

function compile(type, src) {
  const s = gl.createShader(type);
  gl.shaderSource(s, src);
  gl.compileShader(s);
  if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) {
    throw new Error(`shader compile failed (context_lost=${gl.isContextLost()}): ${gl.getShaderInfoLog(s) || 'no log'}`);
  }
  return s;
}

export async function setup(ctx) {
  gl = ctx.gl;
  duration = ctx.frames / ctx.fps;
  prog = gl.createProgram();
  gl.attachShader(prog, compile(gl.VERTEX_SHADER, VS));
  gl.attachShader(prog, compile(gl.FRAGMENT_SHADER, FS));
  gl.linkProgram(prog);
  if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(prog));
  gl.useProgram(prog);
  const buf = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, buf);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
  const loc = gl.getAttribLocation(prog, 'p');
  gl.enableVertexAttribArray(loc);
  gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
  uT = gl.getUniformLocation(prog, 't');
  uRes = gl.getUniformLocation(prog, 'res');
  uSeed = gl.getUniformLocation(prog, 'seed');
  uDur = gl.getUniformLocation(prog, 'dur');
  gl.viewport(0, 0, ctx.width, ctx.height);
  gl.uniform2f(uRes, ctx.width, ctx.height);
  gl.uniform1f(uSeed, ctx.seed);
  gl.uniform1f(uDur, duration);
}

export function renderFrame(i, t) {
  gl.uniform1f(uT, t);
  gl.drawArrays(gl.TRIANGLES, 0, 3);
}
