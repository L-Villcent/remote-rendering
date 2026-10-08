"""Group 2 - CROSS-FIELD SEMANTIC tests (stdlib only).

R1 identity / canonical JSON, R2 legal early-failure results, R4 cross-field
rules and bundle path rules (incl. Windows aliasing).  Inputs are schema-valid
examples or minimal mutations of them.
"""
import copy
import io
import json
import tarfile
import unittest

from _common import EXAMPLES, def_name, jobs_by_id, load_examples, mutate

from render_protocol import bundle as B
from render_protocol.canonical import (ProtocolJSONError, canonical_bytes, decide_publish,
                                       job_identity_sha256, normalize_submission, parse_strict)
from render_protocol.semantics import check_heartbeat, check_result, check_status, check_submission

EX = load_examples()
JOBS = jobs_by_id(EX)
J1, J2 = EX["job"], EX["job.preview"]
VECTORS = json.loads((EXAMPLES / "vectors" / "identity-vectors.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------- R1 identity
class IdentityTests(unittest.TestCase):
    def test_SE01_vectors(self):
        import hashlib
        for v in VECTORS:
            with self.subTest(vector=v["name"]):
                if v["kind"] == "canonical":
                    b = canonical_bytes(v["input"])
                    self.assertEqual(b.decode("utf-8"), v["expected_canonical"])
                    self.assertEqual(hashlib.sha256(b).hexdigest(), v["expected_sha256"])
                else:
                    self.assertEqual(job_identity_sha256(v["input"], v["bundle_sha256"]), v["expected_sha256"])

    def test_SE02_key_excluded_defaults_and_order_normalized(self):
        by = {v["name"]: v["expected_sha256"] for v in VECTORS}
        self.assertEqual(by["submission-example"], by["same-content-other-key"])
        self.assertEqual(by["submission-example"], by["explicit-defaults-and-order"])
        self.assertNotEqual(by["submission-example"], by["same-submission-other-bundle"])

    def test_SE03_publish_decisions(self):
        sub, b1, b2 = EX["submission"], J1["bundle"]["sha256"], J2["bundle"]["sha256"]
        idx = {sub["idempotency_key"]: (job_identity_sha256(sub, b1), J1["job_id"])}
        self.assertEqual(decide_publish(idx, sub["idempotency_key"], job_identity_sha256(sub, b1)), ("duplicate", J1["job_id"]))
        self.assertEqual(decide_publish(idx, sub["idempotency_key"], job_identity_sha256(sub, b2)), ("conflict", None))
        changed = mutate(sub, ["params", "palette"], "noon")
        self.assertEqual(decide_publish(idx, sub["idempotency_key"], job_identity_sha256(changed, b1)), ("conflict", None))
        self.assertEqual(decide_publish(idx, "other-key", job_identity_sha256(sub, b1)), ("new", None))

    def test_SE04_job_json_is_normalized_submission_plus_publish_fields(self):
        sub = EX["submission"]
        expected = normalize_submission(sub)
        publish_fields = {"job_id", "created_at", "bundle", "identity_sha256", "publish_rules_version"}
        self.assertEqual({k: v for k, v in J1.items() if k not in publish_fields}, expected)
        self.assertEqual(J1["identity_sha256"], job_identity_sha256(sub, J1["bundle"]["sha256"]))

    def test_SE05_strict_parse(self):
        for bad, code in [(b'{"a":1,"a":2}', "duplicate_key"), (b'{"a":1.5}', "float_not_allowed"),
                          (b'{"a":NaN}', "non_finite_number"), (b'\xef\xbb\xbf{}', "bom"),
                          (b'{"a":"\xff"}', "not_utf8"), (b'{"a":', "malformed")]:
            with self.subTest(code=code):
                with self.assertRaises(ProtocolJSONError) as cm:
                    parse_strict(bad)
                self.assertEqual(str(cm.exception), code)
        self.assertEqual(parse_strict(b'{"b":1,"a":[true,null]}'), {"b": 1, "a": [True, None]})

    def test_SE06_canonical_rejects(self):
        for bad in [{"a": 1.0}, {"a": 2**53}, {1: "x"}, {"a": "\ud800"}]:
            with self.subTest(value=repr(bad)[:20]):
                with self.assertRaises(ProtocolJSONError):
                    canonical_bytes(bad)

    def test_SE07_whitespace_and_key_order_do_not_matter(self):
        a = parse_strict(b'{ "b" : 1, "a" : "x" }')
        b = parse_strict(b'{"a":"x","b":1}')
        self.assertEqual(canonical_bytes(a), canonical_bytes(b))


# ---------------------------------------------------------------- R4 bundle
def make_tar(members):
    """members: list of (name, kind, data) where kind in reg, dir, sym, lnk, chr, fifo."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.USTAR_FORMAT) as tf:
        for name, kind, data in members:
            ti = tarfile.TarInfo(name)
            ti.mtime = 0
            if kind == "reg":
                ti.size = len(data)
                tf.addfile(ti, io.BytesIO(data))
                continue
            ti.type = {"dir": tarfile.DIRTYPE, "sym": tarfile.SYMTYPE, "lnk": tarfile.LNKTYPE,
                       "chr": tarfile.CHRTYPE, "fifo": tarfile.FIFOTYPE}[kind]
            if kind in ("sym", "lnk"):
                ti.linkname = "scene/index.html"
            tf.addfile(ti)
    buf.seek(0)
    return buf


OK_TAR = [("scene", "dir", b""), ("scene/index.html", "reg", b"<html></html>"), ("scene/main.js", "reg", b"//")]


class BundleTests(unittest.TestCase):
    def test_SE10_good_paths(self):
        self.assertEqual(B.check_paths([("scene/index.html", False), ("scene/.gitkeep", False), ("scene", True),
                                        ("assets/tex-01_a.png", False), ("console/readme.md", False)]), [])

    def test_SE11_path_violations(self):
        cases = {
            "../a": "XF-B01", "a/./b": "XF-B01", "a\\b": "XF-B02", "a b": "XF-B02", "é.txt": "XF-B02",
            "PROGRA~1": "XF-B02", "file:stream": "XF-B02", "a$b": "XF-B02",
            "file.": "XF-B03", "dir./x": "XF-B03",
            "CON": "XF-B04", "con.txt": "XF-B04", "Aux.tar.gz": "XF-B04", "nul": "XF-B04",
            "COM1": "XF-B04", "com0.log": "XF-B04", "LPT9.x": "XF-B04", "scene/prn": "XF-B04",
            "/abs": "XF-B05", "a//b": "XF-B05", "dir/": "XF-B05",
            "x" * 201: "XF-B06", "/".join(["d"] * 17): "XF-B06",
        }
        for path, code in cases.items():
            with self.subTest(path=path[:30]):
                self.assertIn(code, B.check_paths([(path, False)]))

    def test_SE12_case_and_type_aliasing(self):
        self.assertIn("XF-B07", B.check_paths([("Scene/a.js", False), ("scene/a.js", False)]))
        self.assertIn("XF-B07", B.check_paths([("README.md", False), ("readme.md", False)]))
        self.assertIn("XF-B08", B.check_paths([("x", False), ("x/y", False)]))
        self.assertIn("XF-B08", B.check_paths([("X", False), ("x/y", False)]))
        self.assertEqual(B.check_paths([("a/b", False), ("a/c", False), ("a", True)]), [])

    def test_SE16_path_vectors(self):
        vectors = json.loads((EXAMPLES / "vectors" / "bundle-path-vectors.json").read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(vectors), 30)
        self.assertTrue(any(v["expected"] == [] for v in vectors))
        for i, v in enumerate(vectors):
            with self.subTest(vector=i):
                self.assertEqual(B.check_paths([(e["path"], e["is_dir"]) for e in v["entries"]]), v["expected"])

    def test_SE17_tar_duplicates_and_directory_case(self):
        dup = OK_TAR + [("scene/main.js", "reg", b"// second copy")]
        self.assertEqual(B.check_tar(make_tar(dup)), ["XF-B13"])
        self.assertEqual(B.check_tar(make_tar([("scene.js", "reg", b"1"), ("scene.js", "reg", b"2")])), ["XF-B13"])
        self.assertEqual(B.check_tar(make_tar([("Scene/a.js", "reg", b""), ("scene/b.js", "reg", b"")])), ["XF-B07"])
        self.assertEqual(B.check_tar(make_tar([("a/b.js", "reg", b""), ("A", "dir", b"")])), ["XF-B07"])
        self.assertEqual(B.check_tar(make_tar([("a/b.js", "reg", b""), ("a", "dir", b"")])), [])   # implicit then explicit
        self.assertEqual(B.check_tar(make_tar([("a", "dir", b""), ("a", "dir", b"")])), ["XF-B13"])

    def test_SE13_tar_ok_and_entrypoint(self):
        self.assertEqual(B.check_tar(make_tar(OK_TAR), entrypoint="scene/index.html"), [])
        self.assertEqual(B.check_tar(make_tar(OK_TAR), entrypoint="scene/missing.html"), ["XF-J12"])
        self.assertEqual(B.check_tar(make_tar(OK_TAR), entrypoint="scene"), ["XF-J12"])

    def test_SE14_tar_member_types(self):
        for kind in ("sym", "lnk", "chr", "fifo"):
            with self.subTest(kind=kind):
                self.assertIn("XF-B10", B.check_tar(make_tar(OK_TAR + [("scene/x", kind, b"")])))

    def test_SE15_tar_limits_and_corruption(self):
        many = OK_TAR + [(f"scene/f{i}.txt", "reg", b"") for i in range(B.MAX_ENTRIES)]
        self.assertIn("XF-B09", B.check_tar(make_tar(many)))
        old = B.MAX_UNPACKED_BYTES
        try:
            B.MAX_UNPACKED_BYTES = 10
            self.assertIn("XF-B11", B.check_tar(make_tar(OK_TAR + [("scene/big.bin", "reg", b"x" * 64)])))
        finally:
            B.MAX_UNPACKED_BYTES = old
        self.assertEqual(B.check_tar(io.BytesIO(b"not a tar file" * 100)), ["XF-B12"])
        self.assertIn("XF-B04", B.check_tar(make_tar(OK_TAR + [("scene/CON.js", "reg", b"")])))
        self.assertIn("XF-B07", B.check_tar(make_tar(OK_TAR + [("Scene/Main.js", "reg", b"")])))


# ---------------------------------------------------------------- R4 submission
class SubmissionRuleTests(unittest.TestCase):
    def test_SE20_examples_pass(self):
        for stem, doc in EX.items():
            if def_name(stem) in ("submission", "job"):
                with self.subTest(example=stem):
                    self.assertEqual(check_submission(doc), [])

    def test_SE21_violations(self):
        s, p = EX["submission"], EX["job.preview"]
        cases = [
            ("XF-J01", mutate(s, ["render", "frame_end_exclusive"], 0)),
            ("XF-J02", mutate(s, ["render", "keyframes"], delete=True)),
            ("XF-J03", mutate(s, ["render", "keyframes"], [0, 150])),
            ("XF-J04", mutate(s, ["export", "profiles"], ["png-rgb8-v1", "h264-preview-v1"])),
            ("XF-J04", mutate(p, ["export", "profiles"], ["png-rgb8-v1"])),
            ("XF-J05", mutate(s, ["artifacts"], ["log", "keyframe", "preview"])),
            ("XF-J05", mutate(s, ["artifacts"], ["keyframe"])),
            ("XF-J06", mutate(p, ["artifacts"], ["log", "preview"])),
            ("XF-J07", mutate(s, ["export", "final_delivery"], "upload")),
            ("XF-J08", mutate(p, ["render", "frame_end_exclusive"], 30 * 21)),
            ("XF-J10", mutate(p, ["artifacts"], ["log", "contact", "preview", "keyframe"])),
        ]
        final = mutate(mutate(mutate(p, ["stage"], "final"), ["export", "profiles"], ["h264-final-v1", "png-rgb8-v1"]),
                       ["artifacts"], ["log", "contact", "final"])
        final["export"]["final_delivery"] = "upload"
        self.assertEqual(check_submission(final), [])
        cases.append(("XF-J09", mutate(final, ["render", "frame_end_exclusive"], 30 * 601)))
        for code, doc in cases:
            with self.subTest(code=code):
                self.assertIn(code, check_submission(doc))


# ---------------------------------------------------------------- R2 / R4 result
class ResultRuleTests(unittest.TestCase):
    def test_SE30_all_result_examples_pass_against_their_job(self):
        names = [n for n in EX if def_name(n) == "result"]
        self.assertGreaterEqual(len(names), 8)
        for n in names:
            with self.subTest(example=n):
                self.assertEqual(check_result(JOBS[EX[n]["job_id"]], EX[n]), [])

    def test_SE31_required_early_failure_examples_exist(self):
        for n in ("result.cancelled_before_start", "result.browser_missing", "result.download_failed",
                  "result.gpu_query_unavailable", "result.failed_retry_scheduled", "result.job_unreadable"):
            self.assertIn(n, EX)
        self.assertEqual(EX["result.failed_retry_scheduled"]["exit"]["code"], 3221225477)  # Windows NTSTATUS-style DWORD

    def test_SE32_violations(self):
        r, rp, cb = EX["result"], EX["result.preview_succeeded"], EX["result.cancelled_before_start"]
        kf = [a for a in r["artifacts"] if a["kind"] != "keyframe"]
        dup = copy.deepcopy(r)
        dup["artifacts"].append(copy.deepcopy(dup["artifacts"][1]))
        final_job = mutate(mutate(mutate(J2, ["stage"], "final"), ["export", "profiles"], ["h264-final-v1", "png-rgb8-v1"]),
                           ["artifacts"], ["contact", "final", "log"])
        final_art = {"artifact_id": "final", "kind": "final", "media_type": "video/mp4", "export_profile": "h264-final-v1",
                     "delivery": "uploaded", "size_bytes": 1000, "sha256": "0" * 64}
        rfinal = mutate(rp, ["artifacts"], rp["artifacts"][:2] + [final_art])
        cases = [
            ("XF-R01", J1, mutate(r, ["job_id"], J2["job_id"])),
            ("XF-R02", J1, mutate(r, ["bundle_sha256"], "f" * 64)),
            ("XF-R02", J1, mutate(EX["result.browser_missing"], ["bundle_sha256"], None)),
            ("XF-R03", J1, mutate(r, ["adapter", "version"], "0.2.0")),
            ("XF-R04", J1, mutate(r, ["attempt_no"], 3)),
            ("XF-R05", J1, dup),
            ("XF-R06", J1, mutate(r, ["artifacts", 4, "kind"], "keyframe")),
            ("XF-R07", J1, mutate(r, ["artifacts", 0, "media_type"], "image/png")),
            ("XF-R08", J1, mutate(r, ["artifacts"], r["artifacts"] + [dict(final_art, artifact_id="preview", kind="preview", export_profile="h264-preview-v1")])),
            ("XF-R09", J2, mutate(rp, ["artifacts"], rp["artifacts"] + [dict(r["artifacts"][1])])),
            ("XF-R10", J1, mutate(r, ["artifacts", 1, "frame_index"], 5)),
            ("XF-R10", J1, mutate(r, ["artifacts", 4, "frame_index"], 0)),
            ("XF-R11", J1, mutate(r, ["artifacts", 1, "size_bytes"], 9 * 1024 * 1024)),
            ("XF-R11", J2, mutate(rp, ["artifacts", 2, "size_bytes"], 21 * 1024 * 1024)),
            ("XF-R12", final_job, mutate(rfinal, ["artifacts", 2, "delivery"], "uploaded")),
            ("XF-R13", J1, mutate(r, ["frames", "rendered"], 2)),
            ("XF-R13", J1, mutate(r, ["artifacts"], kf)),
            ("XF-R14", J2, mutate(rp, ["toolchain", "encoder"], None)),
            ("XF-R15", J1, mutate(r, ["frames"], {"expected": 150, "rendered": 150})),
            ("XF-R16", J1, mutate(EX["result.browser_missing"], ["frames", "rendered"], 4)),
            ("XF-R18", J1, mutate(cb, ["phase_reached"], "render")),
            ("XF-R18", J1, mutate(cb, ["frames", "rendered"], 1)),
            ("XF-R19", J2, mutate(EX["result.failed_retry_scheduled"], ["attempt_no"], 2)),
            ("XF-R19", J2, mutate(EX["result.failed_retry_scheduled"], ["error_class"], "gpu_oom")),
            ("XF-R20", J1, mutate(r, ["timings_ms", "total"], 100)),
            ("XF-R21", J1, mutate(EX["result.gpu_query_unavailable"], ["device", "gpu_busy_observed"], False)),
            ("XF-R23", J1, mutate(cb, ["artifacts"], [])),
        ]
        ju = EX["result.job_unreadable"]
        cases += [
            ("XF-R15", J1, mutate(EX["result.download_failed"], ["frames", "expected"], None)),   # job known -> must not be null
            ("XF-R15", J1, mutate(r, ["frames", "expected"], None)),
            ("XF-R02", J1, mutate(ju, ["frames", "expected"], 3)),                               # null fields go together
            ("XF-R03", J1, mutate(ju, ["frames", "expected"], 3)),
        ]
        self.assertEqual(check_result(J1, ju), [])
        # final retained locally: size is NOT subject to upload caps (review item)
        self.assertEqual(check_result(final_job, mutate(mutate(rfinal, ["artifacts", 2, "delivery"], "retained_local"),
                                                        ["artifacts", 2, "size_bytes"], 5 * 1024**3)), [])
        for code, job, doc in cases:
            with self.subTest(code=code):
                self.assertIn(code, check_result(job, doc))


# ---------------------------------------------------------------- status / heartbeat
class StatusRuleTests(unittest.TestCase):
    def test_SE40_examples_pass(self):
        self.assertEqual(check_status(J2, EX["status"]), [])
        self.assertEqual(check_heartbeat(EX["heartbeat"]), [])
        self.assertEqual(check_heartbeat(EX["heartbeat.idle"]), [])

    def test_SE41_violations(self):
        st, hb = EX["status"], EX["heartbeat"]
        self.assertIn("XF-S01", check_status(J1, st))
        self.assertIn("XF-S02", check_status(J2, mutate(st, ["attempt_no"], 3)))
        self.assertIn("XF-S03", check_status(J2, mutate(st, ["progress", "frames_done"], 151)))
        self.assertIn("XF-S04", check_status(J2, mutate(st, ["progress", "frames_total"], 149)))
        self.assertIn("XF-H01", check_heartbeat(mutate(hb, ["active_attempt_id"], None)))
        self.assertIn("XF-H01", check_heartbeat(mutate(EX["heartbeat.idle"], ["state"], "busy")))
        self.assertIn("XF-H01", check_heartbeat(mutate(hb, ["state"], "idle")))
        dup = mutate(hb, ["capabilities", "adapters"], hb["capabilities"]["adapters"] * 2)
        self.assertIn("XF-H02", check_heartbeat(dup))


if __name__ == "__main__":
    unittest.main()
