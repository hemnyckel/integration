"""Unit tests for the pure logic in hemnyckel.const — no Home Assistant needed."""

from __future__ import annotations

import importlib.util
import pathlib
import unittest

CONST_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "custom_components"
    / "hemnyckel"
    / "const.py"
)
spec = importlib.util.spec_from_file_location("hemnyckel_const", CONST_PATH)
assert spec is not None and spec.loader is not None
const = importlib.util.module_from_spec(spec)
spec.loader.exec_module(const)


class ConstantsTest(unittest.TestCase):
    def test_domain(self) -> None:
        self.assertEqual(const.DOMAIN, "hemnyckel")


if __name__ == "__main__":
    unittest.main()
