import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

PROVIDER = Path(__file__).resolve().parents[1]
REPO = PROVIDER.parent
KIT = REPO / "kits" / "dobot_kit"
sys.path.insert(0, str(PROVIDER))


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_port(port, timeout=10):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
            return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError(f"fake controller did not open port {port}")


class FakeRobot:
    """kits/dobot_kit/fake_dobot.py (unchanged) on free ports."""

    def __init__(self):
        self.dashboard_port, self.feedback_port = free_port(), free_port()
        self.proc = subprocess.Popen(
            [sys.executable, str(KIT / "fake_dobot.py"), "--dashboard-port", str(self.dashboard_port),
             "--feedback-port", str(self.feedback_port)],
            cwd=KIT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        _wait_port(self.dashboard_port)
        _wait_port(self.feedback_port)

    @property
    def ports(self):
        return {"dashboard_port": self.dashboard_port, "feedback_port": self.feedback_port}

    def stop(self):
        self.proc.terminate()
        self.proc.wait(5)


@pytest.fixture(scope="module")
def fake_robot():
    r = FakeRobot()
    yield r
    r.stop()


@pytest.fixture
def fresh_fake_robot():
    r = FakeRobot()
    yield r
    r.stop()


@pytest.fixture(scope="session")
def robot_info():
    # The kit's public limits double as the robot_info of the fake controller (as in the kit's README).
    return json.loads((KIT / "lab_limits_cr3a.json").read_text())


@pytest.fixture(scope="module")
def signer(tmp_path_factory):
    from roaas.robots.dobot_cr3a.kit import signers
    return signers.make_signer("software", tmp_path_factory.mktemp("keys"))
