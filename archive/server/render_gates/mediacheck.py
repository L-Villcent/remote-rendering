"""Bounded, read-only structure checks for PNG / MP4 (protocol-v1 section 7).

Detects deviation from a registered export profile; it is NOT evidence that a
file is free of sensitive content.  No decoding, re-encoding or cleaning.
The whole file is walked (metadata may sit anywhere, e.g. moov at the end,
tEXt after IDAT, bytes after IEND).  Hard limits on bytes read, structure
count, nesting depth and wall time raise structure_limit_exceeded.

check(path, profile_name, registry) -> Stats  (raises MediaError(code))
describe(path, fmt, limits)        -> raw structure summary (for fingerprinting)
fingerprint(summaries, fmt)        -> draft profile grammar for MX-01
"""
import os
import re
import struct
import time
from dataclasses import dataclass, field

KIB, MIB = 1024, 1024 * 1024
PNG_SIG = b"\x89PNG\r\n\x1a\n"

LIMITS = {
    "png": {"max_file": 8 * MIB, "max_chunks": 8192, "max_ancillary": 64 * KIB, "max_read": 128 * KIB, "max_seconds": 2.0},
    "mp4-preview": {"max_file": 20 * MIB, "max_top": 16, "max_boxes": 20000, "max_depth": 10,
                    "max_moov": 4 * MIB, "max_read": 4 * MIB + 200 * KIB, "max_seconds": 2.0},
    "mp4-final": {"max_file": 200 * MIB, "max_top": 16, "max_boxes": 100000, "max_depth": 10,
                  "max_moov": 8 * MIB, "max_read": 8 * MIB + 200 * KIB, "max_seconds": 5.0},
}
PROFILE_LIMITS = {"png-rgb8-v1": "png", "h264-preview-v1": "mp4-preview", "h264-final-v1": "mp4-final"}

CONTAINERS = {"moov", "trak", "mdia", "minf", "stbl", "dinf", "edts", "udta", "mvex"}
FULLBOX_CONTAINERS = {"meta": 4, "stsd": 8, "dref": 8}      # bytes before the child boxes
VISUAL_SAMPLE_ENTRIES = {"avc1", "avc3"}                     # 78 bytes before the child boxes


class MediaError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _mismatch():
    return MediaError("profile_mismatch")


def _limit():
    return MediaError("structure_limit_exceeded")


@dataclass
class Stats:
    bytes_read: int = 0
    structures: int = 0
    seconds: float = 0.0


@dataclass
class _Reader:
    f: object
    limits: dict
    stats: Stats
    deadline: float = 0.0

    def read(self, n):
        self.tick()
        if self.stats.bytes_read + n > self.limits["max_read"]:
            raise _limit()
        data = self.f.read(n)
        self.stats.bytes_read += len(data)
        if len(data) != n:
            raise _mismatch()
        return data

    def tick(self):
        if time.monotonic() > self.deadline:
            raise _limit()


# --------------------------------------------------------------------- PNG
def _png(r: _Reader, size: int):
    if r.read(8) != PNG_SIG:
        raise _mismatch()
    pos, types, ihdr = 8, [], None
    while True:
        if pos + 8 > size:
            raise _mismatch()                       # truncated
        r.f.seek(pos)
        length, ctype = struct.unpack(">I4s", r.read(8))
        if not re.fullmatch(rb"[A-Za-z]{4}", ctype) or length > 0x7FFFFFFF:
            raise _mismatch()
        end = pos + 12 + length                     # 8 header + payload + 4 CRC
        if end > size:
            raise _mismatch()
        r.stats.structures += 1
        if r.stats.structures > r.limits["max_chunks"]:
            raise _limit()
        name = ctype.decode("ascii")
        if name == "IHDR":
            if length != 13 or ihdr is not None:
                raise _mismatch()
            ihdr = struct.unpack(">IIBBBBB", r.read(13))
        elif name != "IDAT" and length > r.limits["max_ancillary"]:
            raise _limit()
        types.append(name)
        pos = end
        if name == "IEND":
            break
    if pos != size:
        raise _mismatch()                           # trailing bytes after IEND
    if ihdr is None:
        raise _mismatch()
    w, h, depth, color, comp, filt, interlace = ihdr
    return {"format": "png", "sequence": types, "width": w, "height": h, "bit_depth": depth,
            "color_type": color, "compression": comp, "filter": filt, "interlace": interlace}


# --------------------------------------------------------------------- MP4
def _box_header(buf, off, end):
    if end - off < 8:
        raise _mismatch()
    size, btype = struct.unpack_from(">I4s", buf, off)
    hdr = 8
    if size == 1:
        if end - off < 16:
            raise _mismatch()
        size = struct.unpack_from(">Q", buf, off + 8)[0]
        hdr = 16
    if size == 0 or size < hdr or off + size > end:
        raise _mismatch()
    return size, btype.decode("latin-1"), hdr


def _mp4_tree(r: _Reader, buf, off, end, path, depth, out):
    while off < end:
        r.tick()
        size, btype, hdr = _box_header(buf, off, end)
        r.stats.structures += 1
        if r.stats.structures > r.limits["max_boxes"]:
            raise _limit()
        if depth > r.limits["max_depth"]:
            raise _limit()
        p = f"{path}/{btype}"
        out["paths"][p] = out["paths"].get(p, 0) + 1
        payload = off + hdr
        if btype == "uuid":
            raise _mismatch()
        if btype in ("free", "skip") and size != hdr:
            raise _mismatch()
        if btype in CONTAINERS:
            _mp4_tree(r, buf, payload, off + size, p, depth + 1, out)
        elif btype in FULLBOX_CONTAINERS:
            _mp4_tree(r, buf, payload + FULLBOX_CONTAINERS[btype], off + size, p, depth + 1, out)
        elif btype in VISUAL_SAMPLE_ENTRIES:
            if size - hdr < 78:
                raise _mismatch()
            w, h = struct.unpack_from(">HH", buf, payload + 24)
            n = buf[payload + 42]
            out["compressors"].add(bytes(buf[payload + 43: payload + 43 + min(n, 31)]).decode("latin-1"))
            out["dims"].append((w, h))
            _mp4_tree(r, buf, payload + 78, off + size, p, depth + 1, out)
        elif btype == "hdlr":
            if size - hdr < 24:
                raise _mismatch()
            handler = bytes(buf[payload + 8: payload + 12]).decode("latin-1")
            name = bytes(buf[payload + 24: off + size]).split(b"\0", 1)[0].decode("latin-1")
            out["handlers"].add((handler, name))
        elif btype == "ilst":
            o = payload
            while o < off + size:
                isz, itype, _ = _box_header(buf, o, off + size)
                out["meta_keys"].add(itype)
                o += isz
        elif btype == "mvhd":
            version = buf[payload]
            if version == 1:
                ts, dur = struct.unpack_from(">IQ", buf, payload + 20)
            else:
                ts, dur = struct.unpack_from(">II", buf, payload + 12)
            out["duration_s"] = dur / ts if ts else None
        elif btype == "tkhd":
            out["tracks"] += 1
        off += size


def _mp4(r: _Reader, size: int):
    pos, top = 0, []
    out = {"format": "mp4", "top": top, "paths": {}, "handlers": set(), "meta_keys": set(),
           "compressors": set(), "dims": [], "tracks": 0, "duration_s": None, "brand": None}
    moov_seen = False
    while pos < size:
        r.f.seek(pos)
        if size - pos < 8:
            raise _mismatch()                       # trailing bytes after the last box
        head = r.read(8)
        bsize, btype = struct.unpack(">I4s", head)
        hdr = 8
        if bsize == 1:
            bsize = struct.unpack(">Q", r.read(8))[0]
            hdr = 16
        if bsize == 0 or bsize < hdr or pos + bsize > size:
            raise _mismatch()
        name = btype.decode("latin-1")
        r.stats.structures += 1
        if len(top) >= r.limits["max_top"]:
            raise _limit()
        top.append(name)
        if name == "moov":
            if moov_seen:
                raise _mismatch()
            moov_seen = True
            if bsize - hdr > r.limits["max_moov"]:
                raise _limit()
            buf = memoryview(r.read(bsize - hdr))
            _mp4_tree(r, buf, 0, len(buf), "moov", 1, out)
        elif name == "ftyp":
            if bsize - hdr < 8 or bsize - hdr > 256:
                raise _mismatch()
            out["brand"] = r.read(4).decode("latin-1")
        elif name in ("free", "skip"):
            if bsize != hdr:
                raise _mismatch()
        elif name == "uuid":
            raise _mismatch()
        pos += bsize
    return out


# --------------------------------------------------------------------- API
def describe(path, limits_key):
    limits = LIMITS[limits_key]
    stats = Stats()
    t0 = time.monotonic()
    with open(path, "rb") as f:
        size = os.fstat(f.fileno()).st_size
        if size > limits["max_file"]:
            raise _limit()
        r = _Reader(f, limits, stats, t0 + limits["max_seconds"])
        summary = _png(r, size) if limits_key == "png" else _mp4(r, size)
    stats.seconds = time.monotonic() - t0
    return summary, stats


def check(path, profile_name, registry):
    """Raise MediaError unless the file matches the registered profile grammar."""
    prof = registry["profiles"].get(profile_name)
    if prof is None:
        raise _mismatch()
    s, stats = describe(path, PROFILE_LIMITS[profile_name])
    if s["format"] == "png":
        if not re.fullmatch(prof["sequence"], " ".join(s["sequence"]) + " "):
            raise _mismatch()
        if (s["bit_depth"] not in prof["bit_depths"] or s["color_type"] not in prof["color_types"]
                or s["compression"] or s["filter"] or s["interlace"]
                or s["width"] > prof["max_width"] or s["height"] > prof["max_height"]):
            raise _mismatch()
    else:
        if not any(re.fullmatch(rx, " ".join(s["top"]) + " ") for rx in prof["top_sequences"]):
            raise _mismatch()
        if not set(s["paths"]) <= set(prof["paths"]):
            raise _mismatch()
        if s["tracks"] != prof["tracks"] or s["brand"] not in prof["brands"]:
            raise _mismatch()
        if not {f"{h}:{n}" for h, n in s["handlers"]} <= set(prof["handlers"]):
            raise _mismatch()
        if not s["meta_keys"] <= set(prof["meta_keys"]) or not s["compressors"] <= set(prof["compressors"]):
            raise _mismatch()
        if any(w > prof["max_width"] or h > prof["max_height"] for w, h in s["dims"]):
            raise _mismatch()
        if s["duration_s"] is None or s["duration_s"] > prof["max_duration_s"]:
            raise _mismatch()
    return stats


def fingerprint(summaries, fmt, caps):
    """Draft grammar (rules + repetition ranges) from a sample matrix (MX-01).

    The draft must be reviewed and approved by the user before registration.
    """
    if fmt == "png":
        pre, post = set(), set()
        for s in summaries:
            seq = s["sequence"]
            first, last = seq.index("IDAT"), len(seq) - 1 - seq[::-1].index("IDAT")
            pre.update(seq[1:first])
            post.update(seq[last + 1:-1])
            if set(seq[first:last + 1]) != {"IDAT"}:
                raise ValueError("non-contiguous IDAT in a sample")

        def alt(names, n):
            return f"(?:(?:{'|'.join(sorted(names))}) ){{0,{n}}}" if names else ""

        return {"format": "png", "sequence": f"^IHDR {alt(pre, len(pre))}(?:IDAT ){{1,8192}}{alt(post, len(post))}IEND $",
                "bit_depths": sorted({s["bit_depth"] for s in summaries}),
                "color_types": sorted({s["color_type"] for s in summaries}), **caps}
    return {"format": "mp4",
            "top_sequences": sorted({"^" + "".join(re.escape(t) + " " for t in s["top"]) + "$" for s in summaries}),
            "paths": sorted(set().union(*(s["paths"] for s in summaries))),
            "handlers": sorted({f"{h}:{n}" for s in summaries for h, n in s["handlers"]}),
            "meta_keys": sorted(set().union(*(s["meta_keys"] for s in summaries))),
            "compressors": sorted(set().union(*(s["compressors"] for s in summaries))),
            "brands": sorted({s["brand"] for s in summaries}),
            "tracks": 1, **caps}
