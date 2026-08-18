from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum


class IntentState(StrEnum):
    PLANNED = "planned"
    RESERVED = "reserved"
    SUBMITTED = "submitted"
    MINED = "mined"
    FINALIZED = "finalized"
    REPLACED = "replaced"
    CANCELLED = "cancelled"
    FAILED = "failed"
    UNKNOWN = "unknown"


TERMINAL_STATES = frozenset(
    {
        IntentState.FINALIZED,
        IntentState.CANCELLED,
        IntentState.FAILED,
    }
)


_ALLOWED: dict[IntentState, frozenset[IntentState]] = {
    IntentState.PLANNED: frozenset({IntentState.RESERVED, IntentState.CANCELLED}),
    IntentState.RESERVED: frozenset({IntentState.SUBMITTED, IntentState.CANCELLED, IntentState.FAILED}),
    IntentState.SUBMITTED: frozenset({IntentState.MINED, IntentState.REPLACED, IntentState.UNKNOWN, IntentState.FAILED}),
    IntentState.MINED: frozenset({IntentState.FINALIZED, IntentState.UNKNOWN}),
    IntentState.REPLACED: frozenset({IntentState.SUBMITTED, IntentState.MINED, IntentState.UNKNOWN, IntentState.FAILED}),
    IntentState.UNKNOWN: frozenset({IntentState.SUBMITTED, IntentState.MINED, IntentState.REPLACED, IntentState.FAILED}),
    IntentState.FINALIZED: frozenset(),
    IntentState.CANCELLED: frozenset(),
    IntentState.FAILED: frozenset(),
}


@dataclass(frozen=True, slots=True)
class ExecutionIntent:
    intent_id: str
    round_id: int
    side: str
    amount_wei: int
    nonce: int | None = None
    tx_hash: str | None = None
    replacement_tx_hash: str | None = None
    state: IntentState = IntentState.PLANNED
    observed_block: int | None = None
    confirmations: int = 0

    def transition(self, target: IntentState, **changes: object) -> "ExecutionIntent":
        if target not in _ALLOWED[self.state]:
            raise ValueError(f"illegal transition: {self.state} -> {target}")
        updated = replace(self, state=target, **changes)
        updated.validate()
        return updated

    def validate(self) -> None:
        if not self.intent_id:
            raise ValueError("intent_id is required")
        if self.round_id < 0:
            raise ValueError("round_id must be non-negative")
        if self.side not in {"BULL", "BEAR"}:
            raise ValueError("side must be BULL or BEAR")
        if self.amount_wei <= 0:
            raise ValueError("amount_wei must be positive")
        if self.nonce is not None and self.nonce < 0:
            raise ValueError("nonce must be non-negative")
        if self.confirmations < 0:
            raise ValueError("confirmations must be non-negative")
        if self.state in {IntentState.SUBMITTED, IntentState.MINED, IntentState.FINALIZED} and not self.tx_hash:
            raise ValueError(f"{self.state} requires tx_hash")
        if self.state is IntentState.REPLACED and not self.replacement_tx_hash:
            raise ValueError("replaced state requires replacement_tx_hash")
        if self.state in {IntentState.MINED, IntentState.FINALIZED} and self.observed_block is None:
            raise ValueError(f"{self.state} requires observed_block")

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    @property
    def unresolved(self) -> bool:
        return not self.terminal


def reconcile_receipt(
    intent: ExecutionIntent,
    *,
    receipt_present: bool,
    canonical_block: bool,
    block_number: int | None,
    confirmations: int,
    required_confirmations: int,
) -> ExecutionIntent:
    if intent.state not in {IntentState.SUBMITTED, IntentState.MINED, IntentState.UNKNOWN, IntentState.REPLACED}:
        raise ValueError("receipt reconciliation requires an in-flight intent")
    if required_confirmations < 1:
        raise ValueError("required_confirmations must be >= 1")

    if not receipt_present or not canonical_block:
        if intent.state is IntentState.UNKNOWN:
            return intent
        return intent.transition(IntentState.UNKNOWN, observed_block=None, confirmations=0)

    if block_number is None:
        raise ValueError("receipt block number required when receipt is present")

    active_hash = intent.replacement_tx_hash if intent.state is IntentState.REPLACED else intent.tx_hash
    mined = replace(
        intent,
        state=IntentState.MINED,
        tx_hash=active_hash,
        observed_block=block_number,
        confirmations=confirmations,
    )
    mined.validate()
    if confirmations >= required_confirmations:
        finalized = replace(mined, state=IntentState.FINALIZED)
        finalized.validate()
        return finalized
    return mined
