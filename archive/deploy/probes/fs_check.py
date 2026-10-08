"""FS-14 / FS-15 / FS-17 post-deployment checks (run as root by 80-verify).

Prints one 'PASS|FAIL <id> <what>' line per check; never file contents or addresses.
FS-17 uses runuser + test(1) and a short-lived '.fs17-*' probe file (ignored by the
gates' name filters) to prove each identity can reach exactly what
deploy/access-matrix.json grants, and that the private areas stay closed.
"""
import json
import os
import pathlib
import secrets
import subprocess
import sys

REAL = {"outbox": "/srv/agent/outbox", "down": "/srv/render/down", "up": "/srv/render/up",
        "inbox": "/srv/render-data/inbox", "ingest_private": "/srv/render-data/ingest",
        "publish_private": "/var/lib/render-publish", "etc": "/etc/render"}
IMAGES = {"up": "/srv/render/up", "down": "/srv/render/down", "data": "/srv/render-data", "agent": "/srv/agent"}
CLOSED = [  # (identity, path) that must NOT be readable
    ("claude-agent", "/srv/render/up"), ("claude-agent", "/srv/render-data/ingest"),
    ("claude-agent", "/etc/render/netcheck-map.json"), ("claude-agent", "/srv/render/down/.staging"),
    ("render-worker", "/srv/render-data/inbox"), ("render-worker", "/srv/agent/outbox"),
    ("render-ingest", "/srv/agent/outbox"), ("render-publish", "/srv/render/up"),
    ("render-netcheck", "/srv/render-data/ingest"), ("render-ingest", "/etc/render/netcheck-map.json"),
]
NOT_LISTABLE = ["/srv/render-data", "/srv/agent"]


def real_path(logical):
    head, _, rest = logical.partition("/")
    return REAL[head] + ("/" + rest if rest else "")


def allocation_ok(st_blocks, st_size):
    return st_blocks * 512 >= st_size


def reserve_ok(size, avail, gib=10, pct=20):
    return avail >= max(gib << 30, size * pct // 100)


def mount_options(mountinfo_text, mountpoint):
    for line in mountinfo_text.splitlines():
        f = line.split()
        if len(f) > 6 and f[4] == mountpoint:
            sep = f.index("-")
            return set(f[5].split(",")) | set(f[sep + 3].split(","))
    return None


def as_user(user, *test_args):
    return subprocess.run(["runuser", "-u", user, "--", "/usr/bin/test", *test_args],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


def can_create(user, directory):
    p = f"{directory}/.fs17-{secrets.token_hex(4)}"
    ok = subprocess.run(["runuser", "-u", user, "--", "/usr/bin/touch", p],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    if ok:
        os.unlink(p)
    return ok


def main(matrix_path, image_dir="/var/lib/render-images"):
    results = []

    def report(ok, cid, what):
        results.append(ok)
        print(f"{'PASS' if ok else 'FAIL'} {cid} {what}")

    mi = pathlib.Path("/proc/self/mountinfo").read_text(encoding="utf-8", errors="replace")
    for name, mp in IMAGES.items():
        img = pathlib.Path(image_dir) / f"{name}.img"
        try:
            st = img.stat()
            report(allocation_ok(st.st_blocks, st.st_size), "FS-14", f"{name} image fully allocated")
        except OSError:
            report(False, "FS-14", f"{name} image present")
        opts = mount_options(mi, mp) or set()
        report("nodiscard" in opts or "discard" not in opts, "FS-14", f"{name} mounted without discard")
    dropin = pathlib.Path("/etc/systemd/system/fstrim.service.d/render.conf")
    report(dropin.exists(), "FS-14", "fstrim limited to /")
    sv = os.statvfs("/")
    report(reserve_ok(sv.f_blocks * sv.f_frsize, sv.f_bavail * sv.f_frsize), "FS-15", "root reserve")
    matrix = json.loads(pathlib.Path(matrix_path).read_text(encoding="utf-8"))
    for ident, acc in matrix.items():
        if ident.startswith("$"):
            continue
        for logical in acc.get("read", []):
            p = real_path(logical)
            report(as_user(ident, "-r", p) and (not os.path.isdir(p) or as_user(ident, "-x", p)), "FS-17", f"{ident} read {logical}")
        for logical in acc.get("write", []):
            p = real_path(logical)
            report(os.path.isdir(p) and can_create(ident, p), "FS-17", f"{ident} write {logical}")
    for ident, p in CLOSED:
        report(not as_user(ident, "-r", p), "FS-17", f"{ident} cannot read {p}")
    for p in NOT_LISTABLE:
        report(not as_user("claude-agent", "-r", p) and not as_user("render-ingest", "-r", p), "FS-17", f"{p} not listable")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
