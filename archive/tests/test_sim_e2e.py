"""Group 5 - SIM-E2E: renderctl -> publish -> simulated Worker -> ingest -> renderctl.

All in a temporary tree (config.Layout.simulated); no SFTP, no services, no network.
Maps to the simulated parts of acceptance PR-01..18, HB-01..06/10, TX-02/04/06,
GT-07/08, MX-03 and FS-12.  The simulated Worker is a test driver, not the real one.
"""
import contextlib
import io
import json
import os
import pathlib
import shutil
import tempfile
import unittest

from _common import LINUX_ONLY, ROOT, doc_ip

import renderctl
from fake_worker import TOOLCHAIN, FakeWorker
from render_gates import textrules
from render_gates.config import Layout
from render_gates.ingest import Ingest
from render_gates.minischema import MiniSchema
from render_gates.publish import Publisher
from test_sim_lib import registry_from_fixtures

SCHEMA = MiniSchema.from_dir(ROOT / "schemas")
URN = {"job_view": "urn:render-protocol:status:v1#/$defs/job_view",
       "handoff_record": "urn:render-protocol:result:v1#/$defs/handoff_record",
       "worker_view": "urn:render-protocol:status:v1#/$defs/worker_view",
       "heartbeat_ack": "urn:render-protocol:status:v1#/$defs/heartbeat_ack",
       "ingest_record": "urn:render-protocol:result:v1#/$defs/ingest_record",
       "submission_receipt": "urn:render-protocol:job:v1#/$defs/submission_receipt",
       "cancel_receipt": "urn:render-protocol:job:v1#/$defs/cancel_receipt",
       "cancel_request": "urn:render-protocol:job:v1#/$defs/cancel_request",
       "job": "urn:render-protocol:job:v1"}

SCENE = {"scene/index.html": b"<!doctype html><canvas></canvas><script src='main.js'></script>",
         "scene/main.js": b"export function renderFrame(i, t) { return t; }"}


class Clock:
    def __init__(self):
        self.wall, self.mono = 1791450000.0, 1000.0

    def advance(self, s):
        self.wall += s
        self.mono += s


class Sim:
    def __init__(self, **scenario):
        self.root = pathlib.Path(tempfile.mkdtemp())
        self.lay = Layout.simulated(self.root / "tree")
        self.clock = Clock()
        self.reg = registry_from_fixtures(self.root)
        self.toolchains = {"tc-sim-a": {"browser": TOOLCHAIN["browser"], "encoder": TOOLCHAIN["encoder"]}}
        self.pub = Publisher(self.lay, SCHEMA, clock=lambda: self.clock.wall)
        self.ing = self.new_ingest()
        self.worker = FakeWorker(self.lay, **scenario)

    def new_ingest(self):
        return Ingest(self.lay, SCHEMA, self.reg, self.toolchains, clock=lambda: self.clock.wall, mono=lambda: self.clock.mono)

    def close(self):
        shutil.rmtree(self.root)

    def ctl(self, *argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            renderctl.main(["--root", str(self.lay.outbox.parent.parent), *argv])
        out = buf.getvalue()
        self.outputs.append(out)
        return json.loads(out) if out.strip().startswith("{") else out

    outputs = []

    def submission(self, key="demo-keyframes-r1", stage="keyframes", **over):
        sub = {"protocol_version": 1, "kind": "job", "idempotency_key": key, "project_id": "shader-demo",
               "stage": stage, "adapter": {"id": "webgl-frames", "version": "0.1.0"}, "entrypoint": "scene/index.html",
               "render": {"width": 1280, "height": 720, "fps": 30, "frame_start": 0, "frame_end_exclusive": 150,
                          "keyframes": [0, 75, 149], "seed": 42},
               "export": {"profiles": ["png-rgb8-v1"]}, "artifacts": ["log", "keyframe", "contact"],
               "limits": {"timeout_seconds": 600, "max_attempts": 2}, "network_policy": "offline"}
        if stage == "preview":
            sub["render"] = {"width": 640, "height": 360, "fps": 30, "frame_start": 0, "frame_end_exclusive": 150, "seed": 1}
            sub["export"]["profiles"] = ["h264-preview-v1", "png-rgb8-v1"]
            sub["artifacts"] = ["log", "contact", "preview"]
        for k, v in over.items():
            sub[k] = v
        return sub

    def submit(self, sub, files=SCENE):
        src = self.root / "src" / sub["idempotency_key"]
        shutil.rmtree(src, ignore_errors=True)
        for rel, data in files.items():
            (src / rel).parent.mkdir(parents=True, exist_ok=True)
            (src / rel).write_bytes(data)
        p = self.root / f"{sub['idempotency_key']}.json"
        p.write_text(json.dumps(sub), encoding="utf-8")
        self.ctl("submit", str(p), "--bundle", str(src))
        self.pub.run_once()
        return self.ctl("receipt", sub["idempotency_key"])

    def cycle(self, n=1):
        """Publish, ingest light, worker step, ingest light+heavy, worker acks."""
        for _ in range(n):
            self.pub.run_once()
            self.ing.run_light()
            self.worker.poll()
            self.worker.heartbeat()
            self.ing.run_light()
            self.ing.run_heavy()
            self.worker.process_acks()
            self.ing.run_light()
            self.clock.advance(5)

    def view(self, job_id):
        v = json.loads((self.lay.inbox / "jobs" / job_id / "status.json").read_text(encoding="utf-8"))
        assert SCHEMA.is_valid(v, URN["job_view"])
        return v

    def all_json_valid(self, testcase):
        checks = [("inbox/jobs/*/status.json", "job_view"), ("inbox/jobs/*/*/ingest.json", "ingest_record"),
                  ("render/down/acks/j_*/*.json", "ingest_record"), ("render/down/acks/_worker/heartbeat.json", "heartbeat_ack"),
                  ("inbox/workers/worker-local.json", "worker_view"), ("render/down/control/*/cancel.json", "cancel_request"),
                  ("render/down/jobs/*/job.json", "job"), ("inbox/handoff/*.record.json", "handoff_record"),
                  ("render/down/acks/_handoff/*.json", "handoff_record")]
        tree = self.lay.outbox.parent.parent
        for pattern, d in checks:
            for p in tree.glob(pattern.replace("inbox/", "data/inbox/")):
                testcase.assertTrue(SCHEMA.is_valid(json.loads(p.read_text(encoding="utf-8")), URN[d]), f"{d} {p.name}")
        for p in (self.lay.inbox / "submissions").glob("*.json"):
            doc = json.loads(p.read_text(encoding="utf-8"))
            testcase.assertTrue(SCHEMA.is_valid(doc, URN[doc["kind"]]), p.name)

    def leaked_anywhere(self, needle):
        """Search everything Claude can read (inbox) plus acks and renderctl outputs."""
        hits = []
        for base in (self.lay.inbox, self.lay.down / "acks"):
            for p in base.rglob("*"):
                if p.is_file() and needle.encode() in p.read_bytes():
                    hits.append(p.name)
        hits += [o for o in self.outputs if needle in o]
        return hits


@LINUX_ONLY
class E2E(unittest.TestCase):
    def setUp(self):
        Sim.outputs = []

    def sim(self, **kw):
        s = Sim(**kw)
        self.addCleanup(s.close)
        return s

    # ------------------------------------------------------------- happy paths
    def test_SE2E01_keyframes_happy_path(self):
        s = self.sim()
        r = s.submit(s.submission())
        self.assertEqual(r["verdict"], "published")
        job = r["job_id"]
        s.ing.run_light()
        self.assertEqual(s.view(job)["display_state"], "queued")
        s.cycle(2)
        v = s.view(job)
        self.assertEqual((v["display_state"], v["overlays"]), ("succeeded", []))
        arts = s.ctl("artifacts", job)
        self.assertEqual(sorted(a["artifact_id"] for a in arts["artifacts"]),
                         ["contact", "keyframe-0", "keyframe-149", "keyframe-75", "log"])
        kf = s.ctl("fetch", job, "--kind", "keyframe")["paths"]
        self.assertEqual(len(kf), 3)
        self.assertTrue(all(pathlib.Path(p).read_bytes().startswith(b"\x89PNG") for p in kf))
        self.assertIn("rendered 3 frames", s.ctl("logs", job, "--tail", "5"))
        self.assertFalse(list((s.lay.up / "jobs").rglob("result.json")))      # Worker cleaned up after ack
        self.assertFalse((s.lay.outbox / "submissions" / "demo-keyframes-r1.json").exists())  # renderctl cleaned outbox
        s.all_json_valid(self)

    def test_SE2E02_preview_mp4(self):
        s = self.sim()
        job = s.submit(s.submission("demo-preview-r1", "preview"))["job_id"]
        s.cycle(2)
        self.assertEqual(s.view(job)["display_state"], "succeeded")
        self.assertEqual(len(s.ctl("fetch", job, "--kind", "preview")["paths"]), 1)
        self.assertTrue(s.ing.metrics)                                       # MX-05 style measurements recorded
        s.all_json_valid(self)

    # ------------------------------------------------------------- publish (PR-01/02/14, GT-07/08)
    def test_SE2E10_idempotency(self):
        s = self.sim()
        sub = s.submission()
        first = s.submit(sub)
        again = s.submit(sub)
        self.assertEqual((again["verdict"], again["job_id"]), ("duplicate", first["job_id"]))
        other_bundle = s.submit(sub, files=dict(SCENE, **{"scene/main.js": b"// changed"}))
        self.assertEqual((other_bundle["verdict"], other_bundle["job_id"]), ("conflict", None))
        other_params = s.submit(dict(sub, params={"palette": "noon"}))
        self.assertEqual(other_params["verdict"], "conflict")
        self.assertEqual(len(list((s.lay.down / "jobs").iterdir())), 1)
        s.all_json_valid(self)

    def test_SE2E11_publish_rejections(self):
        s = self.sim()
        rej = lambda r: (r["verdict"], sorted(x["rule"] for x in r["reasons"]))  # noqa: E731
        self.assertEqual(rej(s.submit(s.submission("k-secret"), files=dict(SCENE, **{"scene/key.txt": b"sk-ant-" + b"x" * 30}))),
                         ("rejected", ["TX-SECRET"]))
        self.assertEqual(rej(s.submit(s.submission("k-claude"), files=dict(SCENE, **{"scene/.claude/settings.json": b"{}"}))),
                         ("rejected", ["TX-SECRET"]))
        self.assertEqual(rej(s.submit(s.submission("k-noentry", entrypoint="scene/missing.html"))), ("rejected", ["XF-J12"]))
        long_preview = s.submission("k-long", "preview")
        long_preview["render"]["frame_end_exclusive"] = 30 * 21
        with self.assertRaises(SystemExit):                       # renderctl precheck refuses locally ...
            s.submit(long_preview)
        (s.lay.outbox / "bundles" / "k-long.tar").write_bytes(_tar(SCENE))   # ... publish enforces anyway
        (s.lay.outbox / "submissions" / "k-long.json").write_text(json.dumps(long_preview), encoding="utf-8")
        s.pub.run_once()
        self.assertEqual(rej(s.ctl("receipt", "k-long")), ("rejected", ["XF-J08"]))
        # bundle with a Windows device name: write the tar directly (renderctl's own precheck refuses it)
        sub = s.submission("k-device")
        (s.lay.outbox / "bundles" / "k-device.tar").write_bytes(_tar({"scene/index.html": b"x", "scene/CON.js": b""}))
        (s.lay.outbox / "submissions" / "k-device.json").write_text(json.dumps(sub), encoding="utf-8")
        s.pub.run_once()
        self.assertEqual(rej(s.ctl("receipt", "k-device")), ("rejected", ["XF-B04"]))
        # float, symlinked bundle (GT-08)
        (s.lay.outbox / "submissions" / "k-float.json").write_text(
            json.dumps(s.submission("k-float")).replace('"seed": 42', '"seed": 4.5'), encoding="utf-8")
        os.symlink("/etc/hostname", s.lay.outbox / "bundles" / "k-link.tar")
        (s.lay.outbox / "submissions" / "k-link.json").write_text(json.dumps(s.submission("k-link")), encoding="utf-8")
        s.pub.run_once()
        self.assertEqual(rej(s.ctl("receipt", "k-float")), ("rejected", ["malformed_json"]))
        self.assertEqual(rej(s.ctl("receipt", "k-link")), ("rejected", ["not_regular_file"]))
        self.assertFalse(list((s.lay.down / "jobs").iterdir()))
        s.all_json_valid(self)

    # ------------------------------------------------------------- delivery verdicts
    def test_SE2E20_privacy_quarantine(self):          # PR-10, TX-02
        s = self.sim(leak_log=True)
        job = s.submit(s.submission())["job_id"]
        s.cycle(3)
        v = s.view(job)
        self.assertEqual(v["display_state"], "quarantined")
        rec = json.loads(next((s.lay.inbox / "jobs" / job).glob("a_*/ingest.json")).read_text(encoding="utf-8"))
        self.assertEqual([(r["rule"], r["ref"]) for r in rec["reasons"]], [("TX-IPV4", "log")])
        self.assertEqual([p.name for p in next((s.lay.inbox / "jobs" / job).glob("a_*")).iterdir()], ["ingest.json"])
        self.assertEqual(s.leaked_anywhere(".".join(["203", "0", "113", "9"])), [])
        s.all_json_valid(self)

    def test_SE2E21_rejected_then_accepted(self):      # PR-08
        s = self.sim(corrupt_times=1)
        job = s.submit(s.submission())["job_id"]
        s.cycle(4)
        v = s.view(job)
        self.assertEqual((v["display_state"], v["current_attempt"]["rejected_count"]), ("succeeded", 1))

    def test_SE2E22_retry_exhaustion(self):            # PR-11
        s = self.sim(corrupt_times=99)
        job = s.submit(s.submission())["job_id"]
        s.cycle(8)
        v = s.view(job)
        self.assertEqual((v["display_state"], v["current_attempt"]["rejected_count"]), ("delivery_failed", 3))
        s.all_json_valid(self)

    def test_SE2E23_contradiction(self):               # PR-18
        s = self.sim(contradict=True)
        job = s.submit(s.submission())["job_id"]
        s.cycle(2)
        self.assertEqual(s.view(job)["display_state"], "delivery_failed")
        rec = json.loads(next((s.lay.inbox / "jobs" / job).glob("a_*/ingest.json")).read_text(encoding="utf-8"))
        self.assertIn("outcome_mismatch", [r["rule"] for r in rec["reasons"]])
        s.all_json_valid(self)

    def test_SE2E24_retry_flow(self):
        s = self.sim(fail_first=True)
        job = s.submit(s.submission())["job_id"]
        s.cycle(1)
        self.assertEqual(s.view(job)["display_state"], "retry_pending")
        s.cycle(2)
        v = s.view(job)
        self.assertEqual((v["display_state"], v["attempts_seen"]), ("succeeded", 2))

    def test_SE2E25_structure_mismatch_and_toolchain(self):   # MX-03 e2e, MX-04(a)
        from media_fixtures import box, fullbox
        s = self.sim(udta=box(b"udta", fullbox(b"meta", box(b"ilst", box(b"\xa9too", b"x")))))
        job = s.submit(s.submission("p1-udta", "preview"))["job_id"]
        s.cycle(2)
        self.assertEqual(s.view(job)["display_state"], "delivery_failed")
        s2 = self.sim()
        s2.toolchains.clear()
        s2.ing = s2.new_ingest()
        job2 = s2.submit(s2.submission())["job_id"]
        s2.cycle(2)
        rec = json.loads(next((s2.lay.inbox / "jobs" / job2).glob("a_*/ingest.json")).read_text(encoding="utf-8"))
        self.assertEqual([r["rule"] for r in rec["reasons"]], ["toolchain_unregistered"])

    # ------------------------------------------------------------- cancel (PR-13)
    def test_SE2E30_cancel_paths(self):
        s = self.sim()
        job = s.submit(s.submission())["job_id"]
        s.ctl("cancel", job)
        s.pub.run_once()
        self.assertEqual(s.ctl("receipt", f"{job}.cancel")["verdict"], "published")
        s.cycle(2)
        v = s.view(job)
        self.assertEqual((v["display_state"], v["cancel"]), ("cancelled", "honored"))
        os.utime(s.lay.outbox / "control" / f"{job}.cancel.json", ns=(1, 1))      # resubmit -> already requested
        s.pub.run_once()
        self.assertEqual(s.ctl("receipt", f"{job}.cancel")["verdict"], "already_requested")
        done = s.submit(s.submission("demo-2"))["job_id"]
        s.cycle(2)
        s.ctl("cancel", done)
        s.pub.run_once()
        self.assertEqual(s.ctl("receipt", f"{done}.cancel")["verdict"], "ignored_terminal")
        self.assertFalse((s.lay.down / "control" / done).exists())
        s.ctl("cancel", "j_" + "0" * 26)
        s.pub.run_once()
        self.assertEqual(s.ctl("receipt", "j_" + "0" * 26 + ".cancel")["verdict"], "unknown_job")
        s.all_json_valid(self)

    # ------------------------------------------------------------- heartbeat (HB sim)
    def test_SE2E40_heartbeat_and_restart(self):
        s = self.sim()
        job = s.submit(s.submission())["job_id"]
        s.ing.run_light()
        s.worker.status(job, "a_" + "1" * 26, 1, 1, "leased")
        s.worker.status(job, "a_" + "1" * 26, 1, 2, "running", 1, 3)
        s.worker.heartbeat("busy", job, "a_" + "1" * 26)
        s.ing.run_light()
        self.assertEqual(json.loads((s.lay.inbox / "workers" / "worker-local.json").read_text(encoding="utf-8"))["health"], "alive")
        s.clock.advance(60)
        s.ing.run_light()                                       # same heartbeat file again: no refresh (HB-01)
        s.clock.advance(31)
        s.ing.run_light()
        v = s.view(job)
        self.assertEqual((v["display_state"], v["overlays"]), ("running", ["worker_lost"]))
        self.assertEqual(v["attempts_seen"], 1)                 # loss creates no attempt (HB-11 sim)
        s.ing = s.new_ingest()                                  # ingest restart (HB-05)
        s.ing.run_light()
        self.assertEqual(s.view(job)["overlays"], ["unconfirmed"])
        s.worker.epoch, s.worker.hb_seq = 2, 0                  # worker restart, new epoch (HB-06)
        s.worker.heartbeat("busy", job, "a_" + "1" * 26)
        s.ing.run_light()
        self.assertEqual(s.view(job)["overlays"], [])
        s.all_json_valid(self)

    def test_SE2E41_part_files_and_missing_result_ignored(self):   # FS-12
        s = self.sim()
        job = s.submit(s.submission())["job_id"]
        s.ing.run_light()
        d = s.lay.up / "jobs" / job / ("a_" + "2" * 26)
        (d / "artifacts").mkdir(parents=True)
        (d / ".part-result.json").write_text("{}", encoding="utf-8")
        (d / "artifacts" / "log.txt").write_text("x", encoding="utf-8")
        s.ing.run_heavy()
        self.assertFalse((s.lay.down / "acks" / job).exists())

    # ------------------------------------------------------------- handoff (TX-04)
    def test_SE2E50_handoff_reports(self):
        s = self.sim()
        (s.lay.up / "handoff" / "local-20261008-01.md").write_text("# report\nall good; DERP vs direct discussed\n", encoding="utf-8")
        (s.lay.up / "handoff" / "local-20261008-02.md").write_text("peer at " + doc_ip() + "\n", encoding="utf-8")
        s.ing.run_heavy()
        self.assertTrue((s.lay.inbox / "handoff" / "local-20261008-01.md").exists())
        self.assertFalse((s.lay.inbox / "handoff" / "local-20261008-02.md").exists())
        rec = json.loads((s.lay.inbox / "handoff" / "local-20261008-02.record.json").read_text(encoding="utf-8"))
        self.assertEqual((rec["verdict"], [r["rule"] for r in rec["reasons"]]), ("quarantined", ["TX-IPV4"]))
        self.assertEqual(s.leaked_anywhere(doc_ip()), [])
        s.all_json_valid(self)                                  # D2: records have a schema definition

    def test_SE2E51_unattributable_delivery(self):          # D3
        s = self.sim()
        job = s.submit(s.submission())["job_id"]
        s.ing.run_light()
        d = s.lay.up / "jobs" / job / ("a_" + "3" * 26)
        d.mkdir(parents=True)
        (d / "result.json").write_text("{not json", encoding="utf-8")
        s.ing.run_heavy()
        ack = json.loads((s.lay.down / "acks" / job / ("a_" + "3" * 26 + ".json")).read_text(encoding="utf-8"))
        self.assertEqual((ack["verdict"], ack["attempt_no"]), ("conflict", None))
        self.assertEqual(s.view(job)["display_state"], "queued")       # no state change
        s.all_json_valid(self)

    def test_SE2E52_unparseable_result_of_known_attempt(self):
        s = self.sim()
        job = s.submit(s.submission())["job_id"]
        s.ing.run_light()
        att = "a_" + "4" * 26
        s.worker.status(job, att, 1, 1, "leased")
        s.ing.run_light()
        (s.lay.up / "jobs" / job / att / "result.json").write_text("{not json", encoding="utf-8")
        s.ing.run_heavy()
        ack = json.loads((s.lay.down / "acks" / job / f"{att}.json").read_text(encoding="utf-8"))
        self.assertEqual((ack["verdict"], ack["attempt_no"], [r["rule"] for r in ack["reasons"]]),
                         ("delivery_failed", 1, ["malformed_json"]))
        s.all_json_valid(self)

    # ------------------------------------------------------------- M2: cache, metrics, concurrency
    def test_SE2E60_verdict_cache_keeps_integrity_checks(self):
        s = self.sim()
        j1 = s.submit(s.submission("cache-a"))["job_id"]
        s.cycle(2)
        j2 = s.submit(s.submission("cache-b", params={"palette": "noon"}))["job_id"]
        s.cycle(2)
        recs = {j: json.loads(next((s.lay.inbox / "jobs" / j).glob("a_*/ingest.json")).read_text(encoding="utf-8")) for j in (j1, j2)}
        self.assertEqual((recs[j1]["cache_hit"], recs[j2]["cache_hit"]), (False, True))   # identical artifacts reused
        s.worker.sc["corrupt_times"], s.worker.corrupted = 1, 0
        j3 = s.submit(s.submission("cache-c", params={"palette": "dusk"}))["job_id"]
        s.cycle(1)
        rec = json.loads(next((s.lay.down / "acks" / j3).glob("a_*.json")).read_text(encoding="utf-8"))
        self.assertEqual((rec["verdict"], rec["reasons"][0]["rule"]), ("rejected", "hash_mismatch"))   # cache never skips hashing
        s.cycle(2)
        self.assertEqual(s.view(j3)["display_state"], "succeeded")
        s.all_json_valid(self)

    def test_SE2E61_metrics_are_numeric_and_private(self):
        s = self.sim()
        s.submit(s.submission("metrics-a", "preview"))
        s.cycle(2)
        lines = [json.loads(line) for f in (s.lay.ingest_private / "metrics").glob("*.jsonl")
                 for line in f.read_text(encoding="utf-8").splitlines()]
        kinds = {x["kind"] for x in lines}
        self.assertTrue({"artifact", "delivery", "light_loop"} <= kinds)
        allowed = {"kind", "t", "ms", "bytes_read", "structures", "profile", "verdict", "max_rss_kb"}
        for x in lines:
            self.assertTrue(set(x) <= allowed, set(x) - allowed)
        self.assertEqual(textrules.scan_text(json.dumps(lines)), set())
        self.assertFalse(any((s.lay.inbox).rglob("*.jsonl")))         # metrics never reach the inbox

    def test_SE2E62_light_and_heavy_as_separate_processes(self):
        import threading
        s = self.sim()
        jobs = [s.submit(s.submission(f"conc-{i}"))["job_id"] for i in range(4)]
        light, heavy = s.new_ingest(), s.new_ingest()                    # two independent instances, like two units
        stop, errors = threading.Event(), []

        def loop(fn):
            while not stop.is_set():
                try:
                    fn()
                except Exception as e:  # noqa: BLE001
                    errors.append(type(e).__name__)
        threads = [threading.Thread(target=loop, args=(light.run_light,)), threading.Thread(target=loop, args=(heavy.run_heavy,))]
        for t in threads:
            t.start()
        try:
            for _ in range(6):
                s.pub.run_once()
                s.worker.poll()
                s.worker.heartbeat()
                s.worker.process_acks()
                import time
                time.sleep(0.05)
        finally:
            stop.set()
            for t in threads:
                t.join()
        light.run_light()
        self.assertEqual(errors, [])
        self.assertEqual([s.view(j)["display_state"] for j in jobs], ["succeeded"] * 4)
        s.all_json_valid(self)

    # ------------------------------------------------------------- global privacy sweep
    def test_SE2E90_outputs_have_no_sensitive_shapes(self):
        s = self.sim()
        job = s.submit(s.submission())["job_id"]
        s.cycle(2)
        for cmd in (["status", job], ["artifacts", job], ["workers"], ["fetch", job, "--kind", "contact"]):
            out = s.ctl(*cmd)
            text = json.dumps(out) if isinstance(out, dict) else out
            hits = textrules.scan_text(text.replace(str(s.root), "<tmp>"))
            self.assertEqual(hits, set(), cmd)


def _tar(files):
    import tarfile
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.USTAR_FORMAT) as tf:
        for name, data in files.items():
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


if __name__ == "__main__":
    unittest.main()
