from __future__ import annotations

import sqlite3
from collections import Counter
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from .execution_intent import IntentState

RESOLVED_STATES = {IntentState.FINALIZED, IntentState.FAILED}


@dataclass(frozen=True, slots=True)
class ExecutionIntentReport:
    total: int
    resolved: int
    unresolved: int
    unresolved_ids: tuple[int, ...]
    state_counts: tuple[tuple[str, int], ...]

    @property
    def gate_ready(self) -> bool:
        return self.total > 0 and self.unresolved == 0

    def as_dict(self) -> dict[str, object]:
        return {
            "total": self.total,
            "resolved": self.resolved,
            "unresolved": self.unresolved,
            "unresolved_ids": list(self.unresolved_ids),
            "state_counts": dict(self.state_counts),
            "gate_ready": self.gate_ready,
        }


def build_execution_intent_report(path: Path) -> ExecutionIntentReport:
    if not path.exists():
        raise FileNotFoundError(path)
    with closing(sqlite3.connect(path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id,state FROM execution_intents ORDER BY id"
        ).fetchall()

    counts: Counter[str] = Counter()
    unresolved_ids: list[int] = []
    resolved = 0
    for row in rows:
        state = IntentState(str(row["state"]))
        counts[state.value] += 1
        if state in RESOLVED_STATES:
            resolved += 1
        else:
            unresolved_ids.append(int(row["id"]))

    return ExecutionIntentReport(
        total=len(rows),
        resolved=resolved,
        unresolved=len(unresolved_ids),
        unresolved_ids=tuple(unresolved_ids),
        state_counts=tuple(sorted(counts.items())),
    )