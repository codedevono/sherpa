"""Build a Context Profile of the machine this script runs on.

Usage:
    python3 probe.py [--json] [-o FILE] [--target vps|docker|bare-metal]

Rules for this script:
  * read-only: it never changes the system
  * offline: it never touches the network
  * stdlib only: it must run on a fresh box that has nothing but Python
  * graceful: every detector returns partial data instead of raising

The profile is plain JSON, so any agent (Claude, Hermes, ...) can read it
without importing anything.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

SCHEMA_VERSION = 1

# tool name -> argv that prints its version
TOOL_VERSION_CMDS = {
    "git": ["git", "--version"],
    "curl": ["curl", "--version"],
    "wget": ["wget", "--version"],
    "python3": ["python3", "--version"],
    "pip3": ["pip3", "--version"],
    "node": ["node", "--version"],
    "npm": ["npm", "--version"],
    "go": ["go", "version"],
    "rustc": ["rustc", "--version"],
    "cargo": ["cargo", "--version"],
    "gcc": ["gcc", "--version"],
    "make": ["make", "--version"],
    "java": ["java", "-version"],
    "ruby": ["ruby", "--version"],
    "php": ["php", "--version"],
    "docker": ["docker", "--version"],
    "docker-compose": ["docker-compose", "--version"],
    "podman": ["podman", "--version"],
    "systemctl": ["systemctl", "--version"],
    "ssh": ["ssh", "-V"],
    "sudo": ["sudo", "--version"],
}

PACKAGE_MANAGERS = ["apt-get", "dnf", "yum", "pacman", "apk", "zypper", "brew", "winget", "choco"]

ARCH_ALIASES = {"amd64": "x86_64", "arm64": "aarch64"}

# systemd-detect-virt names that mean "we are inside a container"
CONTAINER_VIRTS = {
    "docker", "podman", "lxc", "lxc-libvirt", "systemd-nspawn",
    "openvz", "rkt", "proot", "pouch",
}

PROXY_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def _run(cmd: list[str], timeout: float = 4) -> Optional[tuple[int, str]]:
    """Run a command. Return (returncode, output) or None if it could not run."""
    try:
        p = subprocess.run(
            cmd, capture_output=True, text=True, errors="replace",
            timeout=timeout, stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return p.returncode, (p.stdout.strip() or p.stderr.strip())


def _read(path: str) -> Optional[str]:
    try:
        return Path(path).read_text(errors="replace")
    except OSError:
        return None


# --------------------------------------------------------------------------
# pure parsers (unit-tested without touching the real system)
# --------------------------------------------------------------------------

def parse_os_release(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        out[key] = value.strip().strip('"').strip("'")
    return out


def parse_meminfo(text: str) -> dict:
    """Parse /proc/meminfo into MB. Values in the file are kB."""
    vals = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        parts = rest.split()
        if parts and parts[0].isdigit():
            vals[key.strip()] = int(parts[0])
    total, avail = vals.get("MemTotal"), vals.get("MemAvailable")
    return {
        "total_mb": total // 1024 if total else None,
        "available_mb": avail // 1024 if avail else None,
    }


def _addr_scope(addr_hex: str) -> str:
    if set(addr_hex) == {"0"}:
        return "all"
    if addr_hex in ("0100007F", "00000000000000000000000001000000"):
        return "loopback"
    return "specific"


def parse_proc_net_tcp(text: str) -> list[tuple[int, str]]:
    """Return (port, scope) for every socket in LISTEN state (st == 0A)."""
    found = []
    for line in text.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 4 or parts[3] != "0A":
            continue
        addr, _, port_hex = parts[1].rpartition(":")
        try:
            port = int(port_hex, 16)
        except ValueError:
            continue
        found.append((port, _addr_scope(addr)))
    return found


def classify_virt(name: str) -> str:
    """Map a systemd-detect-virt answer to bare-metal / vm / container / wsl."""
    v = name.strip().lower()
    if not v:
        return "unknown"
    if v == "none":
        return "bare-metal"
    if v == "wsl":
        return "wsl"
    if v in CONTAINER_VIRTS:
        return "container"
    return "vm"


# --------------------------------------------------------------------------
# detectors
# --------------------------------------------------------------------------

def detect_os() -> dict:
    system = platform.system().lower()
    arch = platform.machine().lower()
    info = {
        "system": system,
        "arch": ARCH_ALIASES.get(arch, arch),
        "kernel": platform.release(),
    }
    if system == "linux":
        text = _read("/etc/os-release") or _read("/usr/lib/os-release")
        if text:
            rel = parse_os_release(text)
            info.update({
                "distro_id": rel.get("ID"),
                "distro": rel.get("PRETTY_NAME") or rel.get("NAME"),
                "version": rel.get("VERSION_ID"),
                "codename": rel.get("VERSION_CODENAME"),
                "id_like": rel.get("ID_LIKE", "").split(),
            })
    elif system == "darwin":
        info.update({"distro": "macOS", "version": platform.mac_ver()[0]})
    elif system == "windows":
        info.update({"distro": "Windows", "version": platform.version()})
    return info


def _cgroup_mem_limit_mb() -> Optional[int]:
    """Memory limit imposed on a container, if any (cgroup v2, then v1)."""
    for path in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        text = _read(path)
        if text and text.strip().isdigit():
            value = int(text.strip())
            if value < (1 << 50):  # huge values mean "no limit"
                return value // (1024 * 1024)
    return None


def detect_resources() -> dict:
    total = available = None
    text = _read("/proc/meminfo")
    if text:
        mem = parse_meminfo(text)
        total, available = mem["total_mb"], mem["available_mb"]
    elif sys.platform == "darwin":
        r = _run(["sysctl", "-n", "hw.memsize"])
        if r and r[0] == 0 and r[1].isdigit():
            total = int(r[1]) // (1024 * 1024)

    limit = _cgroup_mem_limit_mb()
    effective = min(total, limit) if (total and limit) else total

    disk = None
    try:
        root = Path.cwd().anchor or "/"
        usage = shutil.disk_usage(root)
        disk = {
            "path": root,
            "total_gb": round(usage.total / 1024**3, 1),
            "free_gb": round(usage.free / 1024**3, 1),
        }
    except OSError:
        pass

    return {
        "cpus": os.cpu_count(),
        "mem_total_mb": total,
        "mem_available_mb": available,
        "mem_limit_mb": limit,
        "mem_effective_mb": effective,
        "disk": disk,
    }


def detect_environment() -> dict:
    """Is this bare metal, a VM (typical VPS), a container, or WSL?"""
    env = {
        "kind": "unknown",
        "detail": None,
        "systemd": os.path.isdir("/run/systemd/system"),
        "proxy_configured": any(os.environ.get(v) for v in PROXY_VARS),
    }
    if "microsoft" in (_read("/proc/version") or "").lower():
        env.update(kind="wsl", detail="wsl")
    elif os.path.exists("/.dockerenv") or os.path.exists("/run/.containerenv"):
        env.update(kind="container", detail="podman" if os.path.exists("/run/.containerenv") else "docker")
    else:
        r = _run(["systemd-detect-virt"])
        if r and r[1]:
            name = r[1].splitlines()[0]
            env.update(kind=classify_virt(name), detail=name)
        elif "hypervisor" in (_read("/proc/cpuinfo") or ""):
            env.update(kind="vm", detail="hypervisor flag in cpuinfo")
    return env


def _group_names() -> list[str]:
    try:
        import grp
    except ImportError:
        return []
    names = set()
    for gid in os.getgroups():
        try:
            names.add(grp.getgrgid(gid).gr_name)
        except KeyError:
            continue
    return sorted(names)


def _current_user() -> Optional[str]:
    try:
        import getpass
        return getpass.getuser()
    except Exception:
        return os.environ.get("USER") or os.environ.get("USERNAME")


def detect_privileges() -> dict:
    euid = os.geteuid() if hasattr(os, "geteuid") else None
    is_root = (euid == 0) if euid is not None else None
    if is_root:
        sudo = "root"
    elif shutil.which("sudo"):
        r = _run(["sudo", "-n", "true"])  # -n: never prompt, no side effects
        sudo = "passwordless" if (r and r[0] == 0) else "password-required"
    else:
        sudo = "unavailable"
    return {
        "user": _current_user(),
        "is_root": is_root,
        "sudo": sudo,
        "groups": _group_names(),
    }


def detect_tools() -> dict:
    """Map of installed tool -> first line of its version output."""
    def probe(item):
        name, cmd = item
        if not shutil.which(name):
            return name, None
        r = _run(cmd)
        line = r[1].splitlines()[0][:100] if (r and r[1]) else ""
        return name, line

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(probe, TOOL_VERSION_CMDS.items()))
    return {name: version for name, version in results if version is not None}


def detect_package_managers() -> list[str]:
    return [pm for pm in PACKAGE_MANAGERS if shutil.which(pm)]


def detect_docker(tools: dict, groups: list[str]) -> dict:
    info = {"installed": "docker" in tools}
    if not info["installed"]:
        return info
    r = _run(["docker", "info", "--format", "{{.ServerVersion}}"], timeout=6)
    running = bool(r and r[0] == 0 and r[1])
    compose = _run(["docker", "compose", "version", "--short"])
    info.update({
        "daemon_running": running,
        "server_version": r[1] if running else None,
        "permission_denied": bool(r and r[0] != 0 and "permission denied" in r[1].lower()),
        "compose_v2": compose[1] if (compose and compose[0] == 0) else None,
        "user_in_docker_group": "docker" in groups,
    })
    return info


def detect_listening_ports() -> Optional[list[dict]]:
    """Listening TCP ports. None means 'cannot tell' (e.g. not Linux)."""
    rank = {"all": 3, "specific": 2, "loopback": 1}
    best: dict[int, str] = {}
    seen_any = False
    for path in ("/proc/net/tcp", "/proc/net/tcp6"):
        text = _read(path)
        if text is None:
            continue
        seen_any = True
        for port, scope in parse_proc_net_tcp(text):
            if port not in best or rank[scope] > rank[best[port]]:
                best[port] = scope
    if not seen_any:
        return None
    return [{"port": p, "scope": best[p]} for p in sorted(best)]


# --------------------------------------------------------------------------
# findings: what the profile means for a setup plan
# --------------------------------------------------------------------------

def _f(level: str, code: str, message: str) -> dict:
    return {"level": level, "code": code, "message": message}


def derive_findings(p: dict) -> list[dict]:
    out = []
    res, env, priv = p["resources"], p["environment"], p["privileges"]
    tools, docker = p["tools"], p["docker"]

    mem = res.get("mem_effective_mb")
    if mem is not None and mem < 1024:
        out.append(_f("warn", "low-ram",
                      f"Only {mem} MB of RAM available; local builds (node, rust, image builds) "
                      "may be OOM-killed. The plan should add swap or avoid building here."))
    disk = res.get("disk")
    if disk and disk["free_gb"] < 5:
        out.append(_f("warn", "low-disk",
                      f"Only {disk['free_gb']} GB free on {disk['path']}; "
                      "large images and toolchains may not fit."))

    if priv["sudo"] == "password-required":
        out.append(_f("warn", "sudo-password",
                      "Not root and sudo needs a password, so steps that install system packages "
                      "cannot run unattended."))
    elif priv["sudo"] == "unavailable" and priv["is_root"] is False:
        out.append(_f("warn", "no-escalation",
                      "Not root and no sudo: system-wide installs are impossible, "
                      "the plan must stay in user space."))

    if not p["package_managers"] and p["os"]["system"] in ("linux", "darwin"):
        out.append(_f("warn", "no-package-manager", "No known package manager found on PATH."))
    if "git" not in tools:
        out.append(_f("warn", "no-git", "git is not installed; cloning repos will need it first."))
    if "curl" not in tools and "wget" not in tools:
        out.append(_f("warn", "no-downloader", "Neither curl nor wget is installed."))

    if docker.get("installed") and not docker.get("daemon_running"):
        if docker.get("permission_denied"):
            out.append(_f("warn", "docker-permission",
                          "Docker is installed but this user cannot reach the daemon (permission "
                          "denied). Add the user to the docker group or use sudo."))
        else:
            out.append(_f("warn", "docker-daemon-down",
                          "Docker is installed but the daemon is not reachable."))

    kind = env["kind"]
    if kind == "container" and not env["systemd"]:
        out.append(_f("info", "no-systemd",
                      "Inside a container without systemd: services cannot be managed with "
                      "systemctl; run them as foreground processes."))
    if kind == "wsl":
        out.append(_f("info", "wsl", "WSL detected: check that systemd is enabled before relying on services."))
    if p["os"]["arch"] != "x86_64":
        out.append(_f("info", "non-x86",
                      f"Architecture is {p['os']['arch']}: some prebuilt binaries and "
                      "container images are x86_64-only."))

    target = p.get("declared_target")
    if target == "docker" and not docker.get("installed"):
        out.append(_f("warn", "docker-missing",
                      "Declared target is docker but docker is not installed; "
                      "the plan will start by installing it."))
    if target == "vps" and kind not in ("vm", "unknown"):
        out.append(_f("info", "target-mismatch", f"Declared target is a VPS but this looks like: {kind}."))
    if target == "bare-metal" and kind in ("vm", "container", "wsl"):
        out.append(_f("info", "target-mismatch", f"Declared target is bare metal but this looks like: {kind}."))
    return out


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------

def collect(declared_target: Optional[str] = None) -> dict:
    """Probe this machine and return the Context Profile as a dict.

    declared_target is what the user *says* the goal is (vps, docker,
    bare-metal); everything else is detected.
    """
    privileges = detect_privileges()
    tools = detect_tools()
    profile = {
        "schema": SCHEMA_VERSION,
        "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "declared_target": declared_target,
        "os": detect_os(),
        "resources": detect_resources(),
        "environment": detect_environment(),
        "privileges": privileges,
        "tools": tools,
        "package_managers": detect_package_managers(),
        "docker": detect_docker(tools, privileges["groups"]),
        "listening_ports": detect_listening_ports(),
    }
    profile["findings"] = derive_findings(profile)
    return profile


def render_text(p: dict) -> str:
    """Human-readable summary of a profile."""
    o, r, e, pr, d = p["os"], p["resources"], p["environment"], p["privileges"], p["docker"]
    lines = ["Context Profile", ""]

    def row(key: str, value: str) -> None:
        lines.append(f"  {key:<14}{value}")

    if p.get("declared_target"):
        row("Target", f"{p['declared_target']} (declared)")
    row("OS", f"{o.get('distro') or o['system']} ({o['system']}, {o['arch']}, kernel {o['kernel']})")
    env_text = e["kind"] + (f" ({e['detail']})" if e.get("detail") else "")
    row("Environment", env_text + ("" if e["systemd"] else ", no systemd"))
    mem = r.get("mem_effective_mb")
    disk = r.get("disk")
    row("Resources", ", ".join(filter(None, [
        f"{r['cpus']} CPU" if r.get("cpus") else None,
        f"{mem / 1024:.1f} GB RAM" if mem else None,
        f"{disk['free_gb']} GB free" if disk else None,
    ])) or "unknown")
    row("Privileges", f"user={pr['user']}, sudo={pr['sudo']}")
    row("Pkg manager", ", ".join(p["package_managers"]) or "none found")

    if not d["installed"]:
        row("Docker", "not installed")
    else:
        state = "daemon running" if d.get("daemon_running") else "daemon NOT reachable"
        compose = f", compose {d['compose_v2']}" if d.get("compose_v2") else ""
        row("Docker", state + compose)
    row("Tools", ", ".join(sorted(p["tools"])) or "none")

    ports = p["listening_ports"]
    if ports is None:
        row("Listening", "unknown on this OS")
    else:
        row("Listening", ", ".join(f"{x['port']} ({x['scope']})" for x in ports) or "nothing")

    lines += ["", "Findings"]
    if p["findings"]:
        lines += [f"  [{f['level']}] {f['message']}" for f in p["findings"]]
    else:
        lines.append("  none")
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Detect this machine and print its Context Profile.")
    parser.add_argument("--json", action="store_true", help="print the profile as JSON")
    parser.add_argument("-o", "--output", metavar="FILE", help="also write the JSON profile to FILE")
    parser.add_argument(
        "--target", choices=["vps", "docker", "bare-metal"],
        help="where you intend to install (declared by you, not detected)",
    )
    args = parser.parse_args(argv)

    profile = collect(declared_target=args.target)
    if args.output:
        Path(args.output).write_text(json.dumps(profile, indent=2) + "\n")
        print(f"wrote {args.output}", file=sys.stderr)
    print(json.dumps(profile, indent=2) if args.json else render_text(profile))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
