"""Unit tests for decode_operation_event (attribute 0x0100), no HA needed."""

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
spec = importlib.util.spec_from_file_location("hemnyckel_const_decode", CONST_PATH)
assert spec is not None and spec.loader is not None
const = importlib.util.module_from_spec(spec)
spec.loader.exec_module(const)


class DecodeOperationEventTest(unittest.TestCase):
    def test_keypad_unlock_with_slot(self) -> None:
        decoded = const.decode_operation_event(0x02020009)
        self.assertIsNotNone(decoded)
        self.assertEqual(decoded["user_slot"], 9)
        self.assertEqual(decoded["action_code"], 0x02)
        self.assertEqual(decoded["source_code"], 0x02)
        self.assertEqual(decoded["action"], "unlock")
        self.assertEqual(decoded["source"], "keypad")

    def test_fingerprint_unlock_with_slot(self) -> None:
        decoded = const.decode_operation_event(0x03020003)
        self.assertIsNotNone(decoded)
        self.assertEqual(decoded["user_slot"], 3)
        self.assertEqual(decoded["action"], "unlock")
        self.assertEqual(decoded["source"], "fingerprint")

    def test_lock_without_slot(self) -> None:
        decoded = const.decode_operation_event(0x05010000)
        self.assertIsNotNone(decoded)
        self.assertIsNone(decoded["user_slot"])
        self.assertFalse(decoded["master"])
        self.assertEqual(decoded["action"], "lock")
        self.assertEqual(decoded["source"], "unattributed")

    def test_master_credential_is_slot_zero_with_a_human_source(self) -> None:
        decoded = const.decode_operation_event(0x02020000)
        self.assertIsNotNone(decoded)
        self.assertEqual(decoded["user_slot"], 0)
        self.assertTrue(decoded["master"])
        self.assertEqual(decoded["action"], "unlock")
        self.assertEqual(decoded["source"], "keypad")

    def test_slot_zero_without_a_human_source_is_no_user(self) -> None:
        decoded = const.decode_operation_event(0x0A010000)
        self.assertIsNotNone(decoded)
        self.assertIsNone(decoded["user_slot"])
        self.assertFalse(decoded["master"])

    def test_auto_relock(self) -> None:
        decoded = const.decode_operation_event(0x0A010000)
        self.assertIsNotNone(decoded)
        self.assertEqual(decoded["action"], "lock")
        self.assertEqual(decoded["source"], "auto")

    def test_slot_is_sixteen_bits(self) -> None:
        decoded = const.decode_operation_event(0x00020C2C)
        self.assertIsNotNone(decoded)
        self.assertEqual(decoded["user_slot"], 0x0C2C)

    def test_unknown_codes_have_no_names(self) -> None:
        decoded = const.decode_operation_event(0x7F7F0000)
        self.assertIsNotNone(decoded)
        self.assertIsNone(decoded["action"])
        self.assertIsNone(decoded["source"])

    def test_out_of_range(self) -> None:
        self.assertIsNone(const.decode_operation_event(-1))
        self.assertIsNone(const.decode_operation_event(0x1_0000_0000))


if __name__ == "__main__":
    unittest.main()
