"""Filesystem accesses each identity performs (logical paths under config.Layout).

Checked against deploy/access-matrix.json by tests (and by FS-17 after deployment),
so code and the security-boundary permission table cannot drift apart silently.
"""
ACCESS = {
    "render-publish": {
        "read": ["outbox/submissions", "outbox/bundles", "outbox/control", "down/jobs", "inbox/jobs", "etc"],
        "write": ["down/.staging", "down/jobs", "down/control", "inbox/submissions", "publish_private"],
    },
    "render-ingest": {
        "read": ["up/_worker", "up/jobs", "up/handoff", "down/jobs", "down/control", "etc"],
        "write": ["down/acks", "inbox/jobs", "inbox/workers", "inbox/handoff", "ingest_private"],
    },
    "render-netcheck": {"read": ["etc"], "write": ["inbox/netcheck"]},
    "claude-agent": {"read": ["inbox"], "write": ["outbox"]},
    "render-worker": {"read": ["down"], "write": ["up"]},
}


def violations(matrix):
    """Accesses not granted by the matrix (prefix match on logical paths)."""
    out = []
    for ident, acc in ACCESS.items():
        grant = matrix.get(ident, {})
        for mode in ("read", "write"):
            for path in acc[mode]:
                ok = any(path == g or path.startswith(g + "/") for g in grant.get(mode, []))
                if not ok:
                    out.append(f"{ident} {mode} {path}")
    return sorted(out)
