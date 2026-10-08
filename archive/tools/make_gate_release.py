"""Build a reviewed, reproducible gate release: dist/render-gates-<version>.tar + MANIFEST.sha256.

The admin reviews the diff, then deploy/vps/70-gates.sh verifies every hash before
installing to /opt/render-gates/<version>.  Only stdlib code and schemas are shipped.
Usage: python3 -I tools/make_gate_release.py <version>
"""
import hashlib
import io
import pathlib
import sys
import tarfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
INCLUDE = ["bin/render-gate", "server/render_gates", "refimpl/render_protocol", "schemas"]


def files():
    for inc in INCLUDE:
        p = ROOT / inc
        for f in ([p] if p.is_file() else sorted(p.rglob("*"))):
            if f.is_file() and "__pycache__" not in f.parts:
                yield f.relative_to(ROOT).as_posix(), f


def main(version):
    out = ROOT / "dist"
    out.mkdir(exist_ok=True)
    manifest, buf = [], io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.USTAR_FORMAT) as tf:
        for rel, f in files():
            data = f.read_bytes()
            manifest.append(f"{hashlib.sha256(data).hexdigest()}  {rel}")
            ti = tarfile.TarInfo(f"render-gates-{version}/{rel}")
            ti.size, ti.mtime, ti.uid, ti.gid, ti.uname, ti.gname = len(data), 0, 0, 0, "root", "root"
            ti.mode = 0o755 if rel.startswith("bin/") else 0o644
            tf.addfile(ti, io.BytesIO(data))
    tar = out / f"render-gates-{version}.tar"
    tar.write_bytes(buf.getvalue())
    (out / f"render-gates-{version}.MANIFEST.sha256").write_text("\n".join(manifest) + "\n", encoding="utf-8", newline="\n")
    print(f"{tar.name} sha256={hashlib.sha256(buf.getvalue()).hexdigest()} files={len(manifest)}")


if __name__ == "__main__":
    main(sys.argv[1])
