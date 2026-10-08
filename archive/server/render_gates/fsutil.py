"""Safe reads of untrusted trees and atomic writes (protocol-v1 6.2 / 6.7, security 3).

Untrusted trees are outbox (written by Claude) and up/ (written by the Worker).
Rules: walk every component with O_NOFOLLOW, accept only regular files owned
by the expected uid with a single link, bounded size, and copy while hashing
into a private location.  All later checks use the private copy only, so an
uploader that keeps a handle open cannot change what was verified.
Error codes are protocol reason codes; messages never contain names or data.
"""
import errno
import hashlib
import json
import os
import pathlib
import secrets
import stat

CHUNK = 1024 * 1024


class SnapshotError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _open_dir(base: pathlib.Path, parts):
    fd = os.open(base, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for p in parts:
            nfd = os.open(p, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            os.close(fd)
            fd = nfd
        return fd
    except BaseException:
        os.close(fd)
        raise


def _map_oserror(e: OSError):
    if e.errno == errno.ENOENT:
        return SnapshotError("missing_artifact")
    if e.errno in (errno.ELOOP, errno.ENOTDIR, errno.EMLINK):
        return SnapshotError("not_regular_file")
    if e.errno in (errno.EACCES, errno.EPERM):
        return SnapshotError("unreadable")
    return SnapshotError("unreadable")


def snapshot(base, rel: str, max_bytes: int, dest=None, expect_uid=None):
    """Copy base/rel (no symlinks anywhere below base) while hashing.

    Returns (sha256_hex, size, data) where data is bytes when dest is None,
    otherwise None and the copy is at dest (created exclusively, mode 0600).
    """
    parts = rel.split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise SnapshotError("not_regular_file")
    try:
        dfd = _open_dir(pathlib.Path(base), parts[:-1])
    except OSError as e:
        raise _map_oserror(e) from None
    try:
        try:
            fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=dfd)
        except OSError as e:
            raise _map_oserror(e) from None
    finally:
        os.close(dfd)
    out = None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
            raise SnapshotError("not_regular_file")
        if expect_uid is not None and st.st_uid != expect_uid:
            raise SnapshotError("not_regular_file")
        if st.st_size > max_bytes:
            raise SnapshotError("size_exceeded")
        h, size, buf = hashlib.sha256(), 0, []
        if dest is not None:
            out = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
        while True:
            chunk = os.read(fd, min(CHUNK, max_bytes + 1 - size))
            if not chunk:
                break
            size += len(chunk)
            if size > max_bytes:          # file grew during the copy
                raise SnapshotError("size_exceeded")
            h.update(chunk)
            if out is None:
                buf.append(chunk)
            else:
                os.write(out, chunk)
        if out is not None:
            os.fsync(out)
        return h.hexdigest(), size, (b"".join(buf) if dest is None else None)
    except SnapshotError:
        if out is not None:
            os.close(out)
            out = None
            os.unlink(dest)
        raise
    finally:
        os.close(fd)
        if out is not None:
            os.close(out)


def list_entries(base, rel=""):
    """[(name, is_dir)] of base/rel without following symlinks; [] if missing."""
    parts = [p for p in rel.split("/") if p]
    try:
        fd = _open_dir(pathlib.Path(base), parts)
    except OSError:
        return []
    try:
        with os.scandir(fd) as it:
            return sorted((e.name, e.is_dir(follow_symlinks=False)) for e in it)
    finally:
        os.close(fd)


def atomic_write_bytes(directory, name: str, data: bytes, mode=0o640):
    d = pathlib.Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / f".tmp-{secrets.token_hex(8)}"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, mode)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, d / name)


def atomic_write_json(directory, name: str, obj):
    atomic_write_bytes(directory, name, (json.dumps(obj, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))


def private_dir(parent, prefix):
    d = pathlib.Path(parent) / f"{prefix}-{secrets.token_hex(6)}"
    d.mkdir(mode=0o700)
    return d
