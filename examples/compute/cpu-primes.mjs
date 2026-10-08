// Count primes below N with a segmented sieve, split across all CPU cores (worker_threads).
import { Worker, isMainThread, parentPort, workerData } from 'node:worker_threads';
import os from 'node:os';
import { fileURLToPath } from 'node:url';
const N = 200_000_000;
function sieveRange(lo, hi) {
  const lim = Math.floor(Math.sqrt(hi)) + 1, small = new Uint8Array(lim + 1), primes = [];
  for (let i = 2; i <= lim; i++) if (!small[i]) { primes.push(i); for (let j = i * i; j <= lim; j += i) small[j] = 1; }
  const seg = new Uint8Array(hi - lo);
  for (const p of primes) { let s = Math.max(p * p, Math.ceil(lo / p) * p); for (let j = s; j < hi; j += p) seg[j - lo] = 1; }
  let c = 0; for (let i = Math.max(lo, 2); i < hi; i++) if (!seg[i - lo]) c++;
  return c;
}
if (isMainThread) {
  const cores = os.cpus().length, t0 = performance.now(), step = Math.ceil(N / cores);
  const parts = await Promise.all([...Array(cores).keys()].map((k) => new Promise((res, rej) => {
    const w = new Worker(fileURLToPath(import.meta.url), { workerData: [k * step, Math.min(N, (k + 1) * step)] });
    w.on('message', res); w.on('error', rej);
  })));
  const ms = performance.now() - t0;
  console.log(JSON.stringify({ task: 'cpu-primes', below: N, primes: parts.reduce((a, b) => a + b, 0), expected: 11078937,
    cores, cpu: os.cpus()[0].model.trim(), ms: Math.round(ms) }));
} else {
  parentPort.postMessage(sieveRange(...workerData));
}
