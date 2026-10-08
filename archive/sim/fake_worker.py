"""Simulated Worker for VPS-side end-to-end tests (NOT the real Worker).

It works directly on a config.Layout.simulated tree (no SFTP) and follows the
protocol order: attempt + ledger before download, leased status, local
re-validation, render (synthetic media), artifacts via .part + rename,
result.json last, cleanup on final acks, retransmit on 'rejected'.
Scenario knobs inject the faults the acceptance tests need.
"""
import hashlib
import json
import os
import pathlib
import secrets
import sys
import tarfile

HERE = pathlib.Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "refimpl"), str(HERE.parent / "server")]

from media_fixtures import mp4, png  # noqa: E402
from render_gates.ids import random_attempt_id  # noqa: E402
from render_protocol.bundle import check_tar  # noqa: E402
from render_protocol.semantics import expected_frames  # noqa: E402

TOOLCHAIN = {"worker_version": "0.1.0", "toolchain_id": "tc-sim-a",
             "browser": {"name": "chromium", "major": 140, "exe_sha256": hashlib.sha256(b"sim-browser").hexdigest()},
             "encoder": {"name": "ffmpeg", "version": "7.1", "exe_sha256": hashlib.sha256(b"sim-ffmpeg").hexdigest()}}
DEVICE = {"gpu_query": "available", "gpu_vendor": "nvidia", "gpu_model": "GeForce RTX 4070",
          "software_fallback": False, "gpu_busy_observed": True, "vram_peak_mb": 512}
NO_DEVICE = {"gpu_query": "not_attempted", "gpu_vendor": None, "gpu_model": None,
             "software_fallback": None, "gpu_busy_observed": None, "vram_peak_mb": None}


def _put(path: pathlib.Path, data: bytes):
    """Upload semantics: write .part-<name>, then rename over the final name (new inode)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".part-{path.name}"
    tmp.write_bytes(data)
    os.replace(tmp, path)


class FakeWorker:
    def __init__(self, layout, epoch=1, **scenario):
        self.lay, self.epoch, self.hb_seq = layout, epoch, 0
        self.ledger = {}            # job_id -> {"attempts": [...], "done": bool}
        self.sc = scenario          # leak_log, corrupt_times, contradict, fail_first, extra_sleep ...
        self.corrupted = 0

    # ------------------------------------------------------------ heartbeat
    def heartbeat(self, state="idle", job=None, att=None):
        self.hb_seq += 1
        doc = {"protocol_version": 1, "kind": "heartbeat", "worker_alias": "worker-local", "worker_epoch": self.epoch,
               "seq": self.hb_seq, "state": state, "active_job_id": job, "active_attempt_id": att,
               "worker_version": "0.1.0",
               "capabilities": {"adapters": [{"id": "webgl-frames", "version": "0.1.0"}],
                                "export_profiles": ["png-rgb8-v1", "h264-preview-v1"], "max_concurrent": 1}}
        _put(self.lay.up / "_worker" / "heartbeat.json", json.dumps(doc).encode())

    def status(self, job_id, att, no, seq, state, done=None, total=None):
        doc = {"protocol_version": 1, "kind": "status", "job_id": job_id, "attempt_id": att, "attempt_no": no,
               "worker_epoch": self.epoch, "seq": seq, "state": state}
        if done is not None:
            doc["progress"] = {"frames_done": done, "frames_total": total}
        _put(self.lay.up / "jobs" / job_id / att / "status.json", json.dumps(doc).encode())

    # ------------------------------------------------------------ main step
    def poll(self):
        """Claim and fully process every new job (single worker, one at a time)."""
        for job_dir in sorted((self.lay.down / "jobs").iterdir()):
            job_id = job_dir.name
            entry = self.ledger.get(job_id)
            if entry and (entry["done"] or not entry.get("retry")):
                continue
            no = len(entry["attempts"]) + 1 if entry else 1
            self._run_attempt(job_id, no)

    def _cancelled(self, job_id):
        return (self.lay.down / "control" / job_id / "cancel.json").exists()

    def _run_attempt(self, job_id, no):
        att = random_attempt_id()
        entry = self.ledger.setdefault(job_id, {"attempts": [], "done": False})
        entry["attempts"].append(att)
        entry["retry"] = False
        seq = 1
        self.status(job_id, att, no, seq, "leased")                       # ledger + attempt before download
        job = json.loads((self.lay.down / "jobs" / job_id / "job.json").read_text(encoding="utf-8"))
        bundle = (self.lay.down / "jobs" / job_id / "bundle.tar").read_bytes()
        base = {"protocol_version": 1, "kind": "result", "job_id": job_id, "attempt_id": att, "attempt_no": no,
                "worker_epoch": self.epoch, "bundle_sha256": job["bundle"]["sha256"], "adapter": job["adapter"],
                "local_text_check": {"rules_version": 1, "passed": True}}
        import io
        if hashlib.sha256(bundle).hexdigest() != job["bundle"]["sha256"] or check_tar(io.BytesIO(bundle), job["entrypoint"]):
            return self._finish(job_id, att, dict(base, outcome="failed", phase_reached="validate",
                                exit={"kind": "not_started", "code": None, "signal": None}, error_class="validation_error",
                                toolchain=dict(TOOLCHAIN, browser=None, encoder=None), device=NO_DEVICE,
                                frames={"expected": expected_frames(job), "rendered": 0}, timings_ms={"total": 1}), [])
        if self._cancelled(job_id):
            return self._finish(job_id, att, dict(base, outcome="cancelled", phase_reached="validate",
                                exit={"kind": "not_started", "code": None, "signal": None}, error_class="cancelled",
                                toolchain=dict(TOOLCHAIN, browser=None, encoder=None), device=NO_DEVICE,
                                frames={"expected": expected_frames(job), "rendered": 0}, timings_ms={"total": 1}), [])
        total = expected_frames(job)
        seq += 1
        self.status(job_id, att, no, seq, "running", 0, total)
        self.heartbeat("busy", job_id, att)
        if self.sc.get("fail_first") and no == 1:
            entry["retry"] = no < job["limits"]["max_attempts"]
            return self._finish(job_id, att, dict(base, outcome="failed", phase_reached="render",
                                exit={"kind": "exited", "code": 3221225477, "signal": None}, error_class="adapter_error",
                                retry_scheduled=entry["retry"], toolchain=TOOLCHAIN, device=DEVICE,
                                frames={"expected": total, "rendered": 1}, timings_ms={"total": 50}), [])
        files, arts = self._render(job, total)
        seq += 1
        self.status(job_id, att, no, seq, "uploading", total, total)
        seq += 1
        final_state = "cancelled" if self.sc.get("contradict") else "succeeded"
        self.status(job_id, att, no, seq, final_state)
        self._finish(job_id, att, dict(base, outcome="succeeded", phase_reached="upload",
                     exit={"kind": "exited", "code": 0, "signal": None}, error_class=None, toolchain=TOOLCHAIN,
                     device=DEVICE, frames={"expected": total, "rendered": total},
                     timings_ms={"prepare": 5, "render": 20, "encode": 5, "total": 35}, artifacts=arts), files)

    def _render(self, job, total):
        r = job["render"]
        log = f"webgl-frames 0.1.0 rendered {total} frames at {r['width']}x{r['height']}\n"
        if self.sc.get("leak_log"):
            log += "peer " + ".".join(["203", "0", "113", "9"]) + "\n"         # synthetic TEST-NET-3 leak
        files = {"log.txt": log.encode()}
        arts = []
        if "keyframe" in job["artifacts"]:
            for k in r["keyframes"]:
                files[f"keyframe-{k}.png"] = png(16, 9)
        if "contact" in job["artifacts"]:
            files["contact.png"] = png(32, 18)
        if "preview" in job["artifacts"]:
            files["preview.mp4"] = mp4(duration_s=total / r["fps"], w=r["width"], h=r["height"],
                                       udta=self.sc.get("udta", b""))
        for name, data in files.items():
            aid, ext = name.rsplit(".", 1)
            kind = aid.split("-")[0]
            a = {"artifact_id": aid, "kind": kind,
                 "media_type": {"txt": "text/plain", "png": "image/png", "mp4": "video/mp4"}[ext],
                 "delivery": "uploaded", "size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            if ext != "txt":
                a["export_profile"] = "png-rgb8-v1" if ext == "png" else "h264-preview-v1"
            if kind == "keyframe":
                a["frame_index"] = int(aid.split("-")[1])
            arts.append(a)
        return files, arts

    def _finish(self, job_id, att, result, files):
        if not files:
            files = {"log.txt": b"attempt ended early\n"}
            result.setdefault("artifacts", [])
            result["artifacts"] = [{"artifact_id": "log", "kind": "log", "media_type": "text/plain", "delivery": "uploaded",
                                    "size_bytes": len(files["log.txt"]),
                                    "sha256": hashlib.sha256(files["log.txt"]).hexdigest()}]
        self._upload(job_id, att, result, files)
        if not self.ledger[job_id].get("retry"):
            self.ledger[job_id]["pending"] = (att, result, files)

    def _upload(self, job_id, att, result, files):
        d = self.lay.up / "jobs" / job_id / att
        for name, data in files.items():
            if self.sc.get("corrupt_times", 0) > self.corrupted and name.endswith(".png"):
                data = data[:-1] + bytes([data[-1] ^ 1])
                self.corrupted += 1
            _put(d / "artifacts" / name, data)
        _put(d / "result.json", json.dumps(result).encode())              # result last
        self.ledger[job_id]["last_upload"] = (att, result, files)

    # ------------------------------------------------------------ acks
    def process_acks(self):
        for job_id, entry in self.ledger.items():
            for att in entry["attempts"]:
                p = self.lay.down / "acks" / job_id / f"{att}.json"
                if not p.exists():
                    continue
                ack = json.loads(p.read_text(encoding="utf-8"))
                key = (att, ack["received_at"], ack["verdict"], ack["rejected_count"])
                if entry.get("acked", {}).get(att) == key:
                    continue
                entry.setdefault("acked", {})[att] = key
                if ack["verdict"] == "rejected":
                    _, result, files = entry["last_upload"]
                    self._upload(job_id, att, result, files)                 # retransmit same attempt
                elif ack["verdict"] in ("accepted", "quarantined", "delivery_failed", "stale", "conflict"):
                    import shutil
                    shutil.rmtree(self.lay.up / "jobs" / job_id / att, ignore_errors=True)
                    if not entry.get("retry"):
                        entry["done"] = True
