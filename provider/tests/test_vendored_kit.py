"""The kit modules inside the package must stay byte-identical to kits/dobot_kit."""
from pathlib import Path

import pytest

from conftest import KIT, PROVIDER

VENDORED = PROVIDER / "roaas" / "robots" / "dobot_cr3a" / "kit"


@pytest.mark.parametrize("name", ["dobot_proto.py", "orders.py", "signers.py", "tpm_sign.ps1", "tpm_setup.ps1"])
def test_vendored_file_identical(name):
    assert (VENDORED / name).read_bytes() == (KIT / name).read_bytes(), f"{name} drifted from the kit"


def test_public_limits_identical():
    assert (PROVIDER / "roaas/robots/dobot_cr3a/public_limits.json").read_bytes() == \
        (KIT / "lab_limits_cr3a.json").read_bytes()


def test_controller_classes_copied_unchanged():
    kit_src = (KIT / "episode.py").read_text()
    ours = (PROVIDER / "roaas/robots/dobot_cr3a/controller.py").read_text()
    start = kit_src.index("class Dashboard:")
    end = kit_src.index("# ---------------------------------------------------------------- motion")
    assert kit_src[start:end].strip() in ours
