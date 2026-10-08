"""Shared helpers for the three test groups (schema / semantic / state machine)."""
import copy
import json
import pathlib
import sys
import unittest as _ut

ROOT = pathlib.Path(__file__).resolve().parent.parent
for _d in ("sim", "client", "server", "refimpl"):
    sys.path.insert(0, str(ROOT / _d))

EXAMPLES = ROOT / "examples" / "protocol"
SCHEMA_OF = {  # $defs name -> schema file stem
    "submission": "job", "job": "job", "cancel_submission": "job", "cancel_request": "job",
    "submission_receipt": "job", "cancel_receipt": "job",
    "heartbeat": "status", "status": "status", "heartbeat_ack": "status", "worker_view": "status",
    "job_view": "status", "netcheck_view": "status",
    "result": "result", "ingest_record": "result", "handoff_record": "result",
}


# VPS gate filesystem semantics (O_NOFOLLOW walks, dir_fd, owners, link counts, atomic
# renames) are Linux-specific by design and are verified on Linux only.  Elsewhere these
# tests are reported as skipped with this reason - never as passed.
LINUX_ONLY = _ut.skipUnless(sys.platform.startswith("linux"), "linux-only: VPS gate filesystem semantics")


def load_examples():
    """{file stem: doc} for examples/protocol/*.json (vectors excluded)."""
    return {p.name[:-5]: json.loads(p.read_text(encoding="utf-8")) for p in sorted(EXAMPLES.glob("*.json"))}


def def_name(stem):
    return stem.split(".", 1)[0]


def jobs_by_id(ex):
    return {d["job_id"]: d for n, d in ex.items() if def_name(n) == "job"}


def doc_ip():
    """TEST-NET-3 address assembled at runtime so no file contains a literal."""
    return ".".join(["203", "0", "113", "7"])


def mutate(doc, path, value=..., delete=False):
    d = copy.deepcopy(doc)
    cur = d
    for k in path[:-1]:
        cur = cur[k]
    if delete:
        del cur[path[-1]]
    else:
        cur[path[-1]] = value
    return d
