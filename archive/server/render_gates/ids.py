"""Identifiers: ULIDs from the VPS clock (job_id, request_id); random ids (attempt_id)."""
import secrets
import time

CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _b32(value: int, length: int) -> str:
    out = []
    for _ in range(length):
        out.append(CROCKFORD[value & 31])
        value >>= 5
    return "".join(reversed(out))


def ulid(now_ms=None) -> str:
    ms = int(time.time() * 1000) if now_ms is None else now_ms
    return _b32(ms & ((1 << 48) - 1), 10) + _b32(secrets.randbits(80), 16)


def job_id(now_ms=None) -> str:
    return "j_" + ulid(now_ms)


def request_id(now_ms=None) -> str:
    return "req_" + ulid(now_ms)


def random_attempt_id() -> str:
    """130 random bits, no time component (protocol 3)."""
    return "a_" + _b32(secrets.randbits(130), 26)


def utc_now(now=None) -> str:
    t = time.gmtime(time.time() if now is None else now)
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", t)
