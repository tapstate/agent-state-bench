import importlib.util
import pathlib
import sys
import unittest


MODULE_PATH = pathlib.Path(__file__).parents[1] / "banking_state_bench.py"
SPEC = importlib.util.spec_from_file_location("banking_state_bench", MODULE_PATH)
bench = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = bench
SPEC.loader.exec_module(bench)


class DynamicStateTests(unittest.TestCase):
    def test_stale_reader_differs_and_write_is_revalidated(self):
        result = bench.dynamic_smoke()
        self.assertEqual(result["label"], "CUSTOM_DYNAMIC_EXTENSION_NOT_UPSTREAM_SCORE")
        self.assertEqual(result["stale_read"]["version"], 1)
        self.assertEqual(result["stale_read"]["state"]["status"], "ACTIVE")
        self.assertEqual(result["current_read"]["version"], 2)
        self.assertEqual(result["current_read"]["state"]["status"], "BLOCKED")
        self.assertFalse(result["accepted"])
        self.assertIn("version_conflict", result["rejection"])
        self.assertEqual(result["ground_truth"]["status"], "BLOCKED")

    def test_confirmation_authorization_and_linkage_are_source_side(self):
        source = bench.VersionedBankSource()
        with self.assertRaisesRegex(bench.WriteRejected, "confirmation_required"):
            source.freeze_card(
                "card-1", expected_version=1, confirmed=False,
                authorized_customer="synthetic-customer-1", idempotency_key="a"
            )
        with self.assertRaisesRegex(bench.WriteRejected, "authorization_failed"):
            source.freeze_card(
                "card-1", expected_version=1, confirmed=True,
                authorized_customer="other", idempotency_key="b"
            )

    def test_idempotent_retry_does_not_mutate_twice(self):
        source = bench.VersionedBankSource()
        first = source.freeze_card(
            "card-1", expected_version=1, confirmed=True,
            authorized_customer="synthetic-customer-1", idempotency_key="same"
        )
        second = source.freeze_card(
            "card-1", expected_version=1, confirmed=True,
            authorized_customer="synthetic-customer-1", idempotency_key="same"
        )
        self.assertEqual(first, second)
        self.assertEqual(source.version, 2)
        self.assertEqual(len(source.events), 1)


class ReferenceScoringContractTests(unittest.TestCase):
    def setUp(self):
        self.gold = [{"name": "block_card", "arguments": {"card_id": "c1", "reason": "lost"}}]
        self.writes = {"block_card", "toggle_card_freeze"}

    def test_exact_reference_action_passes(self):
        score = bench.score_reference_actions(self.gold, list(self.gold), self.writes)
        self.assertEqual(score["action_reward"], 1.0)

    def test_extra_read_is_allowed_but_extra_write_fails(self):
        read = {"name": "get_card_details", "arguments": {"card_ids": ["c1"]}}
        extra_write = {"name": "toggle_card_freeze", "arguments": {"card_id": "c1", "state": "freeze"}}
        self.assertEqual(bench.score_reference_actions(self.gold, [read, *self.gold], self.writes)["action_reward"], 1.0)
        self.assertEqual(bench.score_reference_actions(self.gold, [*self.gold, extra_write], self.writes)["action_reward"], 0.0)


if __name__ == "__main__":
    unittest.main()
