// Minimal diagnostic scene: full-screen gradient driven by t.
let gl, uT;
export async function setup(ctx) {
  gl = ctx.gl;
  const vs = gl.createShader(gl.VERTEX_SHADER);
  gl.shaderSource(vs, '#version 300 es\nin vec2 p; void main(){ gl_Position = vec4(p, 0.0, 1.0); }');
  gl.compileShader(vs);
  const fs = gl.createShader(gl.FRAGMENT_SHADER);
  gl.shaderSource(fs, '#version 300 es\nprecision highp float; uniform float t; out vec4 o;\nvoid main(){ o = vec4(fract(gl_FragCoord.x / 640.0 + t), 0.4, 0.6, 1.0); }');
  gl.compileShader(fs);
  if (!gl.getShaderParameter(fs, gl.COMPILE_STATUS)) throw new Error('tri compile failed, lost=' + gl.isContextLost());
  const pr = gl.createProgram(); gl.attachShader(pr, vs); gl.attachShader(pr, fs); gl.linkProgram(pr); gl.useProgram(pr);
  const b = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, b);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
  const loc = gl.getAttribLocation(pr, 'p'); gl.enableVertexAttribArray(loc); gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
  uT = gl.getUniformLocation(pr, 't'); gl.viewport(0, 0, ctx.width, ctx.height);
}
export function renderFrame(i, t) { gl.uniform1f(uT, t); gl.drawArrays(gl.TRIANGLES, 0, 3); }
