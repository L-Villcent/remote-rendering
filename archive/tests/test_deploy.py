"""Group 6 - DEPLOY-STATIC: deployment scripts and probes, WITHOUT applying anything.

Runs the scripts only in dry-run and --render modes (no root, no system change),
then checks the rendered sshd / nftables / systemd / launcher files against the
security boundary and deploy/access-matrix.json.  Probe programs are tested through
their pure functions only; nothing here contacts the network or tailscaled.
Linux-only (bash, POSIX tools).
"""
import ipaddress
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

from _common import LINUX_ONLY, ROOT

sys.path.insert(0, str(ROOT / "deploy" / "probes"))
import fs_check  # noqa: E402
import sandbox_probe  # noqa: E402

VPS = ROOT / "deploy" / "vps"
SCRIPTS = sorted(VPS.glob("*.sh")) + [ROOT / "deploy" / "probes" / "canaries.sh"]
IP_CAND = re.compile(r"(?<![0-9A-Za-z_:.])[0-9A-Fa-f:.]{3,}(?:/\d{1,3})?(?![0-9A-Za-z_])")


def constants():
    vals = set()
    for line in (VPS / "net-constants.env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            vals.update(v.strip() for v in line.split("=", 1)[1].strip().strip('"').split(","))
    return vals


def address_literals(text):
    out = set()
    for tok in IP_CAND.findall(text):
        try:
            ipaddress.ip_network(tok, strict=False)
            if "." in tok and tok.count(".") == 3 or ":" in tok and tok.count(":") >= 2:
                out.add(tok)
        except ValueError:
            pass
    return out


@LINUX_ONLY
class DeployStatic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = pathlib.Path(tempfile.mkdtemp())
        env = {k: v for k, v in os.environ.items() if k not in ("APPLY", "RENDER_DIR", "I_HAVE_REVIEWED")}
        cls.dry = subprocess.run(["bash", str(VPS / "deploy.sh")], capture_output=True, text=True, encoding="utf-8", env=env)
        cls.rend = subprocess.run(["bash", str(VPS / "deploy.sh"), "--render", str(cls.tmp / "r")],
                                  capture_output=True, text=True, encoding="utf-8", env=env)
        cls.r = cls.tmp / "r"

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def read(self, path):
        return (self.r / path.lstrip("/")).read_text(encoding="utf-8")

    def test_DS01_syntax(self):
        for s in SCRIPTS:
            with self.subTest(script=s.name):
                self.assertEqual(subprocess.run(["bash", "-n", str(s)]).returncode, 0)
        for p in [*(ROOT / "deploy" / "probes").glob("*.py"), ROOT / "bin" / "render-gate"]:
            with self.subTest(py=p.name):
                compile(p.read_text(encoding="utf-8"), str(p), "exec")

    def test_DS02_dry_run_and_render_complete_without_root(self):
        self.assertEqual(self.dry.returncode, 0, self.dry.stderr[-300:])
        self.assertEqual(self.rend.returncode, 0, self.rend.stderr[-300:])
        self.assertIn("== 80-verify", self.dry.stdout)
        self.assertIn("+ fallocate", self.dry.stdout)
        self.assertTrue((self.r / "etc/ssh/sshd_config.d/50-render.conf").exists())

    def test_DS03_apply_refused_without_review_flag(self):
        env = {k: v for k, v in os.environ.items() if k != "I_HAVE_REVIEWED"}
        p = subprocess.run(["bash", str(VPS / "deploy.sh"), "--apply", "00-preflight"], capture_output=True, text=True, encoding="utf-8", env=env)
        self.assertEqual(p.returncode, 2)
        self.assertIn("refusing", p.stderr)

    def test_DS04_no_address_literals_outside_constants(self):
        allowed = constants()
        outputs = self.dry.stdout + self.rend.stdout
        for f in self.r.rglob("*"):
            if f.is_file():
                outputs += f.read_text(encoding="utf-8", errors="replace")
        stray = {a for a in address_literals(outputs) if a not in allowed and a.split("/")[0] not in allowed}
        self.assertEqual(stray, set())
        for s in SCRIPTS:
            if s.name != "net-constants.env":
                self.assertEqual({a for a in address_literals(s.read_text(encoding="utf-8")) if a not in allowed}, set(), s.name)

    def test_DS05_sshd(self):
        c = self.read("/etc/ssh/sshd_config.d/50-render.conf")
        req = "open,close,read,write,lstat,fstat,opendir,readdir,remove,mkdir,rmdir,realpath,stat,rename,posix-rename,fsync,limits"
        self.assertIn(f"ForceCommand internal-sftp -u 0027 -p {req}", c)
        for banned in ("setstat", "symlink", "hardlink", "copy-data"):
            self.assertNotIn(banned, c.split("ForceCommand internal-sftp")[1].split("\n")[0])
        self.assertIn("ChrootDirectory /srv/render", c)
        self.assertIn("ForceCommand /usr/local/lib/render/claude-attach", c)
        self.assertRegex(c, r"Match User render-worker,claude-agent Address \*,!\S+,!\S+\n\s+PubkeyAuthentication no")
        self.assertEqual(c.strip().splitlines()[-1], "Match all")
        self.assertNotIn("AllowUsers", c)                       # a global AllowUsers could lock out other accounts

    def test_DS06_nftables(self):
        n = self.read("/etc/render/nft/render-sandbox.nft")
        self.assertIn('meta skuid != "claude-agent" accept', n)
        self.assertIn("fib daddr type local counter reject", n)
        self.assertRegex(n, r"set deny4 \{[^}]*100\.64\.0\.0/10")
        self.assertRegex(n, r"set deny6 \{[^}]*fc00::/7")
        self.assertEqual(n.strip().splitlines()[-3].strip(), "counter reject")
        self.assertNotIn("flush ruleset", n)
        self.assertIn("Requires=render-nft.service", self.read("/etc/systemd/system/claude-sandbox.service"))

    def test_DS07_units_match_access_matrix(self):
        matrix = json.loads((ROOT / "deploy" / "access-matrix.json").read_text(encoding="utf-8"))
        units = {"render-publish": ["render-publish"], "render-ingest": ["render-ingest-light", "render-ingest-heavy"],
                 "render-netcheck": ["render-netcheck"]}
        for ident, names in units.items():
            for name in names:
                u = self.read(f"/etc/systemd/system/{name}.service")
                kv = {}
                for line in u.splitlines():
                    if "=" in line:
                        k, v = line.split("=", 1)
                        kv.setdefault(k, []).extend(v.split())
                rw, ro = set(kv.get("ReadWritePaths", [])), set(kv.get("ReadOnlyPaths", []))
                with self.subTest(unit=name):
                    for logical in matrix[ident]["write"]:
                        real = fs_check.real_path(logical)
                        self.assertTrue(any(real == p or real.startswith(p + "/") for p in rw), f"{name} write {logical}")
                    for logical in matrix[ident]["read"]:
                        real = fs_check.real_path(logical)
                        self.assertTrue(any(real == p or real.startswith(p + "/") for p in rw | ro), f"{name} read {logical}")
                    for k, v in (("PrivateNetwork", "yes"), ("NoNewPrivileges", "yes"), ("ProtectSystem", "strict"),
                                 ("RestrictAddressFamilies", "AF_UNIX"), ("IPAddressDeny", "any")):
                        self.assertIn(v, kv.get(k, []), f"{name} {k}")
                    self.assertIn("/opt/render-gates/current/bin/render-gate", u)
        self.assertIn("LoadCredential=netcheck-map:/etc/render/netcheck-map.json",
                      self.read("/etc/systemd/system/render-netcheck.service.d/credential.conf"))

    def test_DS08_mode_of_netcheck_map(self):
        entries = [line.split(" ") for line in (self.r / ".modes").read_text(encoding="utf-8").splitlines()]
        self.assertIn(["0600", "root:root", "/etc/render/netcheck-map.json"], entries)

    def test_DS09_launcher(self):
        e = self.read("/usr/local/lib/render/claude-entry")
        self.assertEqual(subprocess.run(["bash", "-n", str(self.r / "usr/local/lib/render/claude-entry")]).returncode, 0)
        for flag in ("/usr/bin/env -i", "--unshare-all", "--share-net", "--die-with-parent", "--new-session",
                     "--cap-drop ALL", "--no-map-gw", "-t none -u none -T none -U none", "DISABLE_AUTOUPDATER=1"):
            self.assertIn(flag, e)
        binds = re.findall(r"--(?:ro-)?bind(?:-try)? (\S+) (\S+)", e)
        sources = {s for s, _ in binds}
        for forbidden in ("/var", "/home/code", "/run/tailscale", "/srv/render", "/srv/render/up", "/srv/render-data/ingest", "/etc/render"):
            self.assertNotIn(forbidden, sources)
        self.assertTrue({"/srv/render-data/inbox", "/srv/agent/outbox", "/srv/agent/home", "/srv/agent/work"} <= sources)
        self.assertIn(("/srv/render-data/inbox", "/inbox"), [(s, d) for s, d in binds if s == "/srv/render-data/inbox"])
        self.assertRegex(e, r"--ro-bind /srv/render-data/inbox /inbox")
        resolv = self.read("/etc/render/sandbox/resolv.conf")
        magic = [line.split("=", 1)[1].strip().strip('"') for line in
                 (VPS / "net-constants.env").read_text(encoding="utf-8").splitlines() if line.startswith("MAGICDNS_V4=")][0]
        self.assertNotIn(magic, resolv)
        self.assertIn("nameserver", resolv)
        a = self.read("/usr/local/lib/render/claude-attach")
        self.assertIn("dtach -a /run/claude-sandbox/sock", a)
        self.assertNotIn("tmux", a)                             # no tmux client outside the sandbox

    def test_DS10_probe_pure_functions(self):
        tok = "CANARY-" + "a" * 32
        wl = ["HOME", "PATH"]
        self.assertEqual(sandbox_probe.env_ok({"HOME": "/home/agent"}, wl, tok), (True, "ok"))
        self.assertEqual(sandbox_probe.env_ok({"HOME": "/x", "SSH_CONNECTION": "x"}, wl, tok), (False, "unexpected_key"))
        self.assertEqual(sandbox_probe.env_ok({"HOME": "/x " + ".".join(["203", "0", "113", "5"])}, wl, tok), (False, "sensitive_value"))
        self.assertEqual(sandbox_probe.env_ok({"PATH": tok}, wl, tok), (False, "sensitive_value"))
        self.assertEqual(sandbox_probe.extra_fds(["0", "1", "2", "3", "9"], own=(3,)), [9])
        sample = "  sl  local_address rem_address\n   0: 0100007F:1F90 00000000:0000 0A\n   1: 00000000:0016 00000000:0000 0A\n"
        self.assertEqual(sandbox_probe.proc_net_ports(sample), {8080, 22})
        self.assertFalse(sandbox_probe.connects("nonexistent.invalid", 9, timeout=0.2))

    def test_DS11_probe_output_never_echoes(self):
        src = (ROOT / "deploy" / "probes" / "sandbox_probe.py").read_text(encoding="utf-8")
        prints = re.findall(r"print\((.*)\)", src)
        self.assertEqual(prints, ['"\\n".join(lines)'])           # only the PASS/FAIL lines
        self.assertTrue(all(re.match(r"SB-\d\d", c) for c in re.findall(r'rep\("([^"]+)"', src)))

    def test_DS12_fs_check_pure_functions(self):
        matrix = json.loads((ROOT / "deploy" / "access-matrix.json").read_text(encoding="utf-8"))
        for ident, acc in matrix.items():
            if ident.startswith("$"):
                continue
            for logical in acc["read"] + acc["write"]:
                self.assertTrue(fs_check.real_path(logical).startswith("/"), logical)
        self.assertTrue(fs_check.allocation_ok(8, 4096))
        self.assertFalse(fs_check.allocation_ok(0, 4096))
        self.assertTrue(fs_check.reserve_ok(58 << 30, 21 << 30))
        self.assertFalse(fs_check.reserve_ok(58 << 30, 9 << 30))
        mi = "36 25 7:0 / /srv/render/up rw,nosuid,nodev,noexec,relatime shared:1 - ext4 /dev/loop0 rw,nodiscard\n"
        self.assertIn("nodiscard", fs_check.mount_options(mi, "/srv/render/up"))
        self.assertIsNone(fs_check.mount_options(mi, "/srv/agent"))

    def test_DS13_release_manifest_and_service_roles(self):
        out = self.tmp / "rel"
        shutil.copytree(ROOT, out, ignore=shutil.ignore_patterns("dist", "交接包", "__pycache__", "*.zip"))
        p = subprocess.run([sys.executable, "-I", str(out / "tools" / "make_gate_release.py"), "9.9.9"],
                           capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(p.returncode, 0, p.stderr)
        x = self.tmp / "x"
        x.mkdir()
        subprocess.run(["tar", "-C", str(x), "-xf", str(out / "dist" / "render-gates-9.9.9.tar")], check=True)
        chk = subprocess.run(["sha256sum", "-c", "--quiet", str(out / "dist" / "render-gates-9.9.9.MANIFEST.sha256")],
                             cwd=x / "render-gates-9.9.9", capture_output=True)
        self.assertEqual(chk.returncode, 0)
        sim = self.tmp / "sim"
        for role in ("publish", "ingest-light", "ingest-heavy", "netcheck"):
            r = subprocess.run([sys.executable, "-IB", str(x / "render-gates-9.9.9" / "bin" / "render-gate"), role,
                                "--once", "--sim-root", str(sim), "--config", "/nonexistent"], capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(r.returncode, 0, (role, r.stderr[-200:]))
        view = json.loads((sim / "data" / "inbox" / "netcheck" / "status.json").read_text(encoding="utf-8"))
        self.assertEqual(view["error_code"], "not_configured")    # simulation never queried tailscaled


if __name__ == "__main__":
    unittest.main()
