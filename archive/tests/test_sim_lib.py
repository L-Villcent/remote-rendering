"""Group 4 - SIM-LIB: gate libraries in isolation (stdlib; jsonschema only for the differential test).

Maps to acceptance (simulated parts): TX-01..06, MX-02/03/07/08 (synthetic),
MX-05 (preliminary, ffmpeg if present), FS-10/11/12 primitives, GT-06.
Sensitive-looking inputs are assembled at runtime from documentation ranges.
"""
import copy
import os
import pathlib
import random
import shutil
import struct
import subprocess
import tempfile
import threading
import unittest

from _common import LINUX_ONLY, ROOT, SCHEMA_OF, def_name, doc_ip, load_examples, mutate

import media_fixtures as F
from render_gates import mediacheck as mc
from render_gates import netcheck, textrules
from render_gates.fsutil import SnapshotError, snapshot
from render_gates.minischema import MiniSchema

EX = load_examples()
MS = MiniSchema.from_dir(ROOT / "schemas")
IDS = {"job": "urn:render-protocol:job:v1", "status": "urn:render-protocol:status:v1", "result": "urn:render-protocol:result:v1"}


def ref(defname):
    sid = IDS[SCHEMA_OF[defname]]
    return sid if defname == "job" else f"{sid}#/$defs/{defname}"


# ===================================================================== minischema
class MiniSchemaTests(unittest.TestCase):
    def test_SL01_examples_and_negatives(self):
        import test_schemas as T
        for stem, doc in EX.items():
            with self.subTest(example=stem):
                self.assertTrue(MS.is_valid(doc, ref(def_name(stem))))
        for defname, label, doc in T.NEGATIVE:
            with self.subTest(negative=label):
                self.assertFalse(MS.is_valid(doc, ref(defname)))

    def test_SL02_differential_fuzz_against_jsonschema(self):
        try:
            import test_schemas as T
            if not T.HAVE_JSONSCHEMA:
                raise ImportError
        except ImportError:
            self.skipTest("jsonschema not installed: differential check not executed")
        rng = random.Random(20261008)
        values = [None, True, 0, -1, 4294967296, "", "x", "a_" + "1" * 26, [], {}, [1], {"k": 1}]
        disagreements, n = 0, 0

        def paths(node, base=()):
            yield base
            if isinstance(node, dict):
                for k, v in node.items():
                    yield from paths(v, base + (k,))
            elif isinstance(node, list):
                for i, v in enumerate(node):
                    yield from paths(v, base + (i,))

        for stem, doc in EX.items():
            d = def_name(stem)
            v = T.validator(d)
            ps = [p for p in paths(doc) if p]
            for _ in range(60):
                m = copy.deepcopy(doc)
                p = rng.choice(ps)
                parent = m
                for k in p[:-1]:
                    parent = parent[k]
                op = rng.random()
                if op < 0.4:
                    parent[p[-1]] = rng.choice(values)
                elif op < 0.7 and isinstance(parent, dict):
                    del parent[p[-1]]
                elif isinstance(parent, dict):
                    parent["zz_" + str(rng.randint(0, 9))] = rng.choice(values)
                n += 1
                if v.is_valid(m) != MS.is_valid(m, ref(d)):
                    disagreements += 1
        self.assertGreater(n, 1500)
        self.assertEqual(disagreements, 0)

    def test_SL03_unknown_keyword_is_an_error(self):
        ms = MiniSchema([{"$id": "urn:t", "type": "string", "contentEncoding": "base64"}])
        with self.assertRaises(ValueError):
            ms.is_valid("x", "urn:t")


# ===================================================================== text rules
IP4, IP6 = doc_ip(), ":".join(["2001", "db8"]) + "::7"
MAC = "-".join(["02", "00", "5e", "10", "00", "01"])
SENSITIVE = {
    "TX-IPV4": ["peer " + IP4, "x " + IP4 + ".", "release " + ".".join(["1", "0", "0", "1"])],
    "TX-IPV6": ["[" + IP6 + "]:443", "fe80" + "::1%eth0", "::ffff:" + IP4.replace("203", "198")],
    "TX-MAC": ["mac " + MAC, MAC.replace("-", ":")],
    "TX-WINPATH": [r"C:\Users\example\scene.png", r"\\fileserver\share\x", "d:/Users/example"],
    "TX-POSIXPATH": ["/home/example/x", "/mnt/c/x", "/Users/example", "/root/.ssh"],
    "TX-ENVDUMP": ["PATH=/usr/bin\nHOME=/x\nUSERNAME=example"],
    "TX-SECRET": ["token sk-ant-" + "x" * 24, "-----BEGIN OPENSSH " + "PRIVATE KEY-----", "Authorization: Bearer " + "y" * 30],
    "TX-TAILNET": ["node.example-tailnet.ts.net", "nodekey:" + "ab" * 16],
    "TX-TZ": ["2026-10-08T17:00:00+08:00", "2026-10-08 09:00-05:30", "UTC+8", "zone Europe/Paris"],
}
BENIGN = ["std::vector<int> v; 12:30:45 elapsed 00:01:02.5", "sha " + "ab" * 32, "Chrome 140.0.7339.80",
          "v1.2.3 frame 75/150 1280x720 30fps", "/home/agent/work/scene.js /home/code/x /rootfs /srv/render/up",
          "we discussed DERP, tailscale status, direct vs relay", "2026-10-08T09:00:00Z 2026-10-08 09:00:00+00:00",
          "the sk-ant-` prefix is listed", "version 1.2.3.4.5", "ratio 16:9", "C:/Program Files/app"]


class TextRuleTests(unittest.TestCase):
    def test_SL10_sensitive_corpus(self):            # TX-01
        for rule, samples in SENSITIVE.items():
            for s in samples:
                with self.subTest(rule=rule, sample=s[:12]):
                    self.assertIn(rule, textrules.scan_text(s))

    def test_SL11_benign_corpus(self):               # TX-03 / TX-05
        for s in BENIGN:
            with self.subTest(sample=s[:20]):
                self.assertEqual(textrules.scan_text(s), set())

    def test_SL12_scan_json_refs_are_vocabulary_only(self):   # TX-06 (refs)
        r = mutate(EX["result"], ["device", "gpu_model"], "GPU " + IP4)
        hits = textrules.scan_json(r, "result.json")
        self.assertEqual(hits, [("TX-IPV4", "result.json#/device/gpu_model")])
        self.assertNotIn(IP4, repr(hits))


# ===================================================================== media
def write(tmp, name, data):
    p = pathlib.Path(tmp) / name
    p.write_bytes(data)
    return p


def registry_from_fixtures(tmp):
    pngs = [write(tmp, f"p{i}.png", F.png(w, h)) for i, (w, h) in enumerate([(8, 8), (16, 9), (32, 18)])]
    mp4s = [write(tmp, f"v{i}.mp4", F.mp4(duration_s=d, w=w, h=h)) for i, (d, w, h) in enumerate([(1, 64, 36), (5, 128, 72)])]
    reg = {"profiles_version": 1, "profiles": {
        "png-rgb8-v1": mc.fingerprint([mc.describe(p, "png")[0] for p in pngs], "png", {"max_width": 3840, "max_height": 2160}),
        "h264-preview-v1": mc.fingerprint([mc.describe(p, "mp4-preview")[0] for p in mp4s], "mp4",
                                          {"max_width": 3840, "max_height": 2160, "max_duration_s": 20}),
    }}
    reg["profiles"]["h264-preview-v1"]["top_sequences"].append("^ftyp free mdat moov $")   # moov-at-end variant allowed
    return reg


class MediaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.reg = registry_from_fixtures(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def code(self, data, profile="png-rgb8-v1"):
        try:
            mc.check(write(self.tmp, "t.bin", data), profile, self.reg)
            return None
        except mc.MediaError as e:
            return e.code

    def test_SL20_valid_files_pass(self):
        self.assertIsNone(self.code(F.png(64, 36, idat_parts=5)))
        self.assertIsNone(self.code(F.mp4(duration_s=3, w=640, h=360), "h264-preview-v1"))
        self.assertIsNone(self.code(F.mp4(duration_s=3, moov_last=True), "h264-preview-v1"))

    def test_SL21_png_metadata_anywhere(self):       # MX-02 / MX-07
        tx = (b"tEXt", b"Comment\0" + IP4.encode())
        self.assertEqual(self.code(F.png(before_idat=[tx])), "profile_mismatch")
        self.assertEqual(self.code(F.png(after_idat=[tx])), "profile_mismatch")
        self.assertEqual(self.code(F.png(after_idat=[(b"eXIf", b"\0" * 8)])), "profile_mismatch")
        self.assertEqual(self.code(F.png(trailing=b"hidden")), "profile_mismatch")
        self.assertEqual(self.code(F.png(interlace=1)), "profile_mismatch")
        self.assertEqual(self.code(F.png(color_type=6)), "profile_mismatch")        # not in registered grammar

    def test_SL22_mp4_metadata_anywhere(self):       # MX-03 / MX-07
        udta = F.box(b"udta", F.fullbox(b"meta", F.box(b"ilst", F.box(b"\xa9nam", b"x"))))
        cases = {
            "udta in moov": F.mp4(udta=udta),
            "moov at end with udta": F.mp4(moov_last=True, udta=udta),
            "meta in trak": F.mp4(meta_in_trak=F.fullbox(b"meta", b"")),
            "free with payload": F.mp4(free_payload=b"x" * 16),
            "uuid top": F.mp4(extra_top=F.box(b"uuid", b"\0" * 16)),
            "trailing": F.mp4(trailing=b"tail!!"),
            "audio track": F.mp4(tracks=2),
            "handler name": F.mp4(hdlr_name=b"Recorded on example-host"),
            "compressor name": F.mp4(compressor=b"example"),
            "too long": F.mp4(duration_s=25),
        }
        for label, data in cases.items():
            with self.subTest(case=label):
                self.assertEqual(self.code(data, "h264-preview-v1"), "profile_mismatch")

    def test_SL23_limits(self):                       # MX-08
        many = F.png(w=8, h=8)
        sig_ihdr = many[:8 + 25]
        idat = F.chunk(b"IDAT", b"")
        too_many = sig_ihdr + idat * 8200 + F.chunk(b"IEND", b"")
        self.assertEqual(self.code(too_many), "structure_limit_exceeded")
        big_anc = F.png(before_idat=[(b"pHYs", b"\0" * (65 * 1024))])
        self.assertEqual(self.code(big_anc), "structure_limit_exceeded")
        big_moov = F.mp4(extra_moov=F.box(b"free", b"") + F.box(b"udta", b"\0" * (4 * 1024 * 1024 + 10)))
        self.assertEqual(self.code(big_moov, "h264-preview-v1"), "structure_limit_exceeded")
        deep = F.box(b"udta", b"")
        for _ in range(12):
            deep = F.box(b"udta", deep)
        self.assertEqual(self.code(F.mp4(extra_moov=deep), "h264-preview-v1"), "structure_limit_exceeded")
        bad_len = F.png()[:8] + struct.pack(">I", 10**6) + b"IHDR" + b"\0" * 20
        self.assertEqual(self.code(bad_len), "profile_mismatch")
        zero_box = F.mp4()[:24] + struct.pack(">I4s", 0, b"mdat")
        self.assertEqual(self.code(zero_box, "h264-preview-v1"), "profile_mismatch")
        stats = mc.check(write(self.tmp, "ok.png", F.png(64, 36, idat_parts=20)), "png-rgb8-v1", self.reg)
        self.assertLessEqual(stats.bytes_read, mc.LIMITS["png"]["max_read"])

    def test_SL24_ffmpeg_samples_prelim_mx05(self):
        ff = shutil.which("ffmpeg")
        if not ff:
            self.skipTest("ffmpeg not available")
        samples = []
        for i, (size, dur, src) in enumerate([("320x180", 1, "testsrc2"), ("640x360", 2, "mandelbrot"), ("1280x720", 1, "testsrc2")]):
            base = pathlib.Path(self.tmp) / f"ff{i}"
            common = ["-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", f"{src}=size={size}:rate=30",
                      "-map_metadata", "-1", "-fflags", "+bitexact", "-flags:v", "+bitexact"]
            subprocess.run([ff, *common, "-t", str(dur), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast",
                            "-movflags", "+faststart", "-an", f"{base}.mp4"], check=True)
            subprocess.run([ff, *common, "-frames:v", "1", f"{base}.png"], check=True)
            samples.append(base)
        reg = {"profiles_version": 1, "profiles": {
            "png-rgb8-v1": mc.fingerprint([mc.describe(f"{b}.png", "png")[0] for b in samples], "png",
                                          {"max_width": 3840, "max_height": 2160}),
            "h264-preview-v1": mc.fingerprint([mc.describe(f"{b}.mp4", "mp4-preview")[0] for b in samples], "mp4",
                                              {"max_width": 3840, "max_height": 2160, "max_duration_s": 20})}}
        for b in samples:
            for ext, prof, lim in (("png", "png-rgb8-v1", "png"), ("mp4", "h264-preview-v1", "mp4-preview")):
                s = mc.check(f"{b}.{ext}", prof, reg)
                self.assertLessEqual(s.bytes_read, mc.LIMITS[lim]["max_read"])
                self.assertLess(s.seconds, mc.LIMITS[lim]["max_seconds"])


# ===================================================================== snapshot
@LINUX_ONLY
class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.base = pathlib.Path(tempfile.mkdtemp())
        (self.base / "d").mkdir()

    def tearDown(self):
        shutil.rmtree(self.base)

    def code(self, rel, max_bytes=1 << 20, dest=None, uid=None):
        try:
            snapshot(self.base, rel, max_bytes, dest=dest, expect_uid=uid)
            return None
        except SnapshotError as e:
            return e.code

    def test_SL30_basic_codes(self):                   # FS-11 / FS-12 primitives, GT-08
        (self.base / "d" / "ok").write_bytes(b"hello")
        sha, size, data = snapshot(self.base, "d/ok", 100)
        self.assertEqual((size, data), (5, b"hello"))
        os.symlink("/etc/passwd", self.base / "d" / "link")
        os.symlink(self.base / "d", self.base / "dirlink")
        os.mkfifo(self.base / "d" / "fifo")
        (self.base / "d" / "ok2").write_bytes(b"x")
        os.link(self.base / "d" / "ok2", self.base / "d" / "hard")
        self.assertEqual(self.code("d/link"), "not_regular_file")
        self.assertEqual(self.code("dirlink/ok"), "not_regular_file")
        self.assertEqual(self.code("d/fifo"), "not_regular_file")         # does not block
        self.assertEqual(self.code("d/hard"), "not_regular_file")
        self.assertEqual(self.code("d/missing"), "missing_artifact")
        self.assertEqual(self.code("d/../d/ok"), "not_regular_file")
        self.assertEqual(self.code("d/ok", max_bytes=4), "size_exceeded")
        self.assertEqual(self.code("d/ok", uid=os.getuid() + 1), "not_regular_file")

    def test_SL31_writer_keeps_handle_open(self):      # FS-10
        p = self.base / "d" / "art"
        p.write_bytes(b"A" * (8 << 20))
        dest = self.base / "snap"
        with open(p, "r+b") as w:                        # uploader still holds a write handle
            sha, size, _ = snapshot(self.base, "d/art", 64 << 20, dest=dest)
            w.seek(0)
            w.write(b"B" * 1024)                         # modification after the snapshot
            w.flush()
        self.assertEqual(dest.read_bytes()[:1], b"A")    # snapshot unaffected
        import hashlib
        self.assertEqual(hashlib.sha256(dest.read_bytes()).hexdigest(), sha)
        self.assertNotEqual(hashlib.sha256(p.read_bytes()).hexdigest(), sha)

    def test_SL32_growth_during_copy(self):
        p = self.base / "d" / "grow"
        p.write_bytes(b"x" * 1000)
        stop = threading.Event()

        def grow():
            with open(p, "ab") as f:
                while not stop.is_set():
                    f.write(b"y" * 65536)
                    f.flush()
        t = threading.Thread(target=grow)
        t.start()
        try:
            code = self.code("d/grow", max_bytes=2 << 20, dest=self.base / "s2")
        finally:
            stop.set()
            t.join()
        self.assertIn(code, (None, "size_exceeded"))     # either a bounded consistent copy or refusal
        if code is None:
            self.assertLessEqual((self.base / "s2").stat().st_size, 2 << 20)


# ===================================================================== access matrix (D1)
R5_TABLE = {   # security-boundary-v1 r3 / protocol r5 section 4.2, transcribed as logical paths
    "render-publish": {"read": ["outbox", "down/jobs", "etc"],
                       "write": ["down/.staging", "down/jobs", "down/control", "down/handoff", "inbox/submissions", "publish_private"]},
    "render-ingest": {"read": ["up", "etc"], "write": ["down/acks", "inbox/jobs", "inbox/workers", "inbox/handoff", "ingest_private"]},
    "render-netcheck": {"read": ["etc"], "write": ["inbox/netcheck"]},
    "claude-agent": {"read": ["inbox"], "write": ["outbox"]},
    "render-worker": {"read": ["down"], "write": ["up"]},
}


class AccessTests(unittest.TestCase):
    def test_SL50_code_accesses_granted_by_matrix(self):
        import json as _j
        from render_gates.access import violations
        matrix = _j.loads((ROOT / "deploy" / "access-matrix.json").read_text(encoding="utf-8"))
        self.assertEqual(violations(matrix), [])

    def test_SL51_r5_table_was_missing_three_reads(self):
        from render_gates.access import violations
        self.assertEqual(violations(R5_TABLE), ["render-ingest read down/control", "render-ingest read down/jobs",
                                                "render-publish read inbox/jobs"])


# ===================================================================== netcheck
class NetcheckTests(unittest.TestCase):          # GT-06 (synthetic status only)
    def status(self, **peer):
        p = {"ID": "nSIM123", "HostName": "example-host", "DNSName": "example-host.example-tailnet.ts.net.",
             "TailscaleIPs": [".".join(["100", "64", "0", "9"])], "CurAddr": doc_ip() + ":41641", "Relay": "fra",
             "Online": True, "LastHandshake": "2026-10-08T09:00:00.123456789Z", "Addrs": [doc_ip() + ":41641"]}
        p.update(peer)
        return {"Self": {"ID": "nSELF", "TailscaleIPs": [".".join(["100", "64", "0", "1"])]}, "Peer": {"nodekey:" + "ab" * 32: p}}

    def run1(self, st, mapping={"worker-local": "nSIM123"}):
        import calendar
        now = calendar.timegm((2026, 10, 8, 9, 1, 0))
        v = netcheck.summarize(st, mapping, now, "2026-10-08T09:01:00Z")
        self.assertTrue(MS.is_valid(v, ref("netcheck_view")))
        self.assertEqual(textrules.scan_json(v, "netcheck"), [])
        return v

    def test_SL40_direct_relay_unknown(self):
        self.assertEqual(self.run1(self.status())["path"], "direct")
        self.assertEqual(self.run1(self.status(CurAddr=""))["path"], "relay")
        self.assertEqual(self.run1(self.status(CurAddr="", Relay=""))["path"], "unknown")
        v = self.run1(self.status())
        self.assertEqual((v["online"], v["known"], v["reachable"]), (True, True, True))

    def test_SL41_stale_offline_and_errors(self):
        self.assertFalse(self.run1(self.status(LastHandshake="2026-10-08T08:00:00Z"))["reachable"])
        self.assertFalse(self.run1(self.status(Online=False))["reachable"])
        self.assertEqual(self.run1(self.status(), mapping={})["error_code"], "not_configured")
        self.assertEqual(self.run1(self.status(ID="other"))["error_code"], "peer_unknown")
        self.assertEqual(self.run1(None)["error_code"], "daemon_unavailable")

    def test_SL42_output_never_contains_input_values(self):
        v = repr(self.run1(self.status()))
        for leak in (doc_ip(), "example-host", "ts.net", "nSIM123", "fra", "nodekey"):
            self.assertNotIn(leak, v)


if __name__ == "__main__":
    unittest.main()
