"""render-netcheck: tailscaled status -> whitelisted netcheck_view (security-boundary 3).

summarize() is a pure function over an already-parsed status document and the
alias mapping {"worker-local": "<stable node id>"}.  It reads only Online,
CurAddr/Relay presence and LastHandshake; it never copies any value from the
status document into the output, so endpoints, names and addresses cannot leak.

fetch_status() talks to the local tailscaled socket.  It is only meant to run as
the render-netcheck service after deployment; development and tests never call it.
"""
import datetime
import json
import re
import socket

SOCKET = "/run/tailscale/tailscaled.sock"
FRESH_HANDSHAKE_S = 180


_TS = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:\d{2})$")


def _parse_age(handshake, now_epoch):
    """Seconds since LastHandshake (RFC 3339, any fraction digits), or None if absent/zero/unparseable."""
    if not isinstance(handshake, str) or handshake.startswith("0001-01-01"):
        return None
    m = _TS.match(handshake)
    if not m:
        return None
    base, frac, tz = m.groups()
    try:
        t = datetime.datetime.fromisoformat(base + ("+00:00" if tz == "Z" else tz))
    except ValueError:
        return None
    return now_epoch - (t.timestamp() + (float("0." + frac) if frac else 0.0))


def summarize(status, mapping, now_epoch, checked_at):
    view = {"protocol_version": 1, "kind": "netcheck_view", "worker_alias": "worker-local",
            "online": None, "known": False, "reachable": None, "path": "unknown", "error_code": None,
            "checked_at": checked_at}
    node_id = mapping.get("worker-local") if isinstance(mapping, dict) else None
    if not node_id:
        view["error_code"] = "not_configured"
        return view
    if not isinstance(status, dict) or not isinstance(status.get("Peer"), dict):
        view["error_code"] = "daemon_unavailable"
        return view
    peer = next((p for p in status["Peer"].values() if isinstance(p, dict) and p.get("ID") == node_id), None)
    if peer is None:
        view["error_code"] = "peer_unknown"
        return view
    view["known"] = True
    online = peer.get("Online")
    view["online"] = online if isinstance(online, bool) else None
    if peer.get("CurAddr"):
        view["path"] = "direct"
    elif peer.get("Relay"):
        view["path"] = "relay"
    age = _parse_age(peer.get("LastHandshake"), now_epoch)
    view["reachable"] = None if age is None and view["online"] is None else bool(view["online"] and age is not None
                                                                                  and age <= FRESH_HANDSHAKE_S)
    return view


def fetch_status(sock_path=SOCKET, timeout=5.0):
    """GET /localapi/v0/status over the unix socket. Deployment-only; returns dict or None."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect(sock_path)
            s.sendall(b"GET /localapi/v0/status HTTP/1.0\r\nHost: local-tailscaled.sock\r\n\r\n")
            chunks = []
            while True:
                b = s.recv(65536)
                if not b:
                    break
                chunks.append(b)
        head, _, body = b"".join(chunks).partition(b"\r\n\r\n")
        if not head.startswith(b"HTTP/1.") or b" 200 " not in head.split(b"\r\n", 1)[0]:
            return None
        return json.loads(body)
    except (OSError, ValueError):
        return None


def run_once(layout, mapping, now_epoch, checked_at, status=None):
    """Write inbox/netcheck/status.json. `status` is injectable for simulation."""
    from .fsutil import atomic_write_json
    try:
        view = summarize(status if status is not None else fetch_status(), mapping, now_epoch, checked_at)
    except Exception:  # noqa: BLE001 - any failure collapses to an enum, never to text
        view = {"protocol_version": 1, "kind": "netcheck_view", "worker_alias": "worker-local", "online": None,
                "known": False, "reachable": None, "path": "unknown", "error_code": "internal", "checked_at": checked_at}
    atomic_write_json(layout.inbox / "netcheck", "status.json", view)
    return view
