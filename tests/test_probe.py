import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "sherpa" / "scripts"))

import probe  # noqa: E402

OS_RELEASE = '''
# comment
PRETTY_NAME="Ubuntu 24.04.1 LTS"
NAME="Ubuntu"
VERSION_ID="24.04"
ID=ubuntu
ID_LIKE=debian
VERSION_CODENAME=noble
'''

MEMINFO = """MemTotal:        8123456 kB
MemFree:         1000000 kB
MemAvailable:    5000000 kB
"""

PROC_NET_TCP = """  sl  local_address rem_address   st tx_queue rx_queue
   0: 00000000:0016 00000000:0000 0A 00000000:00000000 00:00000000 00000000     0
   1: 0100007F:1538 00000000:0000 0A 00000000:00000000 00:00000000 00000000     0
   2: 0100007F:9C40 0100007F:0050 01 00000000:00000000 00:00000000 00000000     0
"""


def make_profile(**overrides):
    """A healthy baseline profile; tests override the parts they care about."""
    p = {
        "os": {"system": "linux", "arch": "x86_64", "kernel": "6.1"},
        "resources": {"mem_effective_mb": 8000, "disk": {"path": "/", "free_gb": 50}},
        "environment": {"kind": "vm", "systemd": True},
        "privileges": {"user": "u", "is_root": False, "sudo": "passwordless", "groups": []},
        "tools": {"git": "git 2", "curl": "curl 8"},
        "package_managers": ["apt-get"],
        "docker": {"installed": False},
        "declared_target": None,
    }
    p.update(overrides)
    return p


def codes(profile):
    return {f["code"] for f in probe.derive_findings(profile)}


class ParserTests(unittest.TestCase):
    def test_os_release(self):
        rel = probe.parse_os_release(OS_RELEASE)
        self.assertEqual(rel["ID"], "ubuntu")
        self.assertEqual(rel["VERSION_ID"], "24.04")
        self.assertEqual(rel["PRETTY_NAME"], "Ubuntu 24.04.1 LTS")

    def test_meminfo(self):
        mem = probe.parse_meminfo(MEMINFO)
        self.assertEqual(mem["total_mb"], 7933)
        self.assertEqual(mem["available_mb"], 4882)

    def test_listening_ports_only_listen_state(self):
        ports = probe.parse_proc_net_tcp(PROC_NET_TCP)
        self.assertEqual(ports, [(22, "all"), (5432, "loopback")])

    def test_classify_virt(self):
        self.assertEqual(probe.classify_virt("none"), "bare-metal")
        self.assertEqual(probe.classify_virt("kvm"), "vm")
        self.assertEqual(probe.classify_virt("docker"), "container")
        self.assertEqual(probe.classify_virt("wsl"), "wsl")
        self.assertEqual(probe.classify_virt(""), "unknown")


class FindingTests(unittest.TestCase):
    def test_healthy_machine_has_no_findings(self):
        self.assertEqual(probe.derive_findings(make_profile()), [])

    def test_low_resources(self):
        p = make_profile(resources={"mem_effective_mb": 512, "disk": {"path": "/", "free_gb": 2}})
        self.assertTrue({"low-ram", "low-disk"} <= codes(p))

    def test_privilege_problems(self):
        pw = make_profile(privileges={"user": "u", "is_root": False, "sudo": "password-required", "groups": []})
        none = make_profile(privileges={"user": "u", "is_root": False, "sudo": "unavailable", "groups": []})
        self.assertIn("sudo-password", codes(pw))
        self.assertIn("no-escalation", codes(none))

    def test_docker_permission_denied(self):
        p = make_profile(docker={"installed": True, "daemon_running": False, "permission_denied": True})
        self.assertIn("docker-permission", codes(p))

    def test_declared_docker_target_without_docker(self):
        self.assertIn("docker-missing", codes(make_profile(declared_target="docker")))

    def test_declared_target_mismatch(self):
        p = make_profile(declared_target="bare-metal")
        self.assertIn("target-mismatch", codes(p))

    def test_container_without_systemd(self):
        p = make_profile(environment={"kind": "container", "systemd": False})
        self.assertIn("no-systemd", codes(p))

    def test_non_x86(self):
        p = make_profile(os={"system": "linux", "arch": "aarch64", "kernel": "6.1"})
        self.assertIn("non-x86", codes(p))


class SmokeTest(unittest.TestCase):
    """Runs against the real machine: checks shape, not values."""

    def test_collect_is_complete_and_json_safe(self):
        profile = probe.collect(declared_target="vps")
        json.dumps(profile)
        for key in ("os", "resources", "environment", "privileges", "tools",
                    "package_managers", "docker", "listening_ports", "findings"):
            self.assertIn(key, profile)
        self.assertEqual(profile["declared_target"], "vps")
        self.assertIsInstance(probe.render_text(profile), str)


if __name__ == "__main__":
    unittest.main()
