"""Synthetic PNG / MP4 byte builders for structure-check tests (no decoder needed)."""
import struct
import zlib


def chunk(ctype: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + ctype + data + struct.pack(">I", zlib.crc32(ctype + data) & 0xFFFFFFFF)


def png(w=8, h=8, color_type=2, depth=8, before_idat=(), after_idat=(), trailing=b"", idat_parts=1, interlace=0):
    bpp = {2: 3, 6: 4}.get(color_type, 3)
    raw = b"".join(b"\0" + bytes([(x * 31 + y * 7) & 255 for x in range(w * bpp)]) for y in range(h))
    comp = zlib.compress(raw)
    step = max(1, -(-len(comp) // idat_parts))
    idats = b"".join(chunk(b"IDAT", comp[i:i + step]) for i in range(0, len(comp), step))
    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, depth, color_type, 0, 0, interlace))
    extra = lambda items: b"".join(chunk(t, d) for t, d in items)  # noqa: E731
    return b"\x89PNG\r\n\x1a\n" + ihdr + extra(before_idat) + idats + extra(after_idat) + chunk(b"IEND", b"") + trailing


def box(t: bytes, payload=b"", large=False) -> bytes:
    if large:
        return struct.pack(">I4sQ", 1, t, 16 + len(payload)) + payload
    return struct.pack(">I4s", 8 + len(payload), t) + payload


def fullbox(t: bytes, payload=b"", version=0, flags=0) -> bytes:
    return box(t, struct.pack(">I", (version << 24) | flags) + payload)


def mp4(duration_s=1, w=64, h=64, moov_last=False, hdlr_name=b"VideoHandler", handler=b"vide", tracks=1,
        udta=b"", extra_moov=b"", extra_top=b"", free_payload=b"", trailing=b"", compressor=b"", meta_in_trak=b""):
    mvhd = fullbox(b"mvhd", struct.pack(">IIII", 0, 0, 1000, int(duration_s * 1000)) + b"\0" * 80)
    hdlr = fullbox(b"hdlr", b"\0" * 4 + handler + b"\0" * 12 + hdlr_name + b"\0")
    dref = fullbox(b"dref", struct.pack(">I", 1) + fullbox(b"url ", flags=1))
    cname = bytes([len(compressor)]) + compressor + b"\0" * (31 - len(compressor))
    avc1 = box(b"avc1", b"\0" * 6 + struct.pack(">H", 1) + b"\0" * 16 + struct.pack(">HHIII", w, h, 0x480000, 0x480000, 0)
               + struct.pack(">H", 1) + cname + struct.pack(">Hh", 0x18, -1) + box(b"avcC", b"\x01\x64\x00\x1f\xff\xe1"))
    stbl = box(b"stbl", fullbox(b"stsd", struct.pack(">I", 1) + avc1) + b"".join(
        fullbox(t, b"\0" * 4) for t in (b"stts", b"stss", b"stsc", b"stsz", b"stco")))
    minf = box(b"minf", fullbox(b"vmhd", b"\0" * 8, flags=1) + box(b"dinf", dref) + stbl)
    trak = box(b"trak", fullbox(b"tkhd", b"\0" * 80, flags=3) + box(b"mdia", fullbox(b"mdhd", b"\0" * 20) + hdlr + minf) + meta_in_trak)
    moov = box(b"moov", mvhd + trak * tracks + extra_moov + udta)
    ftyp = box(b"ftyp", b"isom" + struct.pack(">I", 512) + b"isomiso2avc1mp41")
    free, mdat = box(b"free", free_payload), box(b"mdat", b"\0" * 64)
    body = (free + mdat + moov) if moov_last else (moov + free + mdat)
    return ftyp + body + extra_top + trailing
