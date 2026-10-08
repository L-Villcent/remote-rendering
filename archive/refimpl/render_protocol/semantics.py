"""Cross-field rules that JSON Schema cannot express (protocol-v1 section 10, R4).

Every check returns a sorted list of rule codes; [] means OK.  Inputs are
assumed to be schema-valid already.  Codes are listed in protocol-v1 10.x
together with the side responsible for enforcing them.
"""

KIB, MIB = 1024, 1024 * 1024

STAGE_PROFILES = {
    "keyframes": ({"png-rgb8-v1"}, {"png-rgb8-v1"}),
    "preview": ({"h264-preview-v1"}, {"png-rgb8-v1", "h264-preview-v1"}),
    "final": ({"h264-final-v1"}, {"png-rgb8-v1", "h264-preview-v1", "h264-final-v1"}),
}  # stage -> (required, allowed)
STAGE_ARTIFACTS = {
    "keyframes": ({"log", "keyframe"}, {"log", "keyframe", "contact"}),
    "preview": ({"log", "preview"}, {"log", "keyframe", "contact", "preview"}),
    "final": ({"log", "final"}, {"log", "keyframe", "contact", "preview", "final"}),
}
KIND_MEDIA = {  # kind -> (media_type, export_profile)
    "log": ("text/plain", None),
    "keyframe": ("image/png", "png-rgb8-v1"),
    "contact": ("image/png", "png-rgb8-v1"),
    "preview": ("video/mp4", "h264-preview-v1"),
    "final": ("video/mp4", "h264-final-v1"),
}
UPLOAD_CAP = {"log": 256 * KIB, "keyframe": 8 * MIB, "contact": 8 * MIB, "preview": 20 * MIB, "final": 200 * MIB}
PREVIEW_MAX_SECONDS = 20
FINAL_UPLOAD_MAX_SECONDS = 600
RETRYABLE_ERRORS = {"adapter_error", "worker_lost"}


def expected_frames(job) -> int:
    r = job["render"]
    if job["stage"] == "keyframes":
        return len(r.get("keyframes", []))
    return r["frame_end_exclusive"] - r["frame_start"]


def check_submission(sub) -> list:
    bad = set()
    r, stage = sub["render"], sub["stage"]
    start, end, fps = r["frame_start"], r["frame_end_exclusive"], r["fps"]
    profiles, arts = set(sub["export"]["profiles"]), set(sub["artifacts"])
    kfs = r.get("keyframes")
    if end <= start:
        bad.add("XF-J01")
    if stage == "keyframes" and not kfs:
        bad.add("XF-J02")
    if kfs and any(not (start <= k < end) for k in kfs):
        bad.add("XF-J03")
    req, allowed = STAGE_PROFILES[stage]
    if not (req <= profiles <= allowed):
        bad.add("XF-J04")
    req, allowed = STAGE_ARTIFACTS[stage]
    if not (req <= arts <= allowed):
        bad.add("XF-J05")
    needed = {KIND_MEDIA[a][1] for a in arts} - {None}
    if needed != profiles:
        bad.add("XF-J06")          # every artifact has its profile, no unused profile
    if sub["export"].get("final_delivery") == "upload" and stage != "final":
        bad.add("XF-J07")          # normalization writes the retained_local default everywhere
    if "preview" in arts and (end - start) > PREVIEW_MAX_SECONDS * fps:
        bad.add("XF-J08")
    if sub["export"].get("final_delivery") == "upload" and (end - start) > FINAL_UPLOAD_MAX_SECONDS * fps:
        bad.add("XF-J09")
    if "keyframe" in arts and not kfs:
        bad.add("XF-J10")
    return sorted(bad)


def _job_unavailable_branch(res) -> bool:
    """The only branch where job-derived fields are unknown: job.json itself was unusable.

    bundle_sha256, adapter and frames.expected are then null together; the
    Worker never guesses them.  Everywhere else they are compared strictly.
    """
    return (res["outcome"] == "failed" and res["error_class"] == "validation_error"
            and res["phase_reached"] == "validate" and res["frames"]["rendered"] == 0
            and res["bundle_sha256"] is None and res["adapter"] is None and res["frames"]["expected"] is None)


def check_result(job, res) -> list:
    bad = set()
    unavailable = _job_unavailable_branch(res)
    if res["job_id"] != job["job_id"]:
        bad.add("XF-R01")
    if res["bundle_sha256"] is None:
        if not unavailable:
            bad.add("XF-R02")
    elif res["bundle_sha256"] != job["bundle"]["sha256"]:
        bad.add("XF-R02")
    if res["adapter"] is None:
        if not unavailable:
            bad.add("XF-R03")
    elif res["adapter"] != job["adapter"]:
        bad.add("XF-R03")
    if res["attempt_no"] > job["limits"]["max_attempts"]:
        bad.add("XF-R04")

    arts = res["artifacts"]
    ids = [a["artifact_id"] for a in arts]
    if len(ids) != len(set(ids)):
        bad.add("XF-R05")
    job_kfs = set(job["render"].get("keyframes", []))
    uploaded_total = 0
    final_delivery = job["export"].get("final_delivery", "retained_local")
    for a in arts:
        kind = a["kind"]
        if a["artifact_id"].split("-", 1)[0] != kind:
            bad.add("XF-R06")
        media, profile = KIND_MEDIA[kind]
        if a["media_type"] != media or a.get("export_profile") != profile:
            bad.add("XF-R07")
        if profile is not None and profile not in job["export"]["profiles"]:
            bad.add("XF-R08")
        if kind not in job["artifacts"]:
            bad.add("XF-R09")
        if kind == "keyframe":
            fi = a.get("frame_index")
            if fi is None or fi not in job_kfs or a["artifact_id"] != f"keyframe-{fi}":
                bad.add("XF-R10")
        elif "frame_index" in a:
            bad.add("XF-R10")
        if a["delivery"] == "uploaded":
            uploaded_total += a["size_bytes"]
            if a["size_bytes"] > UPLOAD_CAP[kind]:
                bad.add("XF-R11")
        if kind == "final":
            if final_delivery == "retained_local" and (a["delivery"] != "retained_local" or a.get("over_upload_cap")):
                bad.add("XF-R12")
            if final_delivery == "upload" and a["delivery"] == "retained_local" and not a.get("over_upload_cap"):
                bad.add("XF-R12")
        elif a["delivery"] != "uploaded" or "over_upload_cap" in a:
            bad.add("XF-R12")
    if uploaded_total > job["limits"].get("max_upload_mb", 200) * MIB:
        bad.add("XF-R11")

    fr = res["frames"]
    if fr["expected"] is None:
        if not unavailable:
            bad.add("XF-R15")
    else:
        if fr["expected"] != expected_frames(job):
            bad.add("XF-R15")
        if fr["rendered"] > fr["expected"]:
            bad.add("XF-R16")
    outcome, err = res["outcome"], res["error_class"]
    if outcome == "succeeded":
        if fr["rendered"] != fr["expected"]:
            bad.add("XF-R13")
        kinds = {a["kind"] for a in arts}
        if not set(job["artifacts"]) <= kinds:
            bad.add("XF-R13")
        if "keyframe" in job["artifacts"] and {a.get("frame_index") for a in arts if a["kind"] == "keyframe"} != job_kfs:
            bad.add("XF-R13")
        if kinds & {"preview", "final"} and res["toolchain"]["encoder"] is None:
            bad.add("XF-R14")
    if outcome == "failed" and (err is None or err == "cancelled"):
        bad.add("XF-R17")
    if outcome == "cancelled" and err != "cancelled":
        bad.add("XF-R17")
    if res["exit"]["kind"] == "not_started" and (res["phase_reached"] not in ("validate", "prepare") or fr["rendered"] != 0):
        bad.add("XF-R18")
    if res.get("retry_scheduled"):
        if outcome != "failed" or err not in RETRYABLE_ERRORS or res["attempt_no"] >= job["limits"]["max_attempts"]:
            bad.add("XF-R19")
    t = res["timings_ms"]
    if sum(t.get(k, 0) for k in ("prepare", "render", "encode")) > t["total"]:
        bad.add("XF-R20")
    d = res["device"]
    if d["gpu_query"] != "available" and (d.get("gpu_busy_observed") is not None or d.get("vram_peak_mb") is not None):
        bad.add("XF-R21")
    if not any(a["kind"] == "log" and a["delivery"] == "uploaded" for a in arts):
        bad.add("XF-R23")
    return sorted(bad)


def check_status(job, st) -> list:
    bad = set()
    if st["job_id"] != job["job_id"]:
        bad.add("XF-S01")
    if st["attempt_no"] > job["limits"]["max_attempts"]:
        bad.add("XF-S02")
    p = st.get("progress")
    if p is not None:
        if p["frames_done"] > p["frames_total"]:
            bad.add("XF-S03")
        if p["frames_total"] != expected_frames(job):
            bad.add("XF-S04")
    return sorted(bad)


def check_heartbeat(hb) -> list:
    bad = set()
    job, att = hb.get("active_job_id"), hb.get("active_attempt_id")
    if (job is None) != (att is None):
        bad.add("XF-H01")
    if hb["state"] == "busy" and job is None:
        bad.add("XF-H01")
    if hb["state"] == "idle" and job is not None:
        bad.add("XF-H01")
    ids = [a["id"] for a in hb["capabilities"]["adapters"]]
    if len(ids) != len(set(ids)):
        bad.add("XF-H02")
    return sorted(bad)
