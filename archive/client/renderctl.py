"""renderctl: Claude-side CLI (protocol-v1 6.1, 5.3). Reads inbox, writes outbox only.

  renderctl [--root DIR] submit <submission.json> --bundle <dir> [--include FILE...]
  renderctl [--root DIR] receipt <idempotency_key>
  renderctl [--root DIR] status <job_id>
  renderctl [--root DIR] workers | netcheck
  renderctl [--root DIR] artifacts <job_id>
  renderctl [--root DIR] logs <job_id> [--tail N]
  renderctl [--root DIR] fetch <job_id> --kind keyframe|contact|preview
  renderctl [--root DIR] cancel <job_id>

--root points at a simulated tree (config.Layout.simulated); default is the sandbox
view (/outbox, /inbox) described in security-boundary 2.4.
Local prechecks (schema subset, XF-J, bundle paths) are a convenience only; the
security decision is render-publish's.  Output never includes file contents
other than the requested log tail, which already passed ingest's text rules.
"""
import argparse
import io
import json
import os
import pathlib
import sys
import tarfile

HERE = pathlib.Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent / "server"), str(HERE.parent / "refimpl")]

from render_gates.config import Layout, load_json  # noqa: E402
from render_gates.minischema import MiniSchema  # noqa: E402
from render_gates.fsutil import atomic_write_bytes, atomic_write_json  # noqa: E402
from render_protocol.bundle import check_paths  # noqa: E402
from render_protocol.canonical import parse_strict  # noqa: E402
from render_protocol.semantics import check_submission  # noqa: E402

EXT = {"keyframe": "png", "contact": "png", "preview": "mp4", "final": "mp4", "log": "txt"}


def reproducible_tar(src: pathlib.Path, include=None) -> bytes:
    """Sorted entries, mtime 0, uid/gid 0, fixed modes, ustar.  Symlinks are refused."""
    files = []
    for p in sorted(src.rglob("*")):
        rel = p.relative_to(src).as_posix()
        if include and not any(rel == i or rel.startswith(i.rstrip("/") + "/") for i in include):
            continue
        if p.is_symlink():
            raise SystemExit(f"refusing symlink in bundle: {rel}")
        files.append((rel, p))
    problems = check_paths([(rel, p.is_dir()) for rel, p in files])
    if problems:
        raise SystemExit("bundle path rules failed: " + ",".join(problems))
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.USTAR_FORMAT) as tf:
        for rel, p in files:
            ti = tarfile.TarInfo(rel)
            ti.mtime, ti.uid, ti.gid, ti.uname, ti.gname = 0, 0, 0, "", ""
            if p.is_dir():
                ti.type, ti.mode = tarfile.DIRTYPE, 0o755
                tf.addfile(ti)
            else:
                data = p.read_bytes()
                ti.size, ti.mode = len(data), 0o644
                tf.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


def cmd_submit(lay, a):
    sub = parse_strict(pathlib.Path(a.submission).read_bytes())
    if not MiniSchema.from_dir(HERE.parent / "schemas").is_valid(sub, "urn:render-protocol:job:v1#/$defs/submission"):
        raise SystemExit("precheck failed: schema_invalid")
    problems = check_submission(sub)
    if problems:
        raise SystemExit("precheck failed: " + ",".join(problems))
    key = sub["idempotency_key"]
    atomic_write_bytes(lay.outbox / "bundles", f"{key}.tar", reproducible_tar(pathlib.Path(a.bundle), a.include))
    atomic_write_json(lay.outbox / "submissions", f"{key}.json", sub)     # submission last (protocol 6.1)
    print(json.dumps({"submitted": key}))


def cmd_receipt(lay, a):
    r = load_json(lay.inbox / "submissions" / f"{a.key}.json")
    if r is None:
        print(json.dumps({"idempotency_key": a.key, "state": "submitted"}))
        return
    for d, name in (("submissions", f"{a.key}.json"), ("bundles", f"{a.key}.tar")):
        try:
            os.unlink(lay.outbox / d / name)              # receipt seen: clean up outbox originals
        except FileNotFoundError:
            pass
    print(json.dumps(r))


def cmd_status(lay, a):
    v = load_json(lay.inbox / "jobs" / a.job_id / "status.json")
    print(json.dumps(v if v is not None else {"job_id": a.job_id, "display_state": "unknown"}))


def _accepted_dir(lay, job_id):
    v = load_json(lay.inbox / "jobs" / job_id / "status.json") or {}
    cur = v.get("current_attempt") or {}
    d = lay.inbox / "jobs" / job_id / str(cur.get("attempt_id"))
    rec = load_json(d / "ingest.json") or {}
    return d if rec.get("verdict") == "accepted" else None, rec


def cmd_artifacts(lay, a):
    d, rec = _accepted_dir(lay, a.job_id)
    if d is None:
        print(json.dumps({"job_id": a.job_id, "verdict": rec.get("verdict"), "reasons": rec.get("reasons", [])}))
        return
    res = load_json(d / "result.json")
    print(json.dumps({"job_id": a.job_id, "attempt_id": res["attempt_id"], "outcome": res["outcome"],
                      "device": res["device"], "frames": res["frames"],
                      "artifacts": [{k: x[k] for k in ("artifact_id", "kind", "delivery") if k in x} for x in res["artifacts"]]}))


def cmd_logs(lay, a):
    d, rec = _accepted_dir(lay, a.job_id)
    if d is None:
        raise SystemExit(f"no accepted result (verdict={rec.get('verdict')})")
    lines = (d / "log.txt").read_text(encoding="utf-8", errors="replace").splitlines()
    print("\n".join(lines[-a.tail:]))


def cmd_fetch(lay, a):
    d, rec = _accepted_dir(lay, a.job_id)
    if d is None:
        raise SystemExit(f"no accepted result (verdict={rec.get('verdict')})")
    paths = sorted(str(p) for p in d.glob(f"{a.kind}*.{EXT[a.kind]}"))
    print(json.dumps({"job_id": a.job_id, "kind": a.kind, "paths": paths}))


def cmd_cancel(lay, a):
    atomic_write_json(lay.outbox / "control", f"{a.job_id}.cancel.json",
                      {"protocol_version": 1, "kind": "cancel_submission", "job_id": a.job_id})
    print(json.dumps({"cancel_submitted": a.job_id}))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="renderctl")
    ap.add_argument("--root", help="simulated tree root (tests); default: production layout")
    sp = ap.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("submit"); s.add_argument("submission"); s.add_argument("--bundle", required=True)
    s.add_argument("--include", nargs="*")
    sp.add_parser("receipt").add_argument("key")
    sp.add_parser("status").add_argument("job_id")
    sp.add_parser("workers"); sp.add_parser("netcheck")
    sp.add_parser("artifacts").add_argument("job_id")
    s = sp.add_parser("logs"); s.add_argument("job_id"); s.add_argument("--tail", type=int, default=100)
    s = sp.add_parser("fetch"); s.add_argument("job_id"); s.add_argument("--kind", required=True, choices=sorted(EXT))
    sp.add_parser("cancel").add_argument("job_id")
    a = ap.parse_args(argv)
    lay = Layout.simulated(a.root) if a.root else Layout.claude_view()
    if a.cmd == "workers":
        print(json.dumps(load_json(lay.inbox / "workers" / "worker-local.json")))
    elif a.cmd == "netcheck":
        print(json.dumps(load_json(lay.inbox / "netcheck" / "status.json")))
    else:
        globals()[f"cmd_{a.cmd}"](lay, a)


if __name__ == "__main__":
    main()
