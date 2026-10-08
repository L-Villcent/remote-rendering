"""PV-05: count occurrences of the REAL local public address - for the user only.

Run it yourself in a separate terminal on the VPS, NOT inside a Claude session and NOT
via the '!' prefix: the address is read without echo, never stored, and only counts are
printed.  Expected result: 0 everywhere.
Usage: python3 -I pv05_count.py /srv/render-data/inbox /srv/agent /srv/agent/home/.claude
"""
import getpass
import os
import sys


def count(roots, needle: bytes):
    files = hits = 0
    for root in roots:
        for dirpath, _, names in os.walk(root):
            for n in names:
                files += 1
                try:
                    with open(os.path.join(dirpath, n), "rb") as f:
                        hits += f.read().count(needle)
                except OSError:
                    continue
    return files, hits


def main():
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        print("refusing: run interactively in your own terminal", file=sys.stderr)
        return 2
    addr = getpass.getpass("address (not echoed): ").strip().encode()
    if not addr:
        return 2
    files, hits = count(sys.argv[1:], addr)
    del addr
    print(f"PV-05 files_scanned={files} occurrences={hits} -> {'PASS' if hits == 0 else 'FAIL'}")
    return 0 if hits == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
