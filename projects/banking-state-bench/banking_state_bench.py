"""No-API banking operational-state contract and dynamic smoke test.

This is a custom extension, not an upstream TauIndianBankBench result.
It deliberately models source-side validation at write time.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from typing import Any


class WriteRejected(RuntimeError):
    """A source-side safety precondition rejected a mutation."""


def canonical_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


@dataclass(frozen=True)
class ReadResult:
    version: int
    state: dict[str, Any]


class VersionedBankSource:
    def __init__(self) -> None:
        self.version = 1
        self.state = {
            "customer_id": "synthetic-customer-1",
            "authenticated": True,
            "cards": {"card-1": {"linked": True, "status": "ACTIVE"}},
        }
        self.events: list[dict[str, Any]] = []
        self._idempotency: dict[str, dict[str, Any]] = {}

    def read(self) -> ReadResult:
        return ReadResult(self.version, copy.deepcopy(self.state))

    def external_block(self, card_id: str) -> None:
        card = self.state["cards"][card_id]
        card["status"] = "BLOCKED"
        self.version += 1
        self.events.append(
            {"event": "external_block", "card_id": card_id, "version": self.version}
        )

    def freeze_card(
        self,
        card_id: str,
        *,
        expected_version: int,
        confirmed: bool,
        authorized_customer: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        if idempotency_key in self._idempotency:
            return copy.deepcopy(self._idempotency[idempotency_key])
        if not self.state["authenticated"]:
            raise WriteRejected("authentication_required")
        if authorized_customer != self.state["customer_id"]:
            raise WriteRejected("authorization_failed")
        if not confirmed:
            raise WriteRejected("confirmation_required")
        if expected_version != self.version:
            raise WriteRejected(
                f"version_conflict: expected={expected_version}, current={self.version}"
            )
        card = self.state["cards"].get(card_id)
        if not card or not card["linked"]:
            raise WriteRejected("card_not_linked")
        if card["status"] == "BLOCKED":
            raise WriteRejected("blocked_card_cannot_be_frozen")
        card["status"] = "FROZEN"
        self.version += 1
        result = {"status": "FROZEN", "card_id": card_id, "version": self.version}
        self._idempotency[idempotency_key] = result
        self.events.append({"event": "freeze_card", **result})
        return copy.deepcopy(result)


class ReadAdapter:
    """Read-only adapter; periodic mode holds a snapshot, current mode re-reads."""

    def __init__(self, source: VersionedBankSource, *, periodic: bool) -> None:
        self.source = source
        self.periodic = periodic
        self._snapshot = source.read() if periodic else None

    def get_card(self, card_id: str) -> tuple[int, dict[str, Any]]:
        result = self._snapshot if self.periodic else self.source.read()
        assert result is not None
        return result.version, copy.deepcopy(result.state["cards"][card_id])


def score_reference_actions(
    gold_actions: list[dict[str, Any]],
    predicted_actions: list[dict[str, Any]],
    write_tools: set[str],
) -> dict[str, Any]:
    """Mirror upstream ACTION coverage plus unexpected-WRITE purity semantics."""

    def key(action: dict[str, Any]) -> tuple[str, str]:
        return action["name"], json.dumps(action.get("arguments", {}), sort_keys=True)

    gold_keys = {key(action) for action in gold_actions}
    missing = [action for action in gold_actions if key(action) not in {key(p) for p in predicted_actions}]
    unexpected_writes = [
        action
        for action in predicted_actions
        if action["name"] in write_tools and key(action) not in gold_keys
    ]
    return {
        "action_reward": 1.0 if not missing and not unexpected_writes else 0.0,
        "missing": missing,
        "unexpected_writes": unexpected_writes,
    }


def dynamic_smoke() -> dict[str, Any]:
    source = VersionedBankSource()
    stale = ReadAdapter(source, periodic=True)
    current = ReadAdapter(source, periodic=False)
    stale_v1, stale_before = stale.get_card("card-1")
    source.external_block("card-1")
    stale_v2, stale_after = stale.get_card("card-1")
    current_v, current_after = current.get_card("card-1")
    rejection = None
    try:
        source.freeze_card(
            "card-1",
            expected_version=stale_v2,
            confirmed=True,
            authorized_customer="synthetic-customer-1",
            idempotency_key="smoke-freeze-1",
        )
    except WriteRejected as exc:
        rejection = str(exc)
    return {
        "label": "CUSTOM_DYNAMIC_EXTENSION_NOT_UPSTREAM_SCORE",
        "stale_read": {"version": stale_v2, "state": stale_after},
        "current_read": {"version": current_v, "state": current_after},
        "initial_read": {"version": stale_v1, "state": stale_before},
        "intended_action": "freeze_card",
        "accepted": False,
        "rejection": rejection,
        "ground_truth": source.read().state["cards"]["card-1"],
        "events": source.events,
    }


if __name__ == "__main__":
    print(json.dumps(dynamic_smoke(), indent=2, sort_keys=True))
