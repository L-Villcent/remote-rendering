"""Service entry point: render-gate <role> [--config FILE] [--once] [--sim-root DIR].

Roles: publish | ingest-light | ingest-heavy | netcheck.  systemd runs one role per
unit (ingest light and heavy are separate processes; per-job locks serialize them).
Logs carry only exception class names and errno codes, never paths or values.
--sim-root runs against a simulated tree; netcheck then never touches tailscaled.
"""
import argparse
import json
import os
import pathlib
import pwd
import signal
import sys
import time

from . import ids, netcheck
from .config import Layout, load_json
from .ingest import Ingest
from .minischema import MiniSchema
from .publish import Publisher

DEFAULTS = {"schemas_dir": None, "profiles": "/etc/render/profiles.json", "toolchains": "/etc/render/toolchains.json",
            "intervals": {"publish": 3, "ingest-light": 5, "ingest-heavy": 5, "netcheck": 60},
            "owners": {"outbox": "claude-agent", "up": "render-worker"}}


def _uid(name, sim):
    if sim:
        return None
    return pwd.getpwnam(name).pw_uid


def build(role, cfg, sim_root=None):
    lay = Layout.simulated(sim_root) if sim_root else Layout.production()
    schemas_dir = cfg.get("schemas_dir") or pathlib.Path(__file__).resolve().parents[2] / "schemas"
    schema = MiniSchema.from_dir(schemas_dir)
    if role == "publish":
        p = Publisher(lay, schema, expect_uid=_uid(cfg["owners"]["outbox"], sim_root))
        return p.run_once
    if role.startswith("ingest"):
        profiles = load_json(cfg["profiles"], {"profiles_version": 1, "profiles": {}})
        toolchains = load_json(cfg["toolchains"], {})
        ing = Ingest(lay, schema, profiles, toolchains, expect_uid=_uid(cfg["owners"]["up"], sim_root))
        return ing.run_light if role == "ingest-light" else ing.run_heavy
    if role == "netcheck":
        def once():
            cred = os.environ.get("CREDENTIALS_DIRECTORY")
            mapping = load_json(pathlib.Path(cred) / "netcheck-map", {}) if cred else {}
            status = cfg.get("_sim_status") if sim_root else None
            if sim_root and status is None:
                status = {}                       # simulation never queries the real daemon
            now = time.time()
            netcheck.run_once(lay, mapping, now, ids.utc_now(now), status=status)
        return once
    raise SystemExit("unknown role")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="render-gate")
    ap.add_argument("role", choices=["publish", "ingest-light", "ingest-heavy", "netcheck"])
    ap.add_argument("--config", default="/etc/render/gates.json")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--sim-root")
    a = ap.parse_args(argv)
    cfg = dict(DEFAULTS)
    cfg.update(load_json(a.config, {}) or {})
    step = build(a.role, cfg, a.sim_root)
    stop = []
    signal.signal(signal.SIGTERM, lambda *_: stop.append(1))
    interval = cfg["intervals"].get(a.role, 5)
    while not stop:
        try:
            step()
        except Exception as e:  # noqa: BLE001 - keep running; log class and errno only
            print(f"render-gate {a.role}: {type(e).__name__} errno={getattr(e, 'errno', None)}", file=sys.stderr)
            if a.once:
                return 1
        if a.once:
            return 0
        time.sleep(interval)
    return 0
