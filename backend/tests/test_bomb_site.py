import unittest

from app.parser.demo_parser import _build_bomb_events
from app.parser.replay_contract import normalize_replay_contract


class BombSiteTest(unittest.TestCase):
    def test_internal_indices_do_not_become_site_names_or_drop_the_event(self):
        for site in (313, "313", 0, "0", 1, "1", True, None, [], {}, "unknown"):
            with self.subTest(site=site):
                events = _build_bomb_events(
                    [{"tick": 88208, "user_steamid": "76561198355739212", "site": site,
                      "X": 120.0, "Y": 240.0, "Z": -400.0}], [], [],
                )
                self.assertEqual(len(events), 1)
                event = events[0]
                self.assertEqual(event["id"], "bomb_planted-88208-76561198355739212")
                self.assertEqual(event["label"], "Bomb planted")
                self.assertNotIn("site", event["metadata"])
                self.assertEqual((event["x"], event["y"], event["z"]), (120, 240, -400))

    def test_explicit_site_letters_are_preserved_and_normalized(self):
        for raw, expected in (("A", "A"), (" b ", "B")):
            with self.subTest(site=raw):
                parsed = _build_bomb_events([{"tick": 42, "site": raw}], [], [])[0]
                event = normalize_replay_contract({"events": [parsed]})["events"][0]
                self.assertEqual(event["metadata"]["site"], expected)
                self.assertEqual(event["label"], f"Bomb planted {expected}")

    def test_old_stored_labels_and_frame_sites_are_sanitized_on_read(self):
        raw = {
            "events": [{"id": "plant", "type": "bomb_planted", "tick": 88208,
                        "label": "Bomb planted 313", "metadata": {"site": "313"},
                        "x": 12, "y": 24, "z": -400}],
            "frames": [{"tick": 88208, "players": [],
                        "bombState": {"status": "planted", "site": 313, "x": 12, "y": 24, "z": -400}}],
        }
        normalized = normalize_replay_contract(raw)
        self.assertEqual(normalized["events"][0]["label"], "Bomb planted")
        self.assertNotIn("site", normalized["events"][0]["metadata"])
        self.assertEqual(normalized["frames"][0]["bombState"],
                         {"status": "planted", "x": 12, "y": 24, "z": -400})
        self.assertEqual(normalized["diagnostics"]["eventFamilyCounts"]["objective"], 1)
        self.assertEqual(raw["events"][0]["metadata"]["site"], "313")

    def test_bad_labels_do_not_override_explicit_site_or_fail_missing_events(self):
        replay = normalize_replay_contract({"events": [
            {"type": "bomb_planted", "tick": 10, "label": "Bomb planted 313", "site": "b"},
            {"type": "bomb_planted", "tick": 20, "label": "Bomb planted 313"},
        ]})
        self.assertEqual([event["label"] for event in replay["events"]], ["Bomb planted B", "Bomb planted"])
        self.assertEqual(normalize_replay_contract({})["events"], [])
