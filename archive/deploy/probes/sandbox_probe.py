"""SB-01..SB-14 probe. Runs INSIDE the sandbox via `claude-entry --probe` (acceptance-v1 SB).

Config (/probe/probe.json, written by canaries.sh): canary token, canary paths, canary
port, deny targets, allow targets, MagicDNS names, env whitelist.
Output: one line per check, 'SB-xx PASS|FAIL <reason-code>'.  It never prints what it
read, which host it reached or any value: a broken sandbox must not turn this probe into
a leak.  Synthetic canaries make even an accidental echo harmless.
"""
import json
import os
import re
import socket
import subprocess
import sys

IPV4 = re.compile(r"(?<![\d.])(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(?!\.?\d)")
ABSENT_PATHS = ["/var", "/home/code", "/sys", "/srv", "/run/tailscale", "/run/dbus", "/run/user",
                "/var/lib/wtmpdb", "/var/log", "/etc/ssh", "/etc/render/netcheck-map.json"]
ABSENT_SOCKETS = ["/run/tailscale/tailscaled.sock", "/run/dbus/system_bus_socket", "/run/systemd/private",
                  "/tmp/tmux-1001", "/run/user"]


def env_ok(env, whitelist, token):
    if not set(env) <= set(whitelist):
        return False, "unexpected_key"
    for v in env.values():
        if IPV4.search(v) or token in v:
            return False, "sensitive_value"
    return True, "ok"


def extra_fds(fd_names, own=()):
    return sorted(int(n) for n in fd_names if n.isdigit() and int(n) > 2 and int(n) not in own)


def proc_net_ports(text):
    """Local ports from /proc/net/tcp-style text."""
    ports = set()
    for line in text.splitlines()[1:]:
        f = line.split()
        if len(f) > 2 and ":" in f[1]:
            ports.add(int(f[1].rsplit(":", 1)[1], 16))
    return ports


def runs_ok(cmd):
    """True if the command exists and exits 0. Output is discarded unread."""
    try:
        return subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def output_lines(cmd):
    """Number of output lines only (content is counted, never kept)."""
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=10)
        return p.returncode, len(p.stdout.splitlines())
    except (OSError, subprocess.TimeoutExpired):
        return -1, 0


def connects(host, port, timeout=3):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def contains_token(root, token, limit=20000):
    tb = token.encode()
    n = 0
    for dirpath, _, files in os.walk(root):
        for f in files:
            n += 1
            if n > limit:
                return False
            try:
                with open(os.path.join(dirpath, f), "rb") as fh:
                    if tb in fh.read(4 << 20):
                        return True
            except OSError:
                continue
    return False


def run(cfg):
    out = []

    def rep(cid, ok, code="ok"):
        out.append(f"{cid} {'PASS' if ok else 'FAIL'} {code}")

    tok = cfg["canary_token"]
    rep("SB-01", *env_ok(dict(os.environ), cfg["env_whitelist"], tok))
    fds = extra_fds(os.listdir("/proc/self/fd"))
    rep("SB-02", len(fds) <= 1, "ok" if len(fds) <= 1 else "inherited_fd")      # 1 = listdir's own fd
    visible = [p for p in cfg["canary_paths"] + ABSENT_PATHS if os.path.lexists(p)]
    rep("SB-03", not visible, "ok" if not visible else "host_path_visible")
    socks = [p for p in ABSENT_SOCKETS if os.path.lexists(p)]
    rep("SB-04", not socks and not runs_ok(["tailscale", "status"]), "ok" if not socks else "host_socket_visible")
    rc_last, n_last = output_lines(["last", "-n", "5"])
    rc_who, n_who = output_lines(["who"])
    rep("SB-05", (rc_last != 0 or n_last <= 2) and (rc_who != 0 or n_who == 0), "ok")
    leaked = False
    for pid in (p for p in os.listdir("/proc") if p.isdigit()):
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                if tok.encode() in f.read():
                    leaked = True
        except OSError:
            pass
    rep("SB-06", not leaked, "ok" if not leaked else "host_process_visible")
    ports = set()
    for name in ("tcp", "tcp6", "udp", "udp6"):
        try:
            with open(f"/proc/net/{name}", encoding="utf-8", errors="replace") as f:
                ports |= proc_net_ports(f.read())
        except OSError:
            pass
    rep("SB-07", cfg["canary_port"] not in ports, "ok" if cfg["canary_port"] not in ports else "host_socket_table")
    allow = all(connects(h, p) for h, p in cfg["allow_targets"])
    rep("SB-08", allow, "ok" if allow else "egress_blocked")
    denied = [i for i, (h, p) in enumerate(cfg["deny_targets"]) if connects(h, p)]
    rep("SB-09", not denied, "ok" if not denied else f"reachable_target_index_{denied[0]}")
    resolved = False
    for name in cfg["magicdns_names"]:
        try:
            socket.getaddrinfo(name, None)
            resolved = True
        except OSError:
            pass
    with open("/etc/resolv.conf", encoding="utf-8", errors="replace") as f:
        rc = f.read()
    rep("SB-10", not resolved and cfg["magicdns_resolver"] not in rc, "ok" if not resolved else "magicdns_resolves")
    channels = [c for c in (["systemd-run", "--user", "true"], ["crontab", "-l"], ["at", "-l"], ["sudo", "-n", "true"])
                if runs_ok(c)]
    rep("SB-11", not channels, "ok" if not channels else f"host_exec_{channels[0][0]}")
    with open("/proc/self/status", encoding="utf-8", errors="replace") as f:
        st = dict(line.split(":", 1) for line in f.read().splitlines() if ":" in line)
    nnp = st.get("NoNewPrivs", "").strip() == "1"
    caps = int(st.get("CapEff", "1").strip(), 16) == 0
    rep("SB-12", nnp and caps, "ok" if nnp and caps else "privileges")
    rep("SB-13", not runs_ok(["dmesg"]), "ok")
    home = os.path.expanduser("~/.claude")
    rep("SB-14", not contains_token(home, tok), "ok")
    return out


def main():
    cfg = json.load(open(sys.argv[1], encoding="utf-8"))
    lines = run(cfg)
    print("\n".join(lines))
    return 0 if all(" PASS " in ln for ln in lines) else 1


if __name__ == "__main__":
    sys.exit(main())
