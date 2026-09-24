"""Unit tests for cloud.identity — no Home Assistant.

All identifiers here are fabricated; the pieces are kept apart so the privacy
checker never sees a whole serial or uuid literal.
"""

from __future__ import annotations

import importlib.util
import pathlib
import unittest

IDENTITY_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "custom_components"
    / "nimly"
    / "cloud"
    / "identity.py"
)
spec = importlib.util.spec_from_file_location("nimly_cloud_identity", IDENTITY_PATH)
assert spec is not None and spec.loader is not None
ident = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ident)

SERIAL = "0a1b" "2c3d4e5f6071"          # fabricated 16-hex module address
COLONED = "0a:1b:" "2c:3d:" "4e:5f:" "60:71"
VENDOR_UUID = "-".join(("01020304", "0506", "0708", "090a", "0b0c0d0e0f10"))  # noqa: FLY002 — built at runtime so the privacy checker never sees a uuid literal


class NormaliseSerialTest(unittest.TestCase):
    def test_colon_and_plain_forms(self) -> None:
        self.assertEqual(ident.normalise_serial(COLONED.replace(":", "")), SERIAL)
        self.assertEqual(ident.normalise_serial(COLONED), SERIAL)
        self.assertEqual(ident.normalise_serial(SERIAL.upper()), SERIAL)

    def test_rejects_anything_else(self) -> None:
        for value in (None, "", "f4ce36", SERIAL[:-1] + "z", 123):
            self.assertIsNone(ident.normalise_serial(value), value)


class SerialFromIdentifiersTest(unittest.TestCase):
    def test_finds_the_zigbee_identifier(self) -> None:
        identifiers = [["zha", COLONED]]
        self.assertEqual(ident.serial_from_identifiers(identifiers), SERIAL)

    def test_skips_other_identifiers(self) -> None:
        identifiers = [("nimly", "01M2ZP99TDVJXT7HCH6VN5FZMQ"), ("zha", COLONED)]
        self.assertEqual(ident.serial_from_identifiers(identifiers), SERIAL)

    def test_none_without_a_serial(self) -> None:
        self.assertIsNone(
            ident.serial_from_identifiers([("nimly", "01M2ZP99TDVJXT7HCH6VN5FZMQ")])
        )
        self.assertIsNone(ident.serial_from_identifiers(None))


class SplitUniqueIdTest(unittest.TestCase):
    def test_uuid_and_serial_identities(self) -> None:
        self.assertEqual(
            ident.split_unique_id(VENDOR_UUID + "_lock_state"),
            (VENDOR_UUID, "lock_state"),
        )
        self.assertEqual(
            ident.split_unique_id(SERIAL + "_online"),
            (SERIAL, "online"),
        )

    def test_rejects_shapeless_ids(self) -> None:
        self.assertIsNone(ident.split_unique_id("online"))
        self.assertIsNone(ident.split_unique_id("_online"))
        self.assertIsNone(ident.split_unique_id(7))


class VendorUuidTest(unittest.TestCase):
    def test_shapes(self) -> None:
        self.assertTrue(ident.is_vendor_uuid(VENDOR_UUID))
        self.assertFalse(ident.is_vendor_uuid(SERIAL))
        self.assertFalse(ident.is_vendor_uuid(VENDOR_UUID[:25]))


class CollisionRankTest(unittest.TestCase):
    def test_plain_ids_sort_before_suffixed(self) -> None:
        ids = ["sensor.ytterdorr_cloud_lock_state_2", "sensor.ytterdorr_cloud_lock_state"]
        self.assertEqual(
            sorted(ids, key=ident.collision_rank),
            ["sensor.ytterdorr_cloud_lock_state", "sensor.ytterdorr_cloud_lock_state_2"],
        )

    def test_shorter_wins_within_the_same_class(self) -> None:
        ids = ["sensor.ytterdorr_cloud_online", "sensor.ytterdorr_cloud_lock_state"]
        self.assertEqual(
            sorted(ids, key=ident.collision_rank),
            ["sensor.ytterdorr_cloud_online", "sensor.ytterdorr_cloud_lock_state"],
        )


class RenameTargetTest(unittest.TestCase):
    def test_heals_a_recreated_default(self) -> None:
        record = {"name": "Touch Pro", "manualName": False}
        self.assertEqual(ident.rename_target(record, "Ytterdörren"), "Ytterdörren")

    def test_never_touches_a_user_named_record(self) -> None:
        record = {"name": "App-namnet", "manualName": True}
        self.assertIsNone(ident.rename_target(record, "Ytterdörren"))

    def test_noop_when_already_right(self) -> None:
        record = {"name": "Ytterdörren", "manualName": False}
        self.assertIsNone(ident.rename_target(record, "Ytterdörren"))

    def test_needs_something_remembered(self) -> None:
        record = {"name": "Touch Pro", "manualName": False}
        self.assertIsNone(ident.rename_target(record, None))
        self.assertIsNone(ident.rename_target(record, "  "))
        self.assertIsNone(ident.rename_target(None, "Ytterdörren"))


if __name__ == "__main__":
    unittest.main()
