"""Group 1 - SCHEMA tests (needs jsonschema >= 4.18; skipped with a clear reason otherwise).

Checks: every example validates against its $defs; schemas are valid 2020-12;
the ref vocabulary is in sync; mutated negatives are rejected.  Schema
validity does NOT replace the semantic and state-machine groups.
"""
import json
import unittest

from _common import EXAMPLES, ROOT, SCHEMA_OF, def_name, doc_ip, load_examples, mutate

try:
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
    HAVE_JSONSCHEMA = True
except ImportError:  # pragma: no cover
    HAVE_JSONSCHEMA = False

SCHEMAS = {n: json.loads((ROOT / "schemas" / f"{n}-v1.schema.json").read_text(encoding="utf-8")) for n in ("job", "status", "result")}
EX = load_examples()


def validator(defname):
    stem = SCHEMA_OF[defname]
    s = SCHEMAS[stem]
    schema = {"$schema": s["$schema"], "$ref": f"{s['$id']}#/$defs/{defname}"}
    if defname == "job":
        schema = s
    reg = Registry().with_resources([(x["$id"], Resource.from_contents(x)) for x in SCHEMAS.values()])
    return Draft202012Validator(schema, registry=reg, format_checker=Draft202012Validator.FORMAT_CHECKER)


R = EX["result"]
NEGATIVE = [
    # submission / job
    ("submission", "entrypoint traversal", mutate(EX["submission"], ["entrypoint"], "scene/../secret")),
    ("submission", "absolute entrypoint", mutate(EX["submission"], ["entrypoint"], "/etc/passwd")),
    ("submission", "network not offline", mutate(EX["submission"], ["network_policy"], "vps-egress")),
    ("submission", "unknown adapter", mutate(EX["submission"], ["adapter", "id"], "shell")),
    ("submission", "extra top-level field", mutate(EX["submission"], ["command"], "rm -rf /")),
    ("submission", "server field in submission", mutate(EX["submission"], ["job_id"], EX["job"]["job_id"])),
    ("submission", "float param", mutate(EX["submission"], ["params", "speed"], 1.5)),
    ("submission", "control char in param", mutate(EX["submission"], ["params", "palette"], "a\nb")),
    ("submission", "old field max_output_mb", mutate(EX["submission"], ["limits", "max_output_mb"], 10)),
    ("job", "job missing bundle", mutate(EX["job"], ["bundle"], delete=True)),
    ("job", "job without identity", mutate(EX["job"], ["identity_sha256"], delete=True)),
    ("job", "job with old submission_sha256", mutate(EX["job"], ["submission_sha256"], EX["job"]["identity_sha256"])),
    ("job", "job not normalized (no final_delivery)", mutate(EX["job"], ["export", "final_delivery"], delete=True)),
    ("job", "created_at with offset", mutate(EX["job"], ["created_at"], "2026-10-08T17:00:00+08:00")),
    ("job", "created_at with fraction", mutate(EX["job"], ["created_at"], "2026-10-08T09:00:00.123Z")),
    # control messages and receipts
    ("cancel_request", "cancel without request_id", mutate(EX["cancel_request"], ["request_id"], delete=True)),
    ("cancel_submission", "cancel with reason text", mutate(EX["cancel_submission"], ["reason"], "because")),
    ("submission_receipt", "published without job_id", mutate(EX["submission_receipt.published"], ["job_id"], None)),
    ("submission_receipt", "rejected without reasons", mutate(EX["submission_receipt.rejected"], ["reasons"], [])),
    ("submission_receipt", "conflict with job_id", mutate(EX["submission_receipt.conflict"], ["job_id"], EX["job"]["job_id"])),
    ("cancel_receipt", "ignored with request_id", mutate(EX["cancel_receipt.ignored_terminal"], ["request_id"], EX["cancel_request"]["request_id"])),
    # heartbeat / status / views
    ("heartbeat", "heartbeat with wall clock", mutate(EX["heartbeat"], ["sent_at"], "2026-10-08T09:00:00Z")),
    ("heartbeat", "heartbeat free-text field", mutate(EX["heartbeat"], ["note"], "via " + doc_ip())),
    ("heartbeat", "heartbeat missing active ids", mutate(EX["heartbeat"], ["active_job_id"], delete=True)),
    ("status", "status free-text message", mutate(EX["status"], ["message"], "connect failed")),
    ("status", "status seq zero", mutate(EX["status"], ["seq"], 0)),
    ("job_view", "terminal view with overlays", mutate(EX["job_view.quarantined"], ["overlays"], ["worker_lost"])),
    ("job_view", "queued view with attempt", mutate(EX["job_view.running"], ["display_state"], "queued")),
    ("netcheck_view", "netcheck with endpoint field", mutate(EX["netcheck_view"], ["endpoint"], doc_ip())),
    ("netcheck_view", "netcheck free error text", mutate(EX["netcheck_view"], ["error_code"], "dial failed")),
    # result (R2)
    ("result", "succeeded with unknown browser", mutate(R, ["toolchain", "browser"], None)),
    ("result", "succeeded with unknown fallback", mutate(R, ["device", "software_fallback"], None)),
    ("result", "succeeded on software fallback", mutate(R, ["device", "software_fallback"], True)),
    ("result", "succeeded with nonzero exit", mutate(R, ["exit", "code"], 1)),
    ("result", "succeeded not at upload phase", mutate(R, ["phase_reached"], "render")),
    ("result", "succeeded without toolchain_id", mutate(R, ["toolchain", "toolchain_id"], None)),
    ("result", "succeeded with retry_scheduled", mutate(R, ["retry_scheduled"], True)),
    ("result", "failed without error_class", mutate(EX["result.browser_missing"], ["error_class"], None)),
    ("result", "failed with error cancelled", mutate(EX["result.browser_missing"], ["error_class"], "cancelled")),
    ("result", "cancelled with other error", mutate(EX["result.cancelled_before_start"], ["error_class"], "timeout")),
    ("result", "exited without code", mutate(R, ["exit", "code"], None)),
    ("result", "not_started with code", mutate(EX["result.browser_missing"], ["exit", "code"], 0)),
    ("result", "exit code above DWORD", mutate(EX["result.failed_retry_scheduled"], ["exit", "code"], 4294967296)),
    ("result", "negative exit code", mutate(EX["result.failed_retry_scheduled"], ["exit", "code"], -1)),
    ("result", "device field omitted instead of null", mutate(EX["result.browser_missing"], ["device", "gpu_model"], delete=True)),
    ("result", "gpu_model carrying dotted address", mutate(R, ["device", "gpu_model"], "GPU " + doc_ip())),
    ("result", "absolute local path field", mutate(R, ["artifacts", 2, "path"], "C:/Users/example/x.png")),
    ("result", "free-form artifact_id", mutate(R, ["artifacts", 1, "artifact_id"], "my desktop shot")),
    ("result", "uploaded artifact without hash", mutate(R, ["artifacts", 0, "sha256"], delete=True)),
    ("result", "png without export profile", mutate(R, ["artifacts", 1, "export_profile"], delete=True)),
    ("result", "browser full version string", mutate(R, ["toolchain", "browser", "major"], "140.0.7339.80")),
    ("result", "over_upload_cap false", mutate(R, ["artifacts", 0, "over_upload_cap"], False)),
    ("result", "free-form toolchain id", mutate(R, ["toolchain", "toolchain_id"], "Chrome Canary on my PC")),
    ("result", "expected null while job known", mutate(EX["result.download_failed"], ["frames", "expected"], None)),
    ("result", "expected null on success", mutate(R, ["frames", "expected"], None)),
    ("result", "job unavailable but expected guessed", mutate(EX["result.job_unreadable"], ["frames", "expected"], 3)),
    ("result", "job unavailable but frames rendered", mutate(EX["result.job_unreadable"], ["frames", "rendered"], 1)),
    # ingest record (= ack)
    ("ingest_record", "reason echoing value", mutate(EX["ingest_record.quarantined"], ["reasons", 0, "value"], doc_ip())),
    ("ingest_record", "ref with free text", mutate(EX["ingest_record.quarantined"], ["reasons", 0, "ref"], "log line " + doc_ip())),
    ("ingest_record", "ref pointer with unknown key", mutate(EX["ingest_record.quarantined"], ["reasons", 0, "ref"], "result.json#/secretword")),
    ("ingest_record", "unknown reason code", mutate(EX["ingest_record.quarantined"], ["reasons", 0, "rule"], "looks bad")),
    ("ingest_record", "accepted with reasons", mutate(EX["ingest_record.quarantined"], ["verdict"], "accepted")),
    ("ingest_record", "rejected without retry_allowed", mutate(EX["ingest_record.rejected"], ["retry_allowed"], False)),
    ("ingest_record", "quarantined with retry_allowed", mutate(EX["ingest_record.quarantined"], ["retry_allowed"], True)),
]


@unittest.skipUnless(HAVE_JSONSCHEMA, "jsonschema>=4.18 not installed: schema group not executed")
class SchemaTests(unittest.TestCase):
    def test_SC01_examples_valid(self):
        self.assertGreaterEqual(len(EX), 30)
        for stem, doc in EX.items():
            with self.subTest(example=stem):
                errs = list(validator(def_name(stem)).iter_errors(doc))
                self.assertEqual(errs, [], errs[0].message[:120] if errs else "")

    def test_SC02_meta_schema(self):
        for name, s in SCHEMAS.items():
            with self.subTest(schema=name):
                Draft202012Validator.check_schema(s)

    def test_SC03_ref_vocabulary_in_sync(self):
        from render_protocol.vocab import property_names, ref_pattern
        self.assertEqual(SCHEMAS["result"]["$defs"]["ref"]["pattern"], ref_pattern(property_names(SCHEMAS.values())))

    def test_SC04_negatives_rejected(self):
        for defname, label, doc in NEGATIVE:
            with self.subTest(case=label):
                self.assertFalse(validator(defname).is_valid(doc), label)

    def test_SC05_vectors_file_not_an_example(self):
        self.assertTrue((EXAMPLES / "vectors" / "identity-vectors.json").exists())

    def test_SC90_documented_limit_gpu_model(self):
        # Documented limitation, not a guarantee: spaced digits pass the charset limit.
        doc = mutate(R, ["device", "gpu_model"], "GPU " + " ".join(doc_ip().split(".")))
        self.assertTrue(validator("result").is_valid(doc))


if __name__ == "__main__":
    unittest.main()
