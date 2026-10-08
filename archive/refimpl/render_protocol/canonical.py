"""Strict JSON parsing, canonical JSON and job identity (protocol-v1 section 6.2, R1).

Canonical form is the RFC 8785 (JCS) serialization restricted to the value
space protocol v1 allows in signed/identity documents:
  * objects with string keys, sorted by UTF-16 code units (JCS order);
  * arrays, strings, booleans, null;
  * integers in [-(2**53 - 1), 2**53 - 1].  Floats are not allowed anywhere,
    so no language-specific float formatting can change the bytes;
  * no insignificant whitespace; strings are emitted as UTF-8, escaping only
    '"', '\\' and U+0000..U+001F (short forms where JCS defines them).
Every implementation (VPS Python, Worker in any language) must produce the
same bytes; examples/protocol/identity-vectors.json holds test vectors.
"""
import hashlib
import json

IDENTITY_DOMAIN = "render-protocol/v1/job-identity"
MAX_SAFE_INT = 2**53 - 1

_SHORT = {0x08: "\\b", 0x09: "\\t", 0x0A: "\\n", 0x0C: "\\f", 0x0D: "\\r"}


class ProtocolJSONError(ValueError):
    """Input is not acceptable protocol JSON. Message never echoes input values."""


def parse_strict(data: bytes):
    """Parse protocol JSON: UTF-8 without BOM, no duplicate keys, no floats/NaN."""
    if data.startswith(b"\xef\xbb\xbf"):
        raise ProtocolJSONError("bom")
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise ProtocolJSONError("not_utf8") from None

    def pairs(items):
        obj = {}
        for k, v in items:
            if k in obj:
                raise ProtocolJSONError("duplicate_key")
            obj[k] = v
        return obj

    def no_float(_):
        raise ProtocolJSONError("float_not_allowed")

    def no_const(_):
        raise ProtocolJSONError("non_finite_number")

    try:
        return json.loads(text, object_pairs_hook=pairs, parse_float=no_float, parse_constant=no_const)
    except ProtocolJSONError:
        raise
    except ValueError:
        raise ProtocolJSONError("malformed") from None


def _enc_str(s: str) -> str:
    out = ['"']
    for ch in s:
        o = ord(ch)
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif o < 0x20:
            out.append(_SHORT.get(o, "\\u%04x" % o))
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _enc(v) -> str:
    if v is None:
        return "null"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, int):
        if abs(v) > MAX_SAFE_INT:
            raise ProtocolJSONError("integer_out_of_range")
        return str(v)
    if isinstance(v, float):
        raise ProtocolJSONError("float_not_allowed")
    if isinstance(v, str):
        return _enc_str(v)
    if isinstance(v, list):
        return "[" + ",".join(_enc(x) for x in v) + "]"
    if isinstance(v, dict):
        if not all(isinstance(k, str) for k in v):
            raise ProtocolJSONError("non_string_key")
        keys = sorted(v, key=lambda k: k.encode("utf-16-be"))
        return "{" + ",".join(_enc_str(k) + ":" + _enc(v[k]) for k in keys) + "}"
    raise ProtocolJSONError("unsupported_type")


def canonical_bytes(value) -> bytes:
    try:
        return _enc(value).encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        raise ProtocolJSONError("lone_surrogate") from None


def normalize_submission(sub: dict) -> dict:
    """Apply v1 defaults and set-normalization before identity and publishing.

    Omitted optional fields and their explicit defaults must yield the same
    identity; set-valued arrays are sorted.  The returned dict is what
    render-publish writes into job.json.
    """
    s = json.loads(json.dumps(sub))  # deep copy (input has no floats by contract)
    s.setdefault("parent_job_id", None)
    s.setdefault("params", {})
    s["export"].setdefault("final_delivery", "retained_local")
    s["export"]["profiles"] = sorted(s["export"]["profiles"])
    s["limits"].setdefault("max_upload_mb", 200)
    s["artifacts"] = sorted(s["artifacts"])
    if "keyframes" in s["render"]:
        s["render"]["keyframes"] = sorted(s["render"]["keyframes"])
    return s


def job_identity_sha256(submission: dict, bundle_sha256: str) -> str:
    """Identity = sha256(canonical({domain, bundle_sha256, submission - idempotency_key})).

    * idempotency_key is excluded: it names the request, the digest names the content.
    * bundle_sha256 is over the exact tar bytes render-publish snapshotted
      (byte-level identity; reproducible packing is a renderctl convenience only).
    """
    body = {k: v for k, v in normalize_submission(submission).items() if k != "idempotency_key"}
    doc = {"domain": IDENTITY_DOMAIN, "bundle_sha256": bundle_sha256, "submission": body}
    return hashlib.sha256(canonical_bytes(doc)).hexdigest()


def decide_publish(index: dict, idempotency_key: str, identity: str):
    """index: idempotency_key -> (identity, job_id).  Returns (verdict, job_id|None).

    verdict: 'new' (caller allocates a job_id), 'duplicate' (same content, original
    job_id), 'conflict' (same key, different submission or bundle).
    """
    prev = index.get(idempotency_key)
    if prev is None:
        return "new", None
    prev_identity, prev_job = prev
    if prev_identity == identity:
        return "duplicate", prev_job
    return "conflict", None
