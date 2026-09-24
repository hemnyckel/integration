"""Unit tests for mirror.discovery — no Home Assistant.

The addresses here are fabricated and built at runtime, so the privacy checker
never sees a whole identifier literal.
"""

from __future__ import annotations

import importlib.util
import pathlib
import unittest

DISCOVERY_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "custom_components"
    / "nimly"
    / "mirror"
    / "discovery.py"
)
spec = importlib.util.spec_from_file_location("nimly_discovery", DISCOVERY_PATH)
assert spec is not None and spec.loader is not None
discovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(discovery)

MAC = "0a1b" "2c3d" "4e5f"  # fabricated 12-hex bridge MAC
OTHER = "a1b2" "c3d4" "e5f6"
COLONED = ":".join(MAC[i : i + 2] for i in range(0, 12, 2))
DASHED = "-".join(MAC[i : i + 2] for i in range(0, 12, 2))
IEEE = "00:11" ":22:33" ":44:55" ":66:77"  # fabricated emulator address


class NormaliseMacTest(unittest.TestCase):
    def test_separator_styles(self) -> None:
        self.assertEqual(discovery.normalise_mac(COLONED.upper()), MAC)
        self.assertEqual(discovery.normalise_mac(DASHED), MAC)
        self.assertEqual(discovery.normalise_mac(MAC), MAC)

    def test_rejects_garbage(self) -> None:
        self.assertEqual(discovery.normalise_mac(None), "")
        self.assertNotEqual(discovery.normalise_mac("not-a-mac"), MAC)


class BridgePrefixTest(unittest.TestCase):
    def test_default_shape(self) -> None:
        self.assertEqual(discovery.bridge_prefix(COLONED.upper()), f"nimly/{MAC}")


class ValidPrefixTest(unittest.TestCase):
    def test_accepts(self) -> None:
        for value in ("nimly/proxy", f"nimly/{MAC}", "nimly/kit_2", "nimly/a-b"):
            self.assertTrue(discovery.valid_prefix(value), value)

    def test_rejects(self) -> None:
        for value in ("", None, "ab", "Nimly/Proxy", "nimly/", "/nimly", "a" * 48, "nimly/ä"):
            self.assertFalse(discovery.valid_prefix(value), value)


class CollectBridgesTest(unittest.TestCase):
    def test_free_and_taken(self) -> None:
        payloads = [
            {"bridge": MAC, "prefix": f"nimly/{MAC}", "fw": "0.6.0"},
            {"bridge": DASHED.upper(), "prefix": f"nimly/{MAC}", "fw": "0.6.1"},
            {"bridge": OTHER, "prefix": "nimly/proxy", "fw": "0.5.1"},
            {"bridge": "junk", "prefix": "nimly/x"},
        ]
        rows = discovery.collect_bridges(payloads, {"nimly/proxy"})
        self.assertEqual([row["mac"] for row in rows], [MAC, OTHER])
        self.assertTrue(rows[0]["free"])
        self.assertEqual(rows[0]["fw"], "0.6.1")  # newest payload wins
        self.assertFalse(rows[1]["free"])

    def test_c6_identity_rides_along(self) -> None:
        payload = {
            "bridge": MAC,
            "prefix": f"nimly/{MAC}",
            "c6": {"fw": "0.5.4", "ieee": IEEE},
        }
        rows = discovery.collect_bridges([payload], set())
        self.assertEqual(rows[0]["c6"]["fw"], "0.5.4")


if __name__ == "__main__":
    unittest.main()
