"""Modules copied byte for byte from kits/dobot_kit (v0.4.5); tests/test_vendored_kit.py guards the copy.

The kit's modules import each other as top-level modules (`import dobot_proto as dp`), so dobot_proto is
registered under that name before orders is imported. signers (which needs `cryptography`) is imported
only where a signature is made, keeping the quote path light.
"""
import sys

from . import dobot_proto

sys.modules.setdefault("dobot_proto", dobot_proto)

from . import orders  # noqa: E402

__all__ = ["dobot_proto", "orders"]
