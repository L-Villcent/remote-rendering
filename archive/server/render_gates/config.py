"""Directory layout and limits (protocol-v1 sections 4 and 9)."""
import json
import pathlib
from dataclasses import dataclass

KIB, MIB = 1024, 1024 * 1024


@dataclass(frozen=True)
class Layout:
    outbox: pathlib.Path          # /srv/agent/outbox          claude-agent -> publish
    down: pathlib.Path            # /srv/render/down           publish/ingest -> Worker
    up: pathlib.Path              # /srv/render/up             Worker -> ingest
    inbox: pathlib.Path           # /srv/render-data/inbox     services -> Claude
    ingest_private: pathlib.Path  # /srv/render-data/ingest    ingest only
    publish_private: pathlib.Path  # /var/lib/render-publish   publish only
    etc: pathlib.Path             # /etc/render                admin, read-only for services

    @classmethod
    def production(cls):
        p = pathlib.Path
        return cls(p("/srv/agent/outbox"), p("/srv/render/down"), p("/srv/render/up"),
                   p("/srv/render-data/inbox"), p("/srv/render-data/ingest"),
                   p("/var/lib/render-publish"), p("/etc/render"))

    @classmethod
    def claude_view(cls):
        """Paths as seen inside the Claude sandbox (security-boundary 2.4): /outbox rw, /inbox ro."""
        p = pathlib.Path
        none = p("/nonexistent")
        return cls(p("/outbox"), none, none, p("/inbox"), none, none, none)

    @classmethod
    def simulated(cls, root):
        r = pathlib.Path(root)
        lay = cls(r / "agent/outbox", r / "render/down", r / "render/up", r / "data/inbox",
                  r / "data/ingest", r / "publish-state", r / "etc")
        for d in lay.all_dirs():
            d.mkdir(parents=True, exist_ok=True)
        return lay

    def all_dirs(self):
        return [
            self.outbox / "submissions", self.outbox / "bundles", self.outbox / "control", self.outbox / "handoff",
            self.down / "jobs", self.down / "control", self.down / "acks" / "_worker", self.down / "handoff",
            self.down / ".staging",
            self.up / "_worker", self.up / "jobs", self.up / "handoff",
            self.inbox / "workers", self.inbox / "jobs", self.inbox / "submissions", self.inbox / "handoff",
            self.inbox / "netcheck",
            self.ingest_private / "snap", self.ingest_private / "state" / "jobs", self.ingest_private / "quarantine",
            self.ingest_private / "stage",
            self.publish_private / "snap", self.etc,
        ]


@dataclass(frozen=True)
class Limits:
    submission_bytes: int = 64 * KIB
    bundle_bytes: int = 50 * MIB
    heartbeat_bytes: int = 4 * KIB
    status_bytes: int = 16 * KIB
    result_bytes: int = 256 * KIB
    handoff_bytes: int = 256 * KIB
    lease_seconds: int = 90
    upload_retry_limit: int = 3


def load_json(path, default=None):
    try:
        return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
