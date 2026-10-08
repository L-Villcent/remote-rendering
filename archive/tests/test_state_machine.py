"""Group 3 - STATE MACHINE tests (stdlib only), protocol-v1 section 5 (R3).

Execution state, delivery verdict and display state are tested separately;
terminal job states are absorbing; worker loss never creates attempts.
"""
import unittest

import _common  # noqa: F401  (adds refimpl/ to sys.path)

from render_protocol.statemachine import JobTracker, WorkerHealth, decide_verdict

J = "j_01JZ8Y3K5M7Q9R1T3V5X7Z9B2D"
A1, A2, A3, AX = ("a_" + c * 26 for c in "123X")


def tracker(max_attempts=2, limit=3):
    return JobTracker(J, max_attempts, limit)


def run_to(t, attempt=A1, no=1, states=("leased", "running", "uploading", "succeeded")):
    for seq, s in enumerate(states, 1):
        ok, code = t.on_status(attempt, no, seq, s)
        assert ok, code


class ExecutionAndDisplay(unittest.TestCase):
    def test_ST01_normal_path(self):
        t = tracker()
        self.assertEqual(t.display(), ("queued", []))
        for seq, s in enumerate(("leased", "running", "uploading"), 1):
            self.assertEqual(t.on_status(A1, 1, seq, s), (True, None))
            self.assertEqual(t.display()[0], s)
        t.on_status(A1, 1, 4, "succeeded")
        self.assertEqual(t.display()[0], "delivering")          # worker done, ingest not yet
        self.assertEqual(t.on_result(A1, 1, "succeeded"), "accepted")
        self.assertEqual(t.display(), ("succeeded", []))

    def test_ST02_forward_jump_accepted(self):
        t = tracker()                                            # ingest polled late: only terminal seen
        self.assertEqual(t.on_status(A1, 1, 9, "succeeded"), (True, None))
        self.assertEqual(t.display()[0], "delivering")

    def test_ST03_backward_transition_rejected_even_with_higher_seq(self):
        t = tracker()
        run_to(t, states=("leased", "running", "uploading"))
        self.assertEqual(t.on_status(A1, 1, 99, "running"), (False, "illegal_transition"))
        self.assertEqual(t.display()[0], "uploading")

    def test_ST04_no_status_after_exec_terminal(self):
        t = tracker()
        run_to(t)
        self.assertEqual(t.on_status(A1, 1, 99, "running"), (False, "status_after_terminal"))

    def test_ST05_stale_seq(self):
        t = tracker()
        run_to(t, states=("leased", "running"))
        self.assertEqual(t.on_status(A1, 1, 2, "running"), (False, "stale_seq"))
        self.assertEqual(t.on_status(A1, 1, 1, "uploading"), (False, "stale_seq"))

    def test_ST06_progress_regression(self):
        t = tracker()
        t.on_status(A1, 1, 1, "running", frames_done=50)
        self.assertEqual(t.on_status(A1, 1, 2, "running", frames_done=40), (False, "progress_regressed"))
        self.assertEqual(t.on_status(A1, 1, 3, "running", frames_done=60), (True, None))

    def test_ST07_result_without_prior_status(self):
        t = tracker()
        self.assertEqual(t.on_result(A1, 1, "failed"), "accepted")
        self.assertEqual(t.display()[0], "failed")


class AttemptIdentity(unittest.TestCase):
    def test_ST10_same_attempt_no_different_id(self):
        t = tracker()
        run_to(t, states=("leased", "running"))
        self.assertEqual(t.on_status(AX, 1, 5, "running"), (False, "attempt_conflict"))
        self.assertEqual(t.on_result(AX, 1, "succeeded"), "conflict")
        self.assertEqual(t.display()[0], "running")

    def test_ST11_same_id_different_attempt_no(self):
        t = tracker(max_attempts=3)
        run_to(t, states=("leased",))
        self.assertEqual(t.on_status(A1, 2, 5, "running"), (False, "attempt_conflict"))

    def test_ST12_attempt_limit(self):
        t = tracker(max_attempts=2)
        self.assertEqual(t.on_status(A3, 3, 1, "leased"), (False, "attempt_limit_exceeded"))
        self.assertEqual(t.on_result(A3, 3, "succeeded"), "conflict")
        self.assertEqual(t.display()[0], "queued")


class DeliveryVerdicts(unittest.TestCase):
    def test_ST20_quarantine_is_terminal(self):
        t = tracker()
        run_to(t)
        self.assertEqual(t.on_result(A1, 1, "succeeded", reasons=["TX-IPV4"]), "quarantined")
        self.assertEqual(t.display(), ("quarantined", []))
        self.assertEqual(t.on_status(A1, 1, 99, "running"), (False, "job_terminal"))
        self.assertEqual(t.on_result(A1, 1, "succeeded"), "stale")
        self.assertEqual(t.on_status(A2, 2, 1, "leased"), (False, "job_terminal"))

    def test_ST21_retry_exhaustion_is_terminal(self):
        t = tracker(limit=3)
        run_to(t)
        for i in range(3):
            self.assertEqual(t.on_result(A1, 1, "succeeded", reasons=["hash_mismatch"]), "rejected")
            self.assertEqual(t.display()[0], "delivering")
        self.assertEqual(t.on_result(A1, 1, "succeeded", reasons=["hash_mismatch"]), "delivery_failed")
        self.assertEqual(t.display(), ("delivery_failed", []))

    def test_ST22_rejected_then_accepted(self):
        t = tracker()
        run_to(t)
        self.assertEqual(t.on_result(A1, 1, "succeeded", reasons=["missing_artifact"]), "rejected")
        self.assertEqual(t.on_result(A1, 1, "succeeded"), "accepted")
        self.assertEqual(t.display()[0], "succeeded")

    def test_ST23_non_retryable_rejection_is_immediately_terminal(self):
        for reason in ("profile_mismatch", "structure_limit_exceeded", "schema_invalid", "XF-R13", "toolchain_unregistered"):
            with self.subTest(reason=reason):
                t = tracker()
                run_to(t)
                self.assertEqual(t.on_result(A1, 1, "succeeded", reasons=[reason]), "delivery_failed")

    def test_ST26_result_contradicting_reported_terminal(self):
        for reported, claimed in (("cancelled", "succeeded"), ("succeeded", "failed"), ("failed", "succeeded")):
            with self.subTest(reported=reported, claimed=claimed):
                t = tracker()
                run_to(t, states=("leased", "running", reported))
                self.assertEqual(t.on_result(A1, 1, claimed), "delivery_failed")
                self.assertEqual(t.display(), ("delivery_failed", []))
                self.assertEqual(t.attempts[1].exec_state, reported)      # not overwritten

    def test_ST27_result_matching_reported_terminal(self):
        for state in ("succeeded", "failed", "cancelled"):
            with self.subTest(state=state):
                t = tracker()
                run_to(t, states=("leased", "running", state))
                self.assertEqual(t.on_result(A1, 1, state), "accepted")
                self.assertEqual(t.display()[0], state)

    def test_ST28_forward_jump_then_consistent_result(self):
        t = tracker()
        t.on_status(A1, 1, 5, "cancelled")                       # only the terminal was observed
        self.assertEqual(t.on_result(A1, 1, "cancelled"), "accepted")
        self.assertEqual(t.display()[0], "cancelled")

    def test_ST24_privacy_wins_over_retryable(self):
        self.assertEqual(decide_verdict(["hash_mismatch", "TX-WINPATH"], 0), "quarantined")

    def test_ST25_terminal_never_regresses(self):
        t = tracker()
        run_to(t)
        t.on_result(A1, 1, "succeeded")
        for event in (lambda: t.on_result(A1, 1, "failed"), lambda: t.on_result(A1, 1, "succeeded", reasons=["TX-IPV4"]),
                      lambda: t.on_status(A1, 1, 50, "uploading"), lambda: t.on_cancel()):
            event()
            self.assertEqual(t.display(), ("succeeded", []))


class RetryAndLateResults(unittest.TestCase):
    def test_ST30_retry_flow_and_stale(self):
        t = tracker(max_attempts=2)
        run_to(t, states=("leased", "running", "failed"))
        self.assertEqual(t.on_result(A1, 1, "failed", retry_scheduled=True), "accepted")
        self.assertEqual(t.display()[0], "retry_pending")
        run_to(t, attempt=A2, no=2, states=("leased", "running"))
        self.assertEqual(t.display()[0], "running")
        self.assertEqual(t.on_status(A1, 1, 99, "running"), (False, "superseded_attempt"))
        self.assertEqual(t.on_status(A2, 2, 3, "succeeded"), (True, None))
        self.assertEqual(t.on_result(A2, 2, "succeeded"), "accepted")
        self.assertEqual(t.display()[0], "succeeded")

    def test_ST31_late_result_of_older_attempt_is_stale(self):
        t = tracker(max_attempts=2)
        run_to(t, states=("leased", "running"))
        run_to(t, attempt=A2, no=2, states=("leased",))         # worker crashed and restarted attempt 2
        self.assertEqual(t.on_result(A1, 1, "failed"), "stale")
        self.assertEqual(t.display()[0], "leased")

    def test_ST32_retry_scheduled_on_last_attempt_is_terminal(self):
        t = tracker(max_attempts=1)
        run_to(t, states=("leased", "failed"))
        self.assertEqual(t.on_result(A1, 1, "failed", retry_scheduled=True), "accepted")
        self.assertEqual(t.display()[0], "failed")


class CancelRaces(unittest.TestCase):
    def test_ST40_cancel_running_then_cancelled(self):
        t = tracker()
        run_to(t, states=("leased", "running"))
        self.assertEqual(t.on_cancel(), (True, None))
        self.assertEqual(t.display(), ("running", ["cancel_requested"]))
        t.on_status(A1, 1, 3, "cancelled")
        self.assertEqual(t.on_result(A1, 1, "cancelled"), "accepted")
        self.assertEqual((t.display(), t.cancel), (("cancelled", []), "honored"))

    def test_ST41_cancel_after_terminal_is_ignored(self):
        t = tracker()
        run_to(t)
        t.on_result(A1, 1, "succeeded")
        self.assertEqual(t.on_cancel(), (False, "job_terminal"))
        self.assertEqual((t.display(), t.cancel), (("succeeded", []), "ignored_terminal"))

    def test_ST42_cancel_too_late(self):
        t = tracker()
        run_to(t)                                                # worker already finished
        t.on_cancel()
        self.assertEqual(t.display(), ("delivering", ["cancel_requested"]))
        t.on_result(A1, 1, "succeeded")
        self.assertEqual((t.display(), t.cancel), (("succeeded", []), "too_late"))

    def test_ST43_cancel_while_queued(self):
        t = tracker()
        t.on_cancel()
        self.assertEqual(t.display(), ("queued", ["cancel_requested"]))
        self.assertEqual(t.on_result(A1, 1, "cancelled"), "accepted")   # not_started attempt record
        self.assertEqual((t.display()[0], t.cancel), ("cancelled", "honored"))

    def test_ST44_cancel_during_quarantine_resolution(self):
        t = tracker()
        run_to(t)
        t.on_cancel()
        t.on_result(A1, 1, "succeeded", reasons=["TX-TZ"])
        self.assertEqual((t.display(), t.cancel), (("quarantined", []), "too_late"))


    def test_ST45_cancel_during_retry_pending(self):
        t = tracker(max_attempts=2)
        run_to(t, states=("leased", "running", "failed"))
        t.on_result(A1, 1, "failed", retry_scheduled=True)
        self.assertEqual(t.display()[0], "retry_pending")
        t.on_cancel()
        self.assertEqual(t.display(), ("retry_pending", ["cancel_requested"]))
        # Worker cancels the remaining retries: next attempt_no, not_started, cancelled
        self.assertEqual(t.on_result(A2, 2, "cancelled"), "accepted")
        self.assertEqual((t.display(), t.cancel), (("cancelled", []), "honored"))


class WorkerHealthTests(unittest.TestCase):
    def test_ST50_lease_timeout_and_recovery(self):
        w = WorkerHealth(lease_seconds=90)
        self.assertEqual(w.state, "never_seen")
        w.on_heartbeat(3, 1, now_mono=0)
        self.assertEqual(w.tick(90), "alive")
        self.assertEqual(w.tick(91), "lost")
        self.assertEqual(w.on_heartbeat(3, 2, now_mono=200), (True, None))
        self.assertEqual(w.state, "alive")

    def test_ST51_replay_does_not_refresh(self):
        w = WorkerHealth(lease_seconds=90)
        w.on_heartbeat(3, 5, now_mono=0)
        self.assertEqual(w.on_heartbeat(3, 5, now_mono=80), (False, "stale_heartbeat"))
        self.assertEqual(w.on_heartbeat(3, 4, now_mono=85), (False, "stale_heartbeat"))
        self.assertEqual(w.tick(91), "lost")

    def test_ST52_restart_unconfirmed(self):
        w = WorkerHealth(lease_seconds=90, persisted=[3, 5])
        self.assertEqual(w.state, "unconfirmed")
        self.assertEqual(w.on_heartbeat(3, 5, now_mono=1), (False, "stale_heartbeat"))
        self.assertEqual(w.tick(10_000), "unconfirmed")
        self.assertEqual(w.on_heartbeat(3, 6, now_mono=2), (True, None))
        self.assertEqual(w.state, "alive")

    def test_ST53_new_epoch_resets_seq_old_epoch_ignored(self):
        w = WorkerHealth()
        w.on_heartbeat(3, 999, now_mono=0)
        self.assertEqual(w.on_heartbeat(4, 1, now_mono=1), (True, None))
        self.assertEqual(w.on_heartbeat(3, 1000, now_mono=2), (False, "stale_heartbeat"))
        self.assertEqual(w.persist(), [4, 1])

    def test_ST54_worker_lost_is_display_only(self):
        t = tracker()
        run_to(t, states=("leased", "running"))
        before = (dict(t.attempts), t.current_no)
        self.assertEqual(t.display(worker_health="lost"), ("running", ["worker_lost"]))
        self.assertEqual(t.display(worker_health="unconfirmed"), ("running", ["unconfirmed"]))
        self.assertEqual((dict(t.attempts), t.current_no), before)   # no new attempt, no reassignment
        self.assertEqual(t.on_status(A1, 1, 3, "running"), (True, None))  # reconnect continues same attempt

    def test_ST55_no_lost_overlay_on_terminal_or_queued(self):
        t = tracker()
        self.assertEqual(t.display(worker_health="lost"), ("queued", []))
        run_to(t)
        t.on_result(A1, 1, "succeeded")
        self.assertEqual(t.display(worker_health="lost"), ("succeeded", []))


if __name__ == "__main__":
    unittest.main()
