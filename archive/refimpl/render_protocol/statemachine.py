"""Reference state machine for protocol-v1 section 5 (R3).

Three separate dimensions, never stored in one field:
  * execution state per attempt   (reported by the Worker: status / result outcome)
  * delivery verdict per attempt  (decided by render-ingest)
  * job display state             (derived; terminal states are absorbing)

All methods return (accepted: bool, code: str|None) or a verdict string and
never raise on protocol-level misbehaviour.  Time comes in as a monotonic
reading supplied by the caller; wall clock is never used for liveness.
"""
from dataclasses import dataclass

EXEC_TERMINAL = {"succeeded", "failed", "cancelled"}
RANK = {None: 0, "leased": 1, "running": 2, "uploading": 3, "succeeded": 4, "failed": 4, "cancelled": 4}
JOB_TERMINAL = {"succeeded", "failed", "cancelled", "quarantined", "delivery_failed"}
ACTIVE_DISPLAY = {"leased", "running", "uploading", "delivering", "retry_pending"}

PRIVACY_REASONS = {"TX-IPV4", "TX-IPV6", "TX-MAC", "TX-WINPATH", "TX-POSIXPATH", "TX-ENVDUMP",
                   "TX-SECRET", "TX-TAILNET", "TX-TZ"}
RETRYABLE_REASONS = {"hash_mismatch", "missing_artifact", "unreadable"}

UPLOAD_RETRY_LIMIT = 3  # retransmissions of one attempt after 'rejected'


def decide_verdict(reasons, rejected_so_far, limit=UPLOAD_RETRY_LIMIT) -> str:
    """Content verdict for one delivery of an attempt (attempt-level checks done by JobTracker)."""
    reasons = set(reasons)
    if reasons & PRIVACY_REASONS:
        return "quarantined"
    if not reasons:
        return "accepted"
    if reasons <= RETRYABLE_REASONS and rejected_so_far < limit:
        return "rejected"
    return "delivery_failed"


@dataclass
class Attempt:
    attempt_id: str
    attempt_no: int
    last_seq: int = 0
    exec_state: str = None
    frames_done: int = -1
    rejected: int = 0
    verdict: str = None
    retry_scheduled: bool = False


class JobTracker:
    def __init__(self, job_id, max_attempts, upload_retry_limit=UPLOAD_RETRY_LIMIT):
        self.job_id = job_id
        self.max_attempts = max_attempts
        self.limit = upload_retry_limit
        self.attempts = {}          # attempt_no -> Attempt
        self.current_no = 0
        self.terminal = None
        self.cancel = "none"        # none | requested | honored | too_late | ignored_terminal

    # -- attempt identity -------------------------------------------------
    def _resolve(self, attempt_id, attempt_no):
        if attempt_no > self.max_attempts:
            return None, "attempt_limit_exceeded"
        for a in self.attempts.values():
            if a.attempt_id == attempt_id and a.attempt_no != attempt_no:
                return None, "attempt_conflict"
        a = self.attempts.get(attempt_no)
        if a is not None and a.attempt_id != attempt_id:
            return None, "attempt_conflict"
        if a is None:
            if attempt_no < self.current_no:
                return None, "superseded_attempt"
            a = Attempt(attempt_id, attempt_no)
            self.attempts[attempt_no] = a
            self.current_no = max(self.current_no, attempt_no)
        if a.attempt_no < self.current_no:
            return None, "superseded_attempt"
        return a, None

    # -- events -------------------------------------------------------------
    def on_status(self, attempt_id, attempt_no, seq, state, frames_done=None):
        if self.terminal:
            return False, "job_terminal"
        a, code = self._resolve(attempt_id, attempt_no)
        if code:
            return False, code
        if seq <= a.last_seq:
            return False, "stale_seq"
        if a.exec_state in EXEC_TERMINAL or a.verdict in ("accepted", "quarantined", "delivery_failed"):
            return False, "status_after_terminal"
        if RANK[state] < RANK[a.exec_state] or (RANK[state] == RANK[a.exec_state] and state not in ("running", "uploading")):
            return False, "illegal_transition"
        if frames_done is not None and state == a.exec_state and frames_done < a.frames_done:
            return False, "progress_regressed"
        a.last_seq, a.exec_state = seq, state
        if frames_done is not None:
            a.frames_done = frames_done
        return True, None

    def on_result(self, attempt_id, attempt_no, outcome, reasons=(), retry_scheduled=False):
        """Returns the verdict written to ingest.json / ack."""
        if self.terminal:
            return "stale"
        a, code = self._resolve(attempt_id, attempt_no)
        if code == "superseded_attempt":
            return "stale"
        if code:
            return "conflict"
        if a.verdict in ("accepted", "quarantined", "delivery_failed"):
            return "stale"
        reasons = list(reasons)
        if a.exec_state in EXEC_TERMINAL and outcome != a.exec_state:
            # Forward jumps are fine, contradicting an already reported terminal state is not.
            reasons.append("outcome_mismatch")
        verdict = decide_verdict(reasons, a.rejected, self.limit)
        a.verdict = verdict
        if verdict == "rejected":
            a.rejected += 1
            return verdict
        if verdict == "accepted":
            a.exec_state = outcome
            if outcome == "failed" and retry_scheduled and attempt_no < self.max_attempts:
                a.retry_scheduled = True
                return verdict
            self._finish(outcome)
        else:  # quarantined | delivery_failed
            self._finish(verdict)
        return verdict

    def _finish(self, state):
        self.terminal = state
        if self.cancel == "requested":
            self.cancel = "honored" if state == "cancelled" else "too_late"

    def on_cancel(self):
        if self.terminal:
            if self.cancel == "none":
                self.cancel = "ignored_terminal"
            return False, "job_terminal"
        self.cancel = "requested"
        return True, None

    # -- view ---------------------------------------------------------------
    def display(self, worker_health="alive"):
        if self.terminal:
            return self.terminal, []
        if self.current_no == 0:
            state = "queued"
        else:
            a = self.attempts[self.current_no]
            if a.retry_scheduled:
                state = "retry_pending"
            elif a.exec_state in EXEC_TERMINAL:
                state = "delivering"
            else:
                state = a.exec_state or "leased"
        overlays = []
        if self.cancel == "requested":
            overlays.append("cancel_requested")
        if state in ACTIVE_DISPLAY and worker_health in ("lost", "unconfirmed"):
            overlays.append("worker_lost" if worker_health == "lost" else "unconfirmed")
        return state, overlays


class WorkerHealth:
    """Heartbeat acceptance by (worker_epoch, seq); liveness by caller-supplied monotonic time."""

    def __init__(self, lease_seconds=90, persisted=None):
        self.lease = lease_seconds
        self.last = tuple(persisted) if persisted else (0, 0)
        self.state = "unconfirmed" if persisted else "never_seen"
        self.last_rx_mono = None

    def on_heartbeat(self, epoch, seq, now_mono):
        if (epoch, seq) <= self.last:
            return False, "stale_heartbeat"
        self.last = (epoch, seq)
        self.last_rx_mono = now_mono
        self.state = "alive"
        return True, None

    def tick(self, now_mono):
        if self.state == "alive" and now_mono - self.last_rx_mono > self.lease:
            self.state = "lost"
        return self.state

    def persist(self):
        return list(self.last)
