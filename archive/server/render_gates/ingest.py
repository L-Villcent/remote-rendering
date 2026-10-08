"""render-ingest: up/ -> inbox, acks and views (protocol-v1 5, 6.5, 6.7, 6.8).

run_light(): heartbeats, statuses, cancels, health tick, views   (never blocked by results)
run_heavy(): result deliveries and handoff reports

Untrusted input (up/) is always snapshotted first.  State is persisted under
ingest_private/state so a restart resumes with WorkerHealth 'unconfirmed'.
Liveness uses the injected monotonic clock; wall clock only stamps received_at.
"""
import contextlib
import fcntl
import json
import os
import re
import resource
import shutil
import time
from dataclasses import asdict

from render_protocol.canonical import ProtocolJSONError, parse_strict
from render_protocol.semantics import UPLOAD_CAP, check_heartbeat, check_result, check_status, expected_frames
from render_protocol.statemachine import Attempt, JobTracker, WorkerHealth

from . import ids, mediacheck, textrules
from .config import Limits, load_json
from .fsutil import SnapshotError, atomic_write_json, list_entries, private_dir, snapshot

STATUS_URN = "urn:render-protocol:status:v1"
RESULT_URN = "urn:render-protocol:result:v1"
JOB_ID = re.compile(r"^j_[0-9A-HJKMNP-TV-Z]{26}$")
ATTEMPT_ID = re.compile(r"^a_[0-9A-HJKMNP-TV-Z]{26}$")
REPORT_FILE = re.compile(r"^(local-[0-9]{8}-[0-9]{2})\.md$")
EXT = {"text/plain": "txt", "image/png": "png", "video/mp4": "mp4"}
INGEST_VERSION = "0.1.0"
FINAL_VERDICTS = {"accepted", "quarantined", "delivery_failed"}
CACHE_MAX = 20000
LIGHT_SAMPLE_EVERY = 12           # record every 12th light-loop timing (plus any slow one)
LIGHT_SLOW_S = 1.0


class Ingest:
    def __init__(self, layout, schema, profiles, toolchains, limits=Limits(), expect_uid=None, clock=None, mono=None):
        import time
        self.lay, self.schema, self.lim, self.uid = layout, schema, limits, expect_uid
        self.profiles, self.toolchains = profiles, toolchains
        self.clock, self.mono = clock or time.time, mono or time.monotonic
        self.state_dir = layout.ingest_private / "state"
        w = load_json(self.state_dir / "worker.json", {"last": None, "last_received_at": None, "hb": None})
        self.worker = w
        self.health = WorkerHealth(limits.lease_seconds, persisted=w["last"])
        self.metrics = []                      # (artifact_id, bytes_read, structures, seconds) for MX-05
        self._light_runs = 0
        (self.state_dir / "locks").mkdir(parents=True, exist_ok=True)
        (layout.ingest_private / "metrics").mkdir(parents=True, exist_ok=True)

    # ================================================================ locking / metrics / cache
    @contextlib.contextmanager
    def _locked(self, name):
        """Per-job (or per-resource) lock: light and heavy loops run as separate processes."""
        fd = os.open(self.state_dir / "locks" / f"{name}.lock", os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _metric(self, **fields):
        """Admin-only numeric metrics (MX-05, HB-10); never written to inbox."""
        fields["t"] = self._now()
        day = fields["t"][:10]
        line = (json.dumps(fields, separators=(",", ":")) + "\n").encode("utf-8")
        fd = os.open(self.lay.ingest_private / "metrics" / f"{day}.jsonl",
                     os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_CLOEXEC, 0o600)
        try:
            os.write(fd, line)
        finally:
            os.close(fd)

    def _cache_get(self, key):
        return load_json(self.state_dir / "cache.json", {}).get(key)

    def _cache_put(self, key, codes):
        with self._locked("_cache"):
            c = load_json(self.state_dir / "cache.json", {})
            c[key] = codes
            while len(c) > CACHE_MAX:
                c.pop(next(iter(c)))
            atomic_write_json(self.state_dir, "cache.json", c)

    # ================================================================ state helpers
    def _job_state(self, job_id):
        return load_json(self.state_dir / "jobs" / f"{job_id}.json")

    def _tracker(self, st):
        t = JobTracker(st["job_id"], st["max_attempts"], self.lim.upload_retry_limit)
        t.current_no, t.terminal, t.cancel = st["current_no"], st["terminal"], st["cancel"]
        t.attempts = {int(k): Attempt(**v) for k, v in st["attempts"].items()}
        return t

    def _save_job(self, st, t):
        st.update(current_no=t.current_no, terminal=t.terminal, cancel=t.cancel,
                  attempts={str(k): asdict(v) for k, v in t.attempts.items()})
        atomic_write_json(self.state_dir / "jobs", f"{st['job_id']}.json", st)

    def _job_def(self, job_id):
        _, _, raw = snapshot(self.lay.down, f"jobs/{job_id}/job.json", self.lim.submission_bytes)
        return json.loads(raw)

    def _now(self):
        return ids.utc_now(self.clock())

    # ================================================================ light loop
    def run_light(self):
        t0 = time.monotonic()
        now_mono = self.mono()
        for job_id, is_dir in list_entries(self.lay.down, "jobs"):
            if is_dir and JOB_ID.match(job_id) and self._job_state(job_id) is None:
                with self._locked(job_id):
                    if self._job_state(job_id) is None:
                        job = self._job_def(job_id)
                        st = {"job_id": job_id, "stage": job["stage"], "max_attempts": job["limits"]["max_attempts"],
                              "current_no": 0, "terminal": None, "cancel": "none", "attempts": {},
                              "rx": {}, "cancel_requests": [], "deliveries": {}}
                        self._save_job(st, self._tracker(st))
        self._heartbeat(now_mono)
        self.health.tick(now_mono)
        self.worker["health"] = self.health.state
        atomic_write_json(self.state_dir, "worker.json", self.worker)
        self._worker_view()
        for job_id, is_dir in list_entries(self.lay.down, "control"):
            if is_dir and JOB_ID.match(job_id):
                self._cancel(job_id)
        for job_id, is_dir in list_entries(self.lay.up, "jobs"):
            if not (is_dir and JOB_ID.match(job_id)):
                continue
            for att, is_dir2 in list_entries(self.lay.up, f"jobs/{job_id}"):
                if is_dir2 and ATTEMPT_ID.match(att):
                    self._status(job_id, att)
        for name, _ in list_entries(self.state_dir, "jobs"):
            if name.endswith(".json"):
                with self._locked(name[:-5]):
                    self._job_view(name[:-5])
        took = time.monotonic() - t0
        self._light_runs += 1
        if took > LIGHT_SLOW_S or self._light_runs % LIGHT_SAMPLE_EVERY == 1:
            self._metric(kind="light_loop", ms=round(took * 1000, 2))

    def _heartbeat(self, now_mono):
        try:
            _, _, raw = snapshot(self.lay.up, "_worker/heartbeat.json", self.lim.heartbeat_bytes, expect_uid=self.uid)
            hb = parse_strict(raw)
        except (SnapshotError, ProtocolJSONError):
            return
        if not self.schema.is_valid(hb, STATUS_URN + "#/$defs/heartbeat") or check_heartbeat(hb):
            return
        ok, _ = self.health.on_heartbeat(hb["worker_epoch"], hb["seq"], now_mono)
        if not ok:
            return
        now = self._now()
        self.worker.update(last=[hb["worker_epoch"], hb["seq"]], last_received_at=now, hb=hb)
        atomic_write_json(self.lay.down / "acks" / "_worker", "heartbeat.json", {
            "protocol_version": 1, "kind": "heartbeat_ack", "worker_alias": "worker-local",
            "accepted_epoch": hb["worker_epoch"], "accepted_seq": hb["seq"], "issued_at": now})

    def _worker_view(self):
        hb = self.worker.get("hb")
        last = self.worker.get("last")
        atomic_write_json(self.lay.inbox / "workers", "worker-local.json", {
            "protocol_version": 1, "kind": "worker_view", "worker_alias": "worker-local", "health": self.health.state,
            "last_epoch": last[0] if last else None, "last_seq": last[1] if last else None,
            "last_received_at": self.worker.get("last_received_at"),
            "state": hb["state"] if hb else None, "active_job_id": hb["active_job_id"] if hb else None,
            "capabilities": hb["capabilities"] if hb else None, "updated_at": self._now()})

    def _cancel(self, job_id):
        with self._locked(job_id):
            self._cancel_locked(job_id)

    def _cancel_locked(self, job_id):
        st = self._job_state(job_id)
        if st is None:
            return
        try:
            _, _, raw = snapshot(self.lay.down, f"control/{job_id}/cancel.json", 4096)
            req = json.loads(raw)["request_id"]
        except (SnapshotError, KeyError, ValueError):
            return
        if req in st["cancel_requests"]:
            return
        st["cancel_requests"].append(req)
        t = self._tracker(st)
        t.on_cancel()
        self._save_job(st, t)

    def _status(self, job_id, att):
        with self._locked(job_id):
            self._status_locked(job_id, att)

    def _status_locked(self, job_id, att):
        st = self._job_state(job_id)
        if st is None:
            return
        try:
            _, _, raw = snapshot(self.lay.up, f"jobs/{job_id}/{att}/status.json", self.lim.status_bytes, expect_uid=self.uid)
            doc = parse_strict(raw)
        except (SnapshotError, ProtocolJSONError):
            return
        if (not self.schema.is_valid(doc, STATUS_URN + "#/$defs/status") or doc["attempt_id"] != att
                or check_status(self._job_def(job_id), doc)):
            return
        t = self._tracker(st)
        p = doc.get("progress")
        ok, _ = t.on_status(att, doc["attempt_no"], doc["seq"], doc["state"], p["frames_done"] if p else None)
        if ok:
            st["rx"][att] = {"last_received_at": self._now(), "progress": p}
            self._save_job(st, t)

    def _job_view(self, job_id):
        st = self._job_state(job_id)
        t = self._tracker(st)
        state, overlays = t.display(self.health.state)
        cur = None
        if t.current_no:
            a = t.attempts[t.current_no]
            rx = st["rx"].get(a.attempt_id, {})
            cur = {"attempt_id": a.attempt_id, "attempt_no": a.attempt_no, "exec_state": a.exec_state,
                   "last_status_seq": a.last_seq, "last_received_at": rx.get("last_received_at"),
                   "progress": rx.get("progress"), "verdict": a.verdict if a.verdict in
                   ("accepted", "quarantined", "rejected", "delivery_failed") else None, "rejected_count": a.rejected}
        view = {"protocol_version": 1, "kind": "job_view", "job_id": job_id, "stage": st["stage"],
                "display_state": state, "overlays": overlays, "cancel": t.cancel, "current_attempt": cur,
                "attempts_seen": len(t.attempts)}
        old = load_json(self.lay.inbox / "jobs" / job_id / "status.json", {})
        if {k: v for k, v in old.items() if k != "updated_at"} != view:
            view["updated_at"] = self._now()
            atomic_write_json(self.lay.inbox / "jobs" / job_id, "status.json", view)

    # ================================================================ heavy loop
    def run_heavy(self):
        for job_id, is_dir in list_entries(self.lay.up, "jobs"):
            if not (is_dir and JOB_ID.match(job_id)) or self._job_state(job_id) is None:
                continue
            for att, is_dir2 in list_entries(self.lay.up, f"jobs/{job_id}"):
                if not (is_dir2 and ATTEMPT_ID.match(att)):
                    continue
                try:
                    st = os.lstat(self.lay.up / "jobs" / job_id / att / "result.json")
                except OSError:
                    continue
                with self._locked(job_id):
                    state = self._job_state(job_id)
                    seen = state["deliveries"].setdefault(att, [])
                    if st.st_ino in seen:
                        continue                # this delivery was already judged
                    seen.append(st.st_ino)
                    atomic_write_json(self.state_dir / "jobs", f"{job_id}.json", state)
                    t0 = time.monotonic()
                    verdict = self._result(job_id, att)
                    self._metric(kind="delivery", verdict=verdict, ms=round((time.monotonic() - t0) * 1000, 2),
                                 max_rss_kb=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
                    self._job_view(job_id)
        for name, is_dir in list_entries(self.lay.up, "handoff"):
            m = REPORT_FILE.match(name)
            if m and not is_dir:
                self._handoff(m.group(1))

    def _precheck(self, t, att, no):
        """Attempt identity before any content work (protocol 6.7 step 3.1)."""
        if t.terminal:
            return "stale", "superseded_attempt"
        if no is None:
            known = [a for a in t.attempts.values() if a.attempt_id == att]
            if not known:
                return "conflict", "attempt_conflict"
            no = known[0].attempt_no
        if no > t.max_attempts:
            return "conflict", "attempt_limit_exceeded"
        for a in t.attempts.values():
            if (a.attempt_id == att) != (a.attempt_no == no):
                return "conflict", "attempt_conflict"
        if no < t.current_no:
            return "stale", "superseded_attempt"
        a = t.attempts.get(no)
        if a is not None and a.verdict in FINAL_VERDICTS:
            return "stale", "superseded_attempt"
        return None, None

    def _result(self, job_id, att):
        st = self._job_state(job_id)
        t = self._tracker(st)
        job = self._job_def(job_id)
        snapdir = private_dir(self.lay.ingest_private / "snap", "res")
        reasons, res, cache_hit = [], None, False
        try:
            _, _, raw = snapshot(self.lay.up, f"jobs/{job_id}/{att}/result.json", self.lim.result_bytes, expect_uid=self.uid)
            res = parse_strict(raw)
        except SnapshotError as e:
            reasons.append((e.code, "result.json"))
        except ProtocolJSONError:
            reasons.append(("malformed_json", "result.json"))
        if res is not None and (not self.schema.is_valid(res, RESULT_URN) or res["attempt_id"] != att):
            reasons.append(("schema_invalid", "result.json"))
            res = None
        known_no = next((a.attempt_no for a in t.attempts.values() if a.attempt_id == att), None)
        no = res["attempt_no"] if res else known_no
        verdict, why = self._precheck(t, att, no)
        if verdict is None:
            if res is not None:
                more, cache_hit = self._content(job, res, snapdir)
                reasons += more
            if no is None:
                verdict, why = "conflict", "attempt_conflict"
            else:
                outcome = res["outcome"] if res else (t.attempts[no].exec_state if no in t.attempts else None)
                prev = t.attempts.get(no)
                if res and prev is not None and prev.exec_state in ("succeeded", "failed", "cancelled") \
                        and prev.exec_state != outcome:
                    reasons.append(("outcome_mismatch", "result.json#/outcome"))   # same rule the tracker applies
                verdict = t.on_result(att, no, outcome, [r for r, _ in reasons],
                                      bool(res and res.get("retry_scheduled")))
                self._save_job(st, t)
        if verdict in ("stale", "conflict"):
            reasons = [(why, "result.json")]
        a = t.attempts.get(no) if no else None
        record = {"protocol_version": 1, "kind": "ingest_record", "job_id": job_id, "attempt_id": att,
                  "attempt_no": no, "received_at": self._now(), "verdict": verdict,
                  "retry_allowed": verdict == "rejected", "rejected_count": a.rejected if a else 0,
                  "reasons": [] if verdict == "accepted" else [{"rule": r, "ref": ref} for r, ref in reasons],
                  "rules_version": textrules.RULES_VERSION, "profiles_version": self.profiles["profiles_version"],
                  "ingest_version": INGEST_VERSION, "cache_hit": cache_hit}
        atomic_write_json(self.lay.down / "acks" / job_id, f"{att}.json", record)
        target = self.lay.inbox / "jobs" / job_id / att
        stage = private_dir(self.lay.ingest_private / "stage", "pub")
        if verdict == "accepted":
            for f in snapdir.iterdir():
                os.rename(f, stage / f.name)
            (stage / "result.json").write_bytes(json.dumps(res, indent=2, ensure_ascii=False).encode("utf-8") + b"\n")
        elif verdict == "quarantined":
            q = private_dir(self.lay.ingest_private / "quarantine", f"{job_id}-{att}")
            for f in snapdir.iterdir():
                os.rename(f, q / f.name)
        atomic_write_json(stage, "ingest.json", record)
        if target.exists():
            shutil.rmtree(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        os.rename(stage, target)
        shutil.rmtree(snapdir, ignore_errors=True)
        return verdict

    def _content(self, job, res, snapdir):
        """Returns (reasons, cache_hit). Size and hash are always verified; only the
        structure/text verdict of an identical artifact is reused from the cache."""
        reasons = [(c, "result.json") for c in check_result(job, res)]
        if reasons:
            return reasons, False
        for a in res["artifacts"]:
            if a["delivery"] != "uploaded":
                continue
            name = f"{a['artifact_id']}.{EXT[a['media_type']]}"
            try:
                sha, size, _ = snapshot(self.lay.up, f"jobs/{res['job_id']}/{res['attempt_id']}/artifacts/{name}",
                                        UPLOAD_CAP[a["kind"]], dest=snapdir / name, expect_uid=self.uid)
            except SnapshotError as e:
                reasons.append((e.code, a["artifact_id"]))
                continue
            if sha != a["sha256"] or size != a["size_bytes"]:
                reasons.append(("hash_mismatch", a["artifact_id"]))
        if reasons:
            return reasons, False
        tc = res["toolchain"]
        if tc["toolchain_id"] is not None:
            reg = self.toolchains.get(tc["toolchain_id"])
            if (reg is None or any(tc[k] is not None and tc[k]["exe_sha256"] != reg.get(k, {}).get("exe_sha256")
                                   for k in ("browser", "encoder"))):
                return [("toolchain_unregistered", "result.json#/toolchain")], False
        hits = checked = 0
        for a in res["artifacts"]:
            if a["delivery"] != "uploaded":
                continue
            path = snapdir / f"{a['artifact_id']}.{EXT[a['media_type']]}"
            key = f"{a['sha256']}:{textrules.RULES_VERSION}:{self.profiles['profiles_version']}:{a.get('export_profile', 'text')}"
            checked += 1
            cached = self._cache_get(key)
            if cached is not None:
                hits += 1
                reasons += [(c, a["artifact_id"]) for c in cached]
                continue
            if a["media_type"] == "text/plain":
                text = path.read_bytes().decode("utf-8", errors="replace")
                codes = sorted(textrules.scan_text(text))
            else:
                try:
                    s = mediacheck.check(path, a["export_profile"], self.profiles)
                    self.metrics.append((a["artifact_id"], s.bytes_read, s.structures, s.seconds))
                    self._metric(kind="artifact", profile=a["export_profile"], bytes_read=s.bytes_read,
                                 structures=s.structures, ms=round(s.seconds * 1000, 3))
                    codes = []
                except mediacheck.MediaError as e:
                    codes = [e.code]
            self._cache_put(key, codes)
            reasons += [(c, a["artifact_id"]) for c in codes]
        reasons += textrules.scan_json(res, "result.json")
        return reasons, (checked > 0 and hits == checked)

    # ================================================================ handoff
    def _handoff(self, report_id):
        done = load_json(self.state_dir / "handoff.json", {})
        try:
            ino = os.lstat(self.lay.up / "handoff" / f"{report_id}.md").st_ino
        except OSError:
            return
        if done.get(report_id) == ino:
            return
        done[report_id] = ino
        atomic_write_json(self.state_dir, "handoff.json", done)
        try:
            _, _, raw = snapshot(self.lay.up, f"handoff/{report_id}.md", self.lim.handoff_bytes, expect_uid=self.uid)
            text = raw.decode("utf-8")
            rules = sorted(textrules.scan_text(text))
        except (SnapshotError, UnicodeDecodeError):
            raw, rules = None, ["unreadable"]
        record = {"protocol_version": 1, "kind": "handoff_record", "report_id": report_id,
                  "verdict": "accepted" if not rules else "quarantined",
                  "reasons": [{"rule": r, "ref": "handoff"} for r in rules], "received_at": self._now()}
        if rules:
            if raw is not None:
                q = private_dir(self.lay.ingest_private / "quarantine", f"handoff-{report_id}")
                (q / "report.md").write_bytes(raw)
        else:
            from .fsutil import atomic_write_bytes
            atomic_write_bytes(self.lay.inbox / "handoff", f"{report_id}.md", raw)
        atomic_write_json(self.lay.inbox / "handoff", f"{report_id}.record.json", record)
        atomic_write_json(self.lay.down / "acks" / "_handoff", f"{report_id}.json", record)
