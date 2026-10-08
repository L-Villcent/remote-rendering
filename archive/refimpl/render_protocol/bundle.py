"""Bundle path and tar member rules (protocol-v1 section 6.2 / 6.3, R4).

Both render-publish (Linux) and the Worker (Windows) apply the same rules, so a
path that is safe on one side cannot alias a different file on the other.
Violation codes are fixed strings; they never contain the offending name.
"""
import re
import tarfile

MAX_ENTRIES = 2000
MAX_UNPACKED_BYTES = 200 * 1024 * 1024
MAX_PATH_LEN = 200
MAX_DEPTH = 16

_COMPONENT = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
# Windows reserved device names, with or without extension, any case.
_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(10)} | {f"LPT{i}" for i in range(10)}


def component_violation(c: str):
    if c in (".", ".."):
        return "XF-B01"            # dot segment / traversal
    if not _COMPONENT.match(c):
        return "XF-B02"            # charset (excludes \ : * ? " < > | space ~ $ and non-ASCII)
    if c.endswith("."):
        return "XF-B03"            # trailing dot (Windows strips it -> alias)
    if c.split(".", 1)[0].upper() in _RESERVED:
        return "XF-B04"            # reserved device name
    return None


def check_paths(entries):
    """entries: iterable of (path, is_dir) in archive order. Returns sorted violation codes.

    One case-folded namespace covers every explicit entry AND every implicit
    parent directory, so a name may appear with only one spelling and one type.
    An explicit directory entry for a directory already implied by a child (or
    vice versa) is allowed when the spelling is identical.
    """
    bad = set()
    spelling = {}   # folded name -> first spelling seen (explicit or implicit)
    kind = {}       # folded name -> "dir" | "file"
    explicit = set()

    def claim(name, k):
        key = name.lower()
        if key in spelling and spelling[key] != name:
            bad.add("XF-B07")      # case-insensitive alias (file or any directory level)
        spelling.setdefault(key, name)
        if key in kind and kind[key] != k:
            bad.add("XF-B08")      # same name used as file and directory
        kind.setdefault(key, k)

    for path, is_dir in entries:
        if not path or path.startswith("/") or path.endswith("/") or "//" in path:
            bad.add("XF-B05")      # absolute, empty component or trailing slash
            continue
        if len(path) > MAX_PATH_LEN:
            bad.add("XF-B06")
            continue
        parts = path.split("/")
        if len(parts) > MAX_DEPTH:
            bad.add("XF-B06")
            continue
        codes = [c for c in (component_violation(p) for p in parts) if c]
        if codes:
            bad.update(codes)
            continue
        if path in explicit:
            bad.add("XF-B13")      # exact duplicate entry (later one would overwrite)
        explicit.add(path)
        for i in range(1, len(parts)):
            claim("/".join(parts[:i]), "dir")
        claim(path, "dir" if is_dir else "file")
    return sorted(bad)


def check_tar(fileobj, entrypoint=None):
    """Validate a bundle tar from a snapshot file object. Returns violation codes."""
    bad = set()
    entries = []
    total = 0
    try:
        with tarfile.open(fileobj=fileobj, mode="r:") as tf:
            for n, m in enumerate(tf):
                if n >= MAX_ENTRIES:
                    bad.add("XF-B09")
                    break
                if not (m.isreg() or m.isdir()):
                    bad.add("XF-B10")      # symlink, hardlink, device, fifo ...
                    continue
                total += m.size
                if total > MAX_UNPACKED_BYTES:
                    bad.add("XF-B11")
                    break
                entries.append((m.name.rstrip("/") if m.isdir() else m.name, m.isdir()))
    except tarfile.TarError:
        return ["XF-B12"]
    bad.update(check_paths(entries))
    if entrypoint is not None and (entrypoint, False) not in entries:
        bad.add("XF-J12")
    return sorted(bad)
