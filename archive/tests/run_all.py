"""Run the automated test groups and print a per-group summary.

    python3 -I -B tests/run_all.py

Groups: SCHEMA (needs jsonschema>=4.18, otherwise reported as SKIPPED),
SEMANTIC and STATE MACHINE (stdlib only) - the frozen baseline groups;
SIM-LIB and SIM-E2E - VPS-side simulated implementation (stdlib; the
jsonschema differential and ffmpeg sample tests skip when those are absent);
DEPLOY-STATIC - deployment scripts in dry-run/--render mode only (never --apply).  Real deployment acceptance
(docs/acceptance-v1.md) is NOT automated here.  Exit code 0 only if no group
has failures or errors; a skipped schema group is reported, not hidden.
"""
import pathlib
import sys
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

GROUPS = [("SCHEMA", "test_schemas"), ("SEMANTIC", "test_semantics"), ("STATE MACHINE", "test_state_machine"),
          ("SIM-LIB", "test_sim_lib"), ("SIM-E2E", "test_sim_e2e"), ("DEPLOY-STATIC", "test_deploy")]


def main():
    rc = 0
    rows = []
    for label, module in GROUPS:
        if label == "SIM-E2E" and sys.platform != "linux":
            def linux_only():
                raise unittest.SkipTest("linux-only: VPS gate filesystem semantics")
            suite = unittest.TestSuite([unittest.FunctionTestCase(linux_only)])
        else:
            suite = unittest.defaultTestLoader.loadTestsFromName(module)
        res = unittest.TextTestRunner(stream=sys.stderr, verbosity=2).run(suite)
        bad = len(res.failures) + len(res.errors)
        rc |= bool(bad)
        rows.append((label, res.testsRun, res.testsRun - bad - len(res.skipped), len(res.failures), len(res.errors), len(res.skipped)))
    print(f"platform: {sys.platform}  (SIM-LIB snapshot tests, SIM-E2E and DEPLOY-STATIC are linux-only; skipped elsewhere)")
    print(f"{'group':<14}{'run':>5}{'pass':>6}{'fail':>6}{'error':>7}{'skip':>6}")
    for r in rows:
        print(f"{r[0]:<14}{r[1]:>5}{r[2]:>6}{r[3]:>6}{r[4]:>7}{r[5]:>6}")
    print("DEPLOYMENT ACCEPTANCE: not automated (docs/acceptance-v1.md, separate phase)")
    return rc


if __name__ == "__main__":
    sys.exit(main())
