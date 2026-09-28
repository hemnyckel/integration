"""Unit tests for the pure rename helpers in hemnyckel.mirror.identity."""

from __future__ import annotations

import importlib.util
import pathlib
import unittest

IDENTITY_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "custom_components"
    / "hemnyckel"
    / "mirror"
    / "identity.py"
)
spec = importlib.util.spec_from_file_location("hemnyckel_identity", IDENTITY_PATH)
assert spec is not None and spec.loader is not None
identity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(identity)


class NormaliseSerialTest(unittest.TestCase):
    def test_strips_separators(self) -> None:
        self.assertEqual(identity.normalise_serial("11:22:33:44:55:66"), "112233445566")
        self.assertEqual(identity.normalise_serial("AA-BB-CC-DD-EE-FF"), "aabbccddeeff")

    def test_empty(self) -> None:
        self.assertEqual(identity.normalise_serial(None), "")
        self.assertEqual(identity.normalise_serial(""), "")


class ZhaIeeeTest(unittest.TestCase):
    def test_picks_the_zha_identifier(self) -> None:
        self.assertEqual(
            identity.zha_ieee([("zigbee", "other"), ("zha", "11:22:33:44:55:66")]),
            "11:22:33:44:55:66",
        )

    def test_none_without_a_zha_identifier(self) -> None:
        self.assertIsNone(identity.zha_ieee([("zigbee", "11:22:33:44:55:66")]))
        self.assertIsNone(identity.zha_ieee([("zha", "")]))
        self.assertIsNone(identity.zha_ieee([]))


class RenamedEntityIdTest(unittest.TestCase):
    def test_follows_the_rename(self) -> None:
        event = {
            "action": "update",
            "entity_id": "lock.front_door",
            "old_entity_id": "lock.ytterdorren",
            "changes": {"entity_id": "lock.front_door"},
        }
        self.assertEqual(
            identity.renamed_entity_id(event, "lock.ytterdorren"),
            "lock.front_door",
        )

    def test_ignores_other_entity(self) -> None:
        event = {
            "action": "update",
            "entity_id": "lock.other",
            "old_entity_id": "lock.not_ours",
        }
        self.assertIsNone(identity.renamed_entity_id(event, "lock.ytterdorren"))

    def test_ignores_other_actions(self) -> None:
        self.assertIsNone(
            identity.renamed_entity_id({"action": "create", "entity_id": "lock.new"}, "lock.old")
        )
        self.assertIsNone(
            identity.renamed_entity_id({"action": "remove", "entity_id": "lock.old"}, "lock.old")
        )

    def test_ignores_a_plain_update(self) -> None:
        event = {"action": "update", "entity_id": "lock.same"}
        self.assertIsNone(identity.renamed_entity_id(event, "lock.same"))

    def test_ignores_when_we_follow_nothing(self) -> None:
        event = {
            "action": "update",
            "entity_id": "lock.new",
            "old_entity_id": "lock.old",
        }
        self.assertIsNone(identity.renamed_entity_id(event, None))


class EntityIdOnSerialTest(unittest.TestCase):
    serial_front = "11:22:33:44:55:66"
    serial_back = "aa:bb:cc:dd:ee:ff"

    def setUp(self) -> None:
        self.entities = [
            {
                "entity_id": "sensor.front_battery",
                "domain": "sensor",
                "device_id": "dev_front",
                "disabled_by": None,
            },
            {
                "entity_id": "lock.front",
                "domain": "lock",
                "device_id": "dev_front",
                "disabled_by": None,
            },
            {
                "entity_id": "lock.disabled",
                "domain": "lock",
                "device_id": "dev_front",
                "disabled_by": "user",
            },
            {
                "entity_id": "lock.back",
                "domain": "lock",
                "device_id": "dev_back",
                "disabled_by": None,
            },
        ]
        self.device_values = {
            "dev_front": [self.serial_front],
            "dev_back": [self.serial_back],
        }

    def test_matches_the_lock_on_that_serial(self) -> None:
        self.assertEqual(
            identity.entity_id_on_serial(
                self.entities, self.device_values, self.serial_front
            ),
            "lock.front",
        )
        self.assertEqual(
            identity.entity_id_on_serial(
                self.entities, self.device_values, self.serial_back
            ),
            "lock.back",
        )

    def test_serial_forms_compare_equal(self) -> None:
        self.assertEqual(
            identity.entity_id_on_serial(
                self.entities, self.device_values, "11-22-33-44-55-66"
            ),
            "lock.front",
        )

    def test_no_match(self) -> None:
        self.assertIsNone(
            identity.entity_id_on_serial(
                self.entities, self.device_values, "de:ad:be:ef:00:01"
            )
        )
        self.assertIsNone(identity.entity_id_on_serial(self.entities, self.device_values, None))


if __name__ == "__main__":
    unittest.main()
