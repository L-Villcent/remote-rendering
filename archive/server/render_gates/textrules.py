"""Text rules v1 (protocol-v1 section 8).

scan_text(text) -> set of rule codes.  scan_json(doc, file_ref) -> [(rule, ref)]
where ref is built only from keys of a schema-validated document (so every key
is in the fixed vocabulary).  Matched values are never returned.

Purpose: catch accidental leakage shapes in logs, reports and string fields.
Not a proof that content is free of sensitive data (protocol 7.2 / 8).
"""
import ipaddress
import re

RULES_VERSION = 1

_OCT = r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)"
IPV4 = re.compile(r"(?<![\d.])(?<!\d\.)" + _OCT + r"(?:\." + _OCT + r"){3}(?!\.?\d)")
IPV6_CAND = re.compile(r"(?<![0-9A-Za-z_:.])[0-9A-Fa-f:.]*:[0-9A-Fa-f:.]*:[0-9A-Fa-f:.%]*(?![0-9A-Za-z_])")
MAC = re.compile(r"(?<![0-9A-Fa-f:-])[0-9A-Fa-f]{2}([:-])(?:[0-9A-Fa-f]{2}\1){4}[0-9A-Fa-f]{2}(?![0-9A-Fa-f:-])")
WINPATH = re.compile(r"(?i)(?:\b[a-z]:[\\/]+(?:users|documents and settings)[\\/])|(?:\\\\[a-z0-9._$-]+\\)")
POSIX_CAND = re.compile(r"(?<![\w.~-])/(?:home|Users|root|mnt/[a-z])(?![\w-])(?:/[^\s'\"`)\]>]*)?")
POSIX_ALLOW = ("/home/agent", "/home/code")   # VPS-side paths published in the design docs
ENV_LINE = re.compile(r"^\s*(?:export\s+)?[A-Z][A-Z0-9_]{0,63}=\S")
SECRET = re.compile(
    r"sk-ant-[A-Za-z0-9_-]{8,}|tskey-[A-Za-z0-9-]{8,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"\bAKIA[0-9A-Z]{16}\b|\bgh[pousr]_[A-Za-z0-9]{20,}|\bxox[abprs]-[A-Za-z0-9-]{10,}|"
    r"(?i:bearer)\s+[A-Za-z0-9._~+/-]{20,}=*")
TAILNET = re.compile(r"(?i)\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.ts\.net\b|\b(?:nodekey|discokey|mkey|nlpub):[0-9a-f]{16,}")
TZ_OFFSET = re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?\s?([+-])(\d{2}):?(\d{2})\b")
TZ_NAMED = re.compile(r"\b(?:UTC|GMT)\s?[+-]\s?(?:0?[1-9]|1[0-4])(?::?[0-5]\d)?\b|"
                      r"\b(?:Africa|America|Antarctica|Asia|Atlantic|Australia|Europe|Indian|Pacific)/[A-Z][A-Za-z_]+")


def _ipv6_hits(text):
    for m in IPV6_CAND.finditer(text):
        tok = m.group(0).strip(".")
        if tok.count(":") < 2:
            continue
        try:
            ipaddress.IPv6Address(tok.split("%", 1)[0].strip("[]"))
            return True
        except ValueError:
            continue
    return False


def scan_text(text: str) -> set:
    hits = set()
    if IPV4.search(text):
        hits.add("TX-IPV4")
    if _ipv6_hits(text):
        hits.add("TX-IPV6")
    if MAC.search(text):
        hits.add("TX-MAC")
    if WINPATH.search(text):
        hits.add("TX-WINPATH")
    for m in POSIX_CAND.finditer(text):
        path = m.group(0)
        if not any(path == a or path.startswith(a + "/") for a in POSIX_ALLOW):
            hits.add("TX-POSIXPATH")
            break
    run = 0
    for line in text.splitlines():
        run = run + 1 if ENV_LINE.match(line) else 0
        if run >= 3:
            hits.add("TX-ENVDUMP")
            break
    if SECRET.search(text):
        hits.add("TX-SECRET")
    if TAILNET.search(text):
        hits.add("TX-TAILNET")
    for m in TZ_OFFSET.finditer(text):
        if (m.group(2), m.group(3)) != ("00", "00"):
            hits.add("TX-TZ")
            break
    if TZ_NAMED.search(text):
        hits.add("TX-TZ")
    return hits


def scan_json(doc, file_ref: str):
    """Scan every string value (and key) of a schema-valid document."""
    out = []

    def walk(node, path):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, path + [k])
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, path + [str(i)])
        elif isinstance(node, str):
            for rule in sorted(scan_text(node)):
                ref = file_ref + ("#/" + "/".join(path) if path else "")
                out.append((rule, ref))

    walk(doc, [])
    return out
