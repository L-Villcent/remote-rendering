"""render-publish: outbox -> down/jobs + receipts (protocol-v1 6.2, 5.4).

One pass = run_once().  Every outbox file is treated as untrusted: snapshot
first (fsutil.snapshot), then strict parse, schema, XF-J, bundle rules,
credential scan, identity, idempotency, atomic publish, receipt.
"""
import os
import shutil
import tarfile

from render_protocol.bundle import check_tar
from render_protocol.canonical import ProtocolJSONError, decide_publish, job_identity_sha256, normalize_submission, parse_strict
from render_protocol.semantics import check_submission

from . import ids
from .config import Limits, load_json
from .fsutil import SnapshotError, atomic_write_json, list_entries, private_dir, snapshot
from .textrules import SECRET

JOB_URN = "urn:render-protocol:job:v1"
PUBLISH_RULES_VERSION = 1
KEY_FILE = r"^[a-z0-9][a-z0-9._-]{2,63}\.json$"
JOB_TERMINAL = {"succeeded", "failed", "cancelled", "quarantined", "delivery_failed"}
CREDENTIAL_NAMES = (".claude", ".credentials", "credentials.json", ".anthropic", ".netrc", "id_ed25519", "id_rsa")
MAX_SCAN_MEMBER = 2 * 1024 * 1024


class Publisher:
    def __init__(self, layout, schema, limits=Limits(), expect_uid=None, clock=None):
        self.lay, self.schema, self.lim, self.uid = layout, schema, limits, expect_uid
        self.clock = clock or __import__("time").time
        self.index_path = layout.publish_private / "index.json"

    # ------------------------------------------------------------- state
    def _index(self):
        return load_json(self.index_path, {"keys": {}, "cancels": {}, "seen": {}})

    def _changed(self, idx, rels):
        """Change detection only (stat of the outbox entries); the snapshot decides content."""
        sig = []
        for rel in rels:
            try:
                st = os.lstat(self.lay.outbox / rel)
                sig.append([st.st_ino, st.st_size, st.st_mtime_ns])
            except OSError:
                sig.append(None)
        key = "|".join(rels)
        if idx["seen"].get(key) == sig:
            return False
        idx["seen"][key] = sig
        return True

    def _save(self, idx):
        atomic_write_json(self.lay.publish_private, "index.json", idx)

    def _receipt(self, name, doc):
        atomic_write_json(self.lay.inbox / "submissions", name, doc)

    # ------------------------------------------------------------- submissions
    def run_once(self):
        idx = self._index()
        import re
        for name, is_dir in list_entries(self.lay.outbox, "submissions"):
            if is_dir or not re.match(KEY_FILE, name):
                continue
            key = name[:-5]
            if self._changed(idx, [f"submissions/{name}", f"bundles/{key}.tar"]):
                self._submission(idx, key)
        for name, is_dir in list_entries(self.lay.outbox, "control"):
            m = re.match(r"^(j_[0-9A-HJKMNP-TV-Z]{26})\.cancel\.json$", name)
            if m and not is_dir and self._changed(idx, [f"control/{name}"]):
                self._cancel(idx, m.group(1))
        self._save(idx)

    def _reject(self, key, reasons, identity=None):
        self._receipt(f"{key}.json", {
            "protocol_version": 1, "kind": "submission_receipt", "idempotency_key": key, "verdict": "rejected",
            "job_id": None, "identity_sha256": identity, "reasons": [{"rule": r, "ref": ref} for r, ref in reasons],
            "issued_at": ids.utc_now(self.clock())})

    def _submission(self, idx, key):
        snapdir = private_dir(self.lay.publish_private / "snap", "sub")
        try:
            try:
                _, _, raw = snapshot(self.lay.outbox, f"submissions/{key}.json", self.lim.submission_bytes, expect_uid=self.uid)
            except SnapshotError as e:
                return self._reject(key, [(e.code, "submission.json")])
            try:
                sub = parse_strict(raw)
            except ProtocolJSONError:
                return self._reject(key, [("malformed_json", "submission.json")])
            if not self.schema.is_valid(sub, JOB_URN + "#/$defs/submission") or sub["idempotency_key"] != key:
                return self._reject(key, [("schema_invalid", "submission.json")])
            reasons = [(c, "submission.json") for c in check_submission(sub)]
            bundle_path = snapdir / "bundle.tar"
            try:
                bundle_sha, bundle_size, _ = snapshot(self.lay.outbox, f"bundles/{key}.tar", self.lim.bundle_bytes,
                                                      dest=bundle_path, expect_uid=self.uid)
            except SnapshotError as e:
                return self._reject(key, reasons + [(e.code, "bundle")])
            with open(bundle_path, "rb") as f:
                reasons += [(c, "bundle") for c in check_tar(f, entrypoint=sub["entrypoint"])]
            if not reasons:
                reasons += self._credential_scan(bundle_path)
            if reasons:
                return self._reject(key, reasons)
            identity = job_identity_sha256(sub, bundle_sha)
            verdict, job_id = decide_publish({k: tuple(v) for k, v in idx["keys"].items()}, key, identity)
            now = self.clock()
            if verdict == "new":
                job_id = ids.job_id(int(now * 1000))
                with open(bundle_path, "rb") as f:
                    entries = sum(1 for _ in tarfile.open(fileobj=f, mode="r:"))
                job = normalize_submission(sub)
                job.update(job_id=job_id, created_at=ids.utc_now(now),
                           bundle={"format": "tar", "sha256": bundle_sha, "size_bytes": bundle_size, "entry_count": entries},
                           identity_sha256=identity, publish_rules_version=PUBLISH_RULES_VERSION)
                assert self.schema.is_valid(job, JOB_URN)
                stage = self.lay.down / ".staging" / job_id
                stage.mkdir(mode=0o750)
                shutil.copyfile(bundle_path, stage / "bundle.tar")
                atomic_write_json(stage, "job.json", job)
                os.rename(stage, self.lay.down / "jobs" / job_id)          # atomic publish
                idx["keys"][key] = [identity, job_id]
                verdict = "published"
            self._receipt(f"{key}.json", {
                "protocol_version": 1, "kind": "submission_receipt", "idempotency_key": key, "verdict": verdict,
                "job_id": job_id, "identity_sha256": identity,
                "reasons": [] if verdict != "conflict" else [{"rule": "identity_conflict", "ref": "submission.json"}],
                "issued_at": ids.utc_now(now)})
        finally:
            shutil.rmtree(snapdir, ignore_errors=True)

    def _credential_scan(self, bundle_path):
        with open(bundle_path, "rb") as f, tarfile.open(fileobj=f, mode="r:") as tf:
            for m in tf:
                low = m.name.lower()
                if any(part in CREDENTIAL_NAMES for part in low.split("/")):
                    return [("TX-SECRET", "bundle")]
                if m.isreg():
                    data = tf.extractfile(m).read(MAX_SCAN_MEMBER).decode("utf-8", errors="replace")
                    if SECRET.search(data) or "ANTHROPIC_API_KEY" in data or "ANTHROPIC_AUTH_TOKEN" in data:
                        return [("TX-SECRET", "bundle")]
        return []

    # ------------------------------------------------------------- cancel
    def _cancel(self, idx, job_id):
        name = f"{job_id}.cancel.json"
        now = self.clock()

        def receipt(verdict, req=None):
            self._receipt(name, {"protocol_version": 1, "kind": "cancel_receipt", "job_id": job_id, "verdict": verdict,
                                 "request_id": req, "issued_at": ids.utc_now(now)})
        try:
            _, _, raw = snapshot(self.lay.outbox, f"control/{name}", 4096, expect_uid=self.uid)
            doc = parse_strict(raw)
        except (SnapshotError, ProtocolJSONError):
            return receipt("rejected")
        if not self.schema.is_valid(doc, JOB_URN + "#/$defs/cancel_submission") or doc["job_id"] != job_id:
            return receipt("rejected")
        if not (self.lay.down / "jobs" / job_id).is_dir():
            return receipt("unknown_job")
        if job_id in idx["cancels"]:
            return receipt("already_requested", idx["cancels"][job_id])
        view = load_json(self.lay.inbox / "jobs" / job_id / "status.json", {})
        if view.get("display_state") in JOB_TERMINAL:
            return receipt("ignored_terminal")
        req = ids.request_id(int(now * 1000))
        atomic_write_json(self.lay.down / "control" / job_id, "cancel.json", {
            "protocol_version": 1, "kind": "cancel_request", "job_id": job_id, "request_id": req,
            "requested_at": ids.utc_now(now)})
        idx["cancels"][job_id] = req
        receipt("published", req)
