"""Regenerate examples/protocol/*.json and vectors/identity-vectors.json.

File naming: <schema $defs name>[.<variant>].json  (tests map the prefix to a schema).
All IDs are fixed so the output is reproducible: python3 -I tools/make_examples.py
"""
import copy
import hashlib
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "refimpl"))
from render_protocol.bundle import check_paths  # noqa: E402
from render_protocol.canonical import canonical_bytes, job_identity_sha256, normalize_submission  # noqa: E402

OUT = ROOT / "examples" / "protocol"
J1, J2 = "j_01JZ8Y3K5M7Q9R1T3V5X7Z9B2D", "j_01JZ8Y3K5M7Q9R1T3V5X7Z9B3E"
A = ["a_TB8MS32BFS62Y8AE92CCY53VHT", "a_TX9PNCNJ2ASVEHEKAS0EWVRSAP", "a_8Q3JJQEX7XRY7BG24JHVCHTPD4",
     "a_64QS501C8S2E2HKMCD5ZJ7DN0A", "a_HPXYP29FWZ6XJ7WMK7FG2B11JK", "a_8BEC8GCSGSVYW5QDNF7FCHF1BZ"]
REQ = "req_M8410AYQFMN9Z5FGYD1EQCXPJ3"


def h(s):
    return hashlib.sha256(s.encode()).hexdigest()


BUNDLE1, BUNDLE2 = h("example-bundle-1"), h("example-bundle-2")
CHROME, FFMPEG = h("example-browser-exe"), h("example-ffmpeg-exe")


def write(name, doc):
    (OUT / f"{name}.json").write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")


def job_from(sub, job_id, bundle_sha, created):
    j = normalize_submission(sub)
    j.update(job_id=job_id, created_at=created,
             bundle={"format": "tar", "sha256": bundle_sha, "size_bytes": 20480, "entry_count": 5},
             identity_sha256=job_identity_sha256(sub, bundle_sha), publish_rules_version=1)
    return j


def toolchain(browser=True, encoder=False, tc="tc-2026-10-a"):
    return {"worker_version": "0.1.0", "toolchain_id": tc,
            "browser": {"name": "chromium", "major": 140, "exe_sha256": CHROME} if browser else None,
            "encoder": {"name": "ffmpeg", "version": "7.1", "exe_sha256": FFMPEG} if encoder else None}


def device(query="available", busy=True, vram=612, vendor="nvidia", model="GeForce RTX 4070", fallback=False):
    return {"gpu_query": query, "gpu_vendor": vendor, "gpu_model": model,
            "software_fallback": fallback, "gpu_busy_observed": busy, "vram_peak_mb": vram}


NO_DEVICE = {"gpu_query": "not_attempted", "gpu_vendor": None, "gpu_model": None,
             "software_fallback": None, "gpu_busy_observed": None, "vram_peak_mb": None}


def log(size=5120):
    return {"artifact_id": "log", "kind": "log", "media_type": "text/plain", "delivery": "uploaded",
            "size_bytes": size, "sha256": h(f"log-{size}")}


def png(kind, idx=None, size=812345):
    a = {"artifact_id": f"keyframe-{idx}" if kind == "keyframe" else "contact", "kind": kind,
         "media_type": "image/png", "export_profile": "png-rgb8-v1", "delivery": "uploaded",
         "size_bytes": size, "sha256": h(f"{kind}-{idx}")}
    if kind == "keyframe":
        a["frame_index"] = idx
    return a


def result(job_id, attempt, outcome, **kw):
    r = {"protocol_version": 1, "kind": "result", "job_id": job_id, "attempt_id": attempt, "attempt_no": 1,
         "worker_epoch": 3, "outcome": outcome, "phase_reached": "upload",
         "exit": {"kind": "exited", "code": 0, "signal": None}, "error_class": None,
         "bundle_sha256": BUNDLE1, "adapter": {"id": "webgl-frames", "version": "0.1.0"},
         "toolchain": toolchain(), "device": device(), "frames": {"expected": 3, "rendered": 3},
         "timings_ms": {"prepare": 900, "render": 2600, "total": 3800}, "artifacts": [],
         "local_text_check": {"rules_version": 1, "passed": True}}
    r.update(kw)
    return r


def ingest(job_id, attempt, verdict, reasons, rejected=0, attempt_no=1):
    return {"protocol_version": 1, "kind": "ingest_record", "job_id": job_id, "attempt_id": attempt,
            "attempt_no": attempt_no, "received_at": "2026-10-08T09:02:11Z", "verdict": verdict,
            "retry_allowed": verdict == "rejected", "rejected_count": rejected,
            "reasons": [{"rule": r, "ref": ref} for r, ref in reasons],
            "rules_version": 1, "profiles_version": 1, "ingest_version": "0.1.0", "cache_hit": False}


def main():
    for p in OUT.glob("*.json"):
        p.unlink()
    (OUT / "vectors").mkdir(parents=True, exist_ok=True)

    # ---- job schema ------------------------------------------------------
    sub = {"protocol_version": 1, "kind": "job", "idempotency_key": "shader-demo-keyframes-r1",
           "project_id": "shader-demo", "stage": "keyframes",
           "adapter": {"id": "webgl-frames", "version": "0.1.0"}, "entrypoint": "scene/index.html",
           "render": {"width": 1280, "height": 720, "fps": 30, "frame_start": 0, "frame_end_exclusive": 150,
                      "keyframes": [149, 0, 75], "seed": 42},
           "params": {"palette": "dusk", "speed": "1.25", "layers": 3},
           "export": {"profiles": ["png-rgb8-v1"]}, "artifacts": ["log", "keyframe", "contact"],
           "limits": {"timeout_seconds": 600, "max_attempts": 2}, "network_policy": "offline"}
    write("submission", sub)
    job1 = job_from(sub, J1, BUNDLE1, "2026-10-08T09:00:00Z")
    write("job", job1)

    sub2 = {"protocol_version": 1, "kind": "job", "idempotency_key": "shader-demo-preview-r1",
            "project_id": "shader-demo", "parent_job_id": J1, "stage": "preview",
            "adapter": {"id": "webgl-frames", "version": "0.1.0"}, "entrypoint": "scene/index.html",
            "render": {"width": 640, "height": 360, "fps": 30, "frame_start": 0, "frame_end_exclusive": 150, "seed": 42},
            "export": {"profiles": ["h264-preview-v1", "png-rgb8-v1"]}, "artifacts": ["log", "contact", "preview"],
            "limits": {"timeout_seconds": 900, "max_attempts": 2}, "network_policy": "offline"}
    job2 = job_from(sub2, J2, BUNDLE2, "2026-10-08T09:10:00Z")
    write("job.preview", job2)

    write("cancel_submission", {"protocol_version": 1, "kind": "cancel_submission", "job_id": J2})
    write("cancel_request", {"protocol_version": 1, "kind": "cancel_request", "job_id": J2,
                             "request_id": REQ, "requested_at": "2026-10-08T09:12:30Z"})
    write("submission_receipt.published", {"protocol_version": 1, "kind": "submission_receipt",
          "idempotency_key": sub["idempotency_key"], "verdict": "published", "job_id": J1,
          "identity_sha256": job1["identity_sha256"], "reasons": [], "issued_at": "2026-10-08T09:00:00Z"})
    write("submission_receipt.conflict", {"protocol_version": 1, "kind": "submission_receipt",
          "idempotency_key": sub["idempotency_key"], "verdict": "conflict", "job_id": None,
          "identity_sha256": job_identity_sha256(sub, BUNDLE2),
          "reasons": [{"rule": "identity_conflict", "ref": "submission.json"}], "issued_at": "2026-10-08T09:05:00Z"})
    write("submission_receipt.rejected", {"protocol_version": 1, "kind": "submission_receipt",
          "idempotency_key": "shader-demo-bad-r1", "verdict": "rejected", "job_id": None, "identity_sha256": None,
          "reasons": [{"rule": "XF-B04", "ref": "bundle"}, {"rule": "XF-J08", "ref": "submission.json#/render"}],
          "issued_at": "2026-10-08T09:06:00Z"})
    write("cancel_receipt.published", {"protocol_version": 1, "kind": "cancel_receipt", "job_id": J2,
          "verdict": "published", "request_id": REQ, "issued_at": "2026-10-08T09:12:30Z"})
    write("cancel_receipt.ignored_terminal", {"protocol_version": 1, "kind": "cancel_receipt", "job_id": J1,
          "verdict": "ignored_terminal", "request_id": None, "issued_at": "2026-10-08T09:20:00Z"})

    # ---- status schema ---------------------------------------------------
    caps = {"adapters": [{"id": "webgl-frames", "version": "0.1.0"}],
            "export_profiles": ["png-rgb8-v1", "h264-preview-v1"], "max_concurrent": 1}
    write("heartbeat", {"protocol_version": 1, "kind": "heartbeat", "worker_alias": "worker-local",
          "worker_epoch": 3, "seq": 118, "state": "busy", "active_job_id": J2, "active_attempt_id": A[1],
          "worker_version": "0.1.0", "capabilities": caps, "disk_free_class": "ok"})
    write("heartbeat.idle", {"protocol_version": 1, "kind": "heartbeat", "worker_alias": "worker-local",
          "worker_epoch": 4, "seq": 1, "state": "idle", "active_job_id": None, "active_attempt_id": None,
          "worker_version": "0.1.0", "capabilities": caps})
    write("status", {"protocol_version": 1, "kind": "status", "job_id": J2, "attempt_id": A[1], "attempt_no": 2,
          "worker_epoch": 3, "seq": 7, "state": "running", "progress": {"frames_done": 62, "frames_total": 150},
          "elapsed_ms": 4120, "error_class": None})
    write("heartbeat_ack", {"protocol_version": 1, "kind": "heartbeat_ack", "worker_alias": "worker-local",
          "accepted_epoch": 3, "accepted_seq": 118, "issued_at": "2026-10-08T09:12:05Z"})
    write("worker_view", {"protocol_version": 1, "kind": "worker_view", "worker_alias": "worker-local",
          "health": "alive", "last_epoch": 3, "last_seq": 118, "last_received_at": "2026-10-08T09:12:05Z",
          "state": "busy", "active_job_id": J2, "capabilities": caps, "updated_at": "2026-10-08T09:12:05Z"})
    write("job_view.running", {"protocol_version": 1, "kind": "job_view", "job_id": J2, "stage": "preview",
          "display_state": "running", "overlays": ["cancel_requested"], "cancel": "requested",
          "current_attempt": {"attempt_id": A[1], "attempt_no": 2, "exec_state": "running", "last_status_seq": 7,
                              "last_received_at": "2026-10-08T09:12:05Z",
                              "progress": {"frames_done": 62, "frames_total": 150}, "verdict": None, "rejected_count": 0},
          "attempts_seen": 2, "updated_at": "2026-10-08T09:12:31Z"})
    write("job_view.quarantined", {"protocol_version": 1, "kind": "job_view", "job_id": J1, "stage": "keyframes",
          "display_state": "quarantined", "overlays": [], "cancel": "none",
          "current_attempt": {"attempt_id": A[0], "attempt_no": 1, "exec_state": "succeeded", "last_status_seq": 9,
                              "last_received_at": "2026-10-08T09:02:00Z", "progress": None,
                              "verdict": "quarantined", "rejected_count": 0},
          "attempts_seen": 1, "updated_at": "2026-10-08T09:02:11Z"})
    write("netcheck_view", {"protocol_version": 1, "kind": "netcheck_view", "worker_alias": "worker-local",
          "online": True, "known": True, "reachable": True, "path": "direct", "error_code": None,
          "checked_at": "2026-10-08T09:12:00Z"})

    # ---- result schema ---------------------------------------------------
    kf = [png("keyframe", i) for i in (0, 75, 149)]
    write("result", result(J1, A[0], "succeeded", artifacts=[log()] + kf + [png("contact", size=402112)]))
    write("result.gpu_query_unavailable", result(J1, A[2], "succeeded",
          device=device(query="unavailable", busy=None, vram=None),
          artifacts=[log()] + kf + [png("contact", size=402112)]))
    write("result.preview_succeeded", result(J2, A[1], "succeeded", attempt_no=2, bundle_sha256=BUNDLE2,
          toolchain=toolchain(encoder=True), frames={"expected": 150, "rendered": 150},
          timings_ms={"prepare": 800, "render": 21000, "encode": 3100, "total": 25400},
          artifacts=[log(), png("contact", size=402112),
                     {"artifact_id": "preview", "kind": "preview", "media_type": "video/mp4",
                      "export_profile": "h264-preview-v1", "delivery": "uploaded",
                      "size_bytes": 1843200, "sha256": h("preview")}]))
    write("result.failed_retry_scheduled", result(J2, A[3], "failed", phase_reached="render",
          exit={"kind": "exited", "code": 3221225477, "signal": None}, error_class="adapter_error",
          retry_scheduled=True, bundle_sha256=BUNDLE2, frames={"expected": 150, "rendered": 40},
          timings_ms={"prepare": 800, "render": 7000, "total": 7900}, artifacts=[log(2048)]))
    write("result.cancelled_before_start", result(J1, A[4], "cancelled", phase_reached="validate",
          exit={"kind": "not_started", "code": None, "signal": None}, error_class="cancelled",
          toolchain=toolchain(browser=False), device=NO_DEVICE, frames={"expected": 3, "rendered": 0},
          timings_ms={"total": 15}, artifacts=[log(300)]))
    write("result.browser_missing", result(J1, A[5], "failed", phase_reached="prepare",
          exit={"kind": "not_started", "code": None, "signal": None}, error_class="adapter_error",
          toolchain=toolchain(browser=False, tc=None), device=NO_DEVICE, frames={"expected": 3, "rendered": 0},
          timings_ms={"prepare": 40, "total": 45}, artifacts=[log(400)]))
    write("result.download_failed", result(J1, A[4], "failed", phase_reached="validate",
          exit={"kind": "not_started", "code": None, "signal": None}, error_class="validation_error",
          toolchain=toolchain(browser=False), device=NO_DEVICE, frames={"expected": 3, "rendered": 0},
          timings_ms={"total": 30}, artifacts=[log(350)]))
    write("result.job_unreadable", result(J1, A[5], "failed", phase_reached="validate",
          exit={"kind": "not_started", "code": None, "signal": None}, error_class="validation_error",
          bundle_sha256=None, adapter=None, toolchain=toolchain(browser=False), device=NO_DEVICE,
          frames={"expected": None, "rendered": 0}, timings_ms={"total": 5}, artifacts=[log(200)]))

    write("ingest_record", ingest(J1, A[0], "accepted", []))
    write("ingest_record.quarantined", ingest(J1, A[0], "quarantined",
          [("TX-WINPATH", "log"), ("TX-IPV4", "result.json#/device/gpu_model")]))
    write("ingest_record.rejected", ingest(J2, A[1], "rejected", [("hash_mismatch", "preview")], rejected=1, attempt_no=2))
    write("ingest_record.outcome_mismatch", ingest(J1, A[0], "delivery_failed", [("outcome_mismatch", "result.json#/outcome")]))
    unattr = ingest(J2, A[5], "conflict", [("attempt_conflict", "result.json")])
    unattr["attempt_no"] = None
    write("ingest_record.unattributable", unattr)
    write("handoff_record.accepted", {"protocol_version": 1, "kind": "handoff_record", "report_id": "local-20261008-01",
          "verdict": "accepted", "reasons": [], "received_at": "2026-10-08T09:30:00Z"})
    write("handoff_record.quarantined", {"protocol_version": 1, "kind": "handoff_record", "report_id": "local-20261008-02",
          "verdict": "quarantined", "reasons": [{"rule": "TX-IPV4", "ref": "handoff"}], "received_at": "2026-10-08T09:31:00Z"})
    write("ingest_record.structure_limit", ingest(J2, A[1], "delivery_failed",
          [("structure_limit_exceeded", "preview")], attempt_no=2))

    # ---- identity / canonical vectors (cross-language) ---------------------
    vectors = []

    def canon(name, value):
        b = canonical_bytes(value)
        vectors.append({"name": name, "kind": "canonical", "input": value,
                        "expected_canonical": b.decode("utf-8"), "expected_sha256": hashlib.sha256(b).hexdigest()})

    def ident(name, s, bundle):
        vectors.append({"name": name, "kind": "identity", "input": s, "bundle_sha256": bundle,
                        "expected_sha256": job_identity_sha256(s, bundle)})

    canon("tiny-object", {"b": 1, "a": "x"})
    canon("string-escapes", {"s": "quote\" backslash\\ tab\t nl\n unicode é"})
    canon("utf16-key-order", {"ﬁ": 4, "\U0001F600": 3, "é": 2, "z": 1})
    canon("nested-and-ints", {"n": [3, -1, 0, 9007199254740991], "o": {"y": None, "x": True, "w": False}})
    ident("submission-example", sub, BUNDLE1)
    other_key = dict(sub, idempotency_key="another-key")
    ident("same-content-other-key", other_key, BUNDLE1)
    explicit = copy.deepcopy(sub)
    explicit.update(parent_job_id=None)
    explicit["export"]["final_delivery"] = "retained_local"
    explicit["limits"]["max_upload_mb"] = 200
    explicit["artifacts"] = ["contact", "keyframe", "log"]
    ident("explicit-defaults-and-order", explicit, BUNDLE1)
    ident("same-submission-other-bundle", sub, BUNDLE2)
    (OUT / "vectors" / "identity-vectors.json").write_text(json.dumps(vectors, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")

    # ---- bundle path vectors (Worker must reproduce on Windows) -----------
    sets = [
        [("scene/index.html", False), ("scene/.gitkeep", False), ("scene", True), ("assets/tex-01_a.png", False)],
        [("console/readme.md", False)], [("comet.js", False)], [("con1.txt", False)],
        [("../a", False)], [("a/./b", False)], [("a\\b", False)], [("a b", False)], [("e\u0301.txt", False)],
        [("PROGRA~1", False)], [("file:stream", False)], [("a$b", False)], [("file.", False)], [("dir./x", False)],
        [("CON", False)], [("con.txt", False)], [("Aux.tar.gz", False)], [("nul", False)], [("COM1", False)],
        [("com0.log", False)], [("LPT9.x", False)], [("scene/prn", False)], [("/abs", False)], [("a//b", False)],
        [("dir/", False)], [("x" * 201, False)], [("/".join(["d"] * 17), False)],
        [("Scene/a.js", False), ("scene/a.js", False)], [("README.md", False), ("readme.md", False)],
        [("x", False), ("x/y", False)], [("X", False), ("x/y", False)], [("a/b", False), ("a/c", False), ("a", True)],
        [("scene.js", False), ("scene.js", False)], [("Scene/a.js", False), ("scene/b.js", False)],
        [("a/b.js", False), ("a", True)], [("a", True), ("a/b.js", False)], [("a/b.js", False), ("A", True)],
        [("a", True), ("a", True)], [("lib/Util/x.js", False), ("lib/util/y.js", False)],
    ]
    pv = [{"entries": [{"path": p, "is_dir": d} for p, d in es], "expected": check_paths(es)} for es in sets]
    (OUT / "vectors" / "bundle-path-vectors.json").write_text(json.dumps(pv, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print("examples:", len(list(OUT.glob("*.json"))), "vectors:", len(vectors))


if __name__ == "__main__":
    main()
