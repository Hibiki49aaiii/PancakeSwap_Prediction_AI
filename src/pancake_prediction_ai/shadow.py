from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .economics import PoolState, Side
from .evidence_gate import Evidence, EvidenceKind, EvidenceOrigin


@dataclass(frozen=True, slots=True)
class ShadowDecision:
    round_id: int
    decision_cutoff_ns: int
    probability_bull: float
    side: Side
    stake_wei: int
    snapshot_bull_wei: int
    snapshot_bear_wei: int
    treasury_fee_ppm: int
    gas_cost_wei: int
    expected_pnl_wei: float
    model_version: str

    def validate(self) -> None:
        if self.round_id < 0 or self.decision_cutoff_ns < 0:
            raise ValueError("round/timestamp must be non-negative")
        if not 0.0 <= self.probability_bull <= 1.0:
            raise ValueError("probability_bull must be in [0, 1]")
        if self.stake_wei <= 0:
            raise ValueError("stake_wei must be positive")
        if self.snapshot_bull_wei < 0 or self.snapshot_bear_wei < 0:
            raise ValueError("snapshot pools must be non-negative")
        if not 0 <= self.treasury_fee_ppm < 1_000_000:
            raise ValueError("treasury_fee_ppm invalid")
        if self.gas_cost_wei < 0:
            raise ValueError("gas_cost_wei must be non-negative")
        if not self.model_version:
            raise ValueError("model_version is required")


@dataclass(frozen=True, slots=True)
class ShadowSettlement:
    round_id: int
    outcome: Side
    final_bull_wei: int
    final_bear_wei: int
    settled_at_ns: int
    simulated_pnl_wei: float


@dataclass(frozen=True, slots=True)
class ShadowSummary:
    resolved_rounds: int
    net_pnl_wei: float
    max_drawdown_wei: float
    brier_score: float
    accuracy: float
    average_expected_pnl_wei: float


@dataclass(frozen=True, slots=True)
class ShadowPolicy:
    min_resolved_rounds: int
    min_net_pnl_wei: float
    max_drawdown_wei: float
    max_brier_score: float
    min_average_expected_pnl_wei: float = 0.0

    def validate(self) -> None:
        if self.min_resolved_rounds <= 0:
            raise ValueError("min_resolved_rounds must be positive")
        if self.max_drawdown_wei < 0:
            raise ValueError("max_drawdown_wei must be non-negative")
        if not 0.0 <= self.max_brier_score <= 1.0:
            raise ValueError("max_brier_score must be in [0, 1]")


class ShadowLedger:
    def __init__(self, path: str | Path) -> None:
        self._conn = sqlite3.connect(str(path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS shadow_decisions (
                round_id INTEGER PRIMARY KEY,
                decision_cutoff_ns INTEGER NOT NULL,
                probability_bull REAL NOT NULL,
                side TEXT NOT NULL,
                stake_wei INTEGER NOT NULL,
                snapshot_bull_wei INTEGER NOT NULL,
                snapshot_bear_wei INTEGER NOT NULL,
                treasury_fee_ppm INTEGER NOT NULL,
                gas_cost_wei INTEGER NOT NULL,
                expected_pnl_wei REAL NOT NULL,
                model_version TEXT NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS shadow_settlements (
                round_id INTEGER PRIMARY KEY,
                outcome TEXT NOT NULL,
                final_bull_wei INTEGER NOT NULL,
                final_bear_wei INTEGER NOT NULL,
                settled_at_ns INTEGER NOT NULL,
                simulated_pnl_wei REAL NOT NULL,
                FOREIGN KEY(round_id) REFERENCES shadow_decisions(round_id)
            )
            """
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "ShadowLedger":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    def record_decision(self, decision: ShadowDecision) -> None:
        decision.validate()
        try:
            self._conn.execute(
                """
                INSERT INTO shadow_decisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision.round_id,
                    decision.decision_cutoff_ns,
                    decision.probability_bull,
                    decision.side.value,
                    decision.stake_wei,
                    decision.snapshot_bull_wei,
                    decision.snapshot_bear_wei,
                    decision.treasury_fee_ppm,
                    decision.gas_cost_wei,
                    decision.expected_pnl_wei,
                    decision.model_version,
                ),
            )
            self._conn.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"decision already recorded for round {decision.round_id}") from exc

    def resolve_round(
        self,
        *,
        round_id: int,
        outcome: Side,
        final_bull_wei: int,
        final_bear_wei: int,
        settled_at_ns: int,
    ) -> ShadowSettlement:
        if final_bull_wei < 0 or final_bear_wei < 0 or settled_at_ns < 0:
            raise ValueError("settlement values must be non-negative")
        row = self._conn.execute(
            """
            SELECT decision_cutoff_ns, side, stake_wei, treasury_fee_ppm, gas_cost_wei
            FROM shadow_decisions WHERE round_id = ?
            """,
            (round_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"missing shadow decision for round {round_id}")
        decision_cutoff_ns, side_text, stake_wei, fee_ppm, gas_cost_wei = row
        if settled_at_ns <= int(decision_cutoff_ns):
            raise ValueError("settlement must occur after decision cutoff")

        side = Side(str(side_text))
        stake = int(stake_wei)
        gas = int(gas_cost_wei)
        fee = int(fee_ppm)
        base_pool = PoolState(final_bull_wei, final_bear_wei, fee)
        base_pool.validate()

        simulated_bull = final_bull_wei + (stake if side is Side.BULL else 0)
        simulated_bear = final_bear_wei + (stake if side is Side.BEAR else 0)
        total = simulated_bull + simulated_bear
        winning_pool = simulated_bull if outcome is Side.BULL else simulated_bear
        won = side is outcome
        if won:
            distributable = total * (1_000_000 - fee) / 1_000_000
            gross = stake / winning_pool * distributable
            pnl = gross - stake - gas
        else:
            pnl = -stake - gas

        settlement = ShadowSettlement(
            round_id=round_id,
            outcome=outcome,
            final_bull_wei=final_bull_wei,
            final_bear_wei=final_bear_wei,
            settled_at_ns=settled_at_ns,
            simulated_pnl_wei=pnl,
        )
        try:
            self._conn.execute(
                "INSERT INTO shadow_settlements VALUES (?, ?, ?, ?, ?, ?)",
                (
                    round_id,
                    outcome.value,
                    final_bull_wei,
                    final_bear_wei,
                    settled_at_ns,
                    pnl,
                ),
            )
            self._conn.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"round {round_id} already settled") from exc
        return settlement

    def summary(self) -> ShadowSummary:
        rows = self._conn.execute(
            """
            SELECT d.probability_bull, d.side, d.expected_pnl_wei,
                   s.outcome, s.simulated_pnl_wei
            FROM shadow_decisions d
            JOIN shadow_settlements s USING(round_id)
            ORDER BY d.round_id ASC
            """
        ).fetchall()
        if not rows:
            return ShadowSummary(0, 0.0, 0.0, 0.0, 0.0, 0.0)

        pnl_values = [float(row[4]) for row in rows]
        net = sum(pnl_values)
        equity = 0.0
        peak = 0.0
        max_drawdown = 0.0
        correct = 0
        brier_sum = 0.0
        expected_sum = 0.0
        for probability_bull, side_text, expected_pnl, outcome_text, pnl in rows:
            equity += float(pnl)
            peak = max(peak, equity)
            max_drawdown = max(max_drawdown, peak - equity)
            y = 1.0 if Side(str(outcome_text)) is Side.BULL else 0.0
            p = float(probability_bull)
            brier_sum += (p - y) ** 2
            if Side(str(side_text)) is Side(str(outcome_text)):
                correct += 1
            expected_sum += float(expected_pnl)

        n = len(rows)
        return ShadowSummary(
            resolved_rounds=n,
            net_pnl_wei=net,
            max_drawdown_wei=max_drawdown,
            brier_score=brier_sum / n,
            accuracy=correct / n,
            average_expected_pnl_wei=expected_sum / n,
        )


def make_shadow_evidence(
    summary: ShadowSummary,
    *,
    policy: ShadowPolicy,
    recorded_at: str | None = None,
) -> Evidence:
    policy.validate()
    passed = (
        summary.resolved_rounds >= policy.min_resolved_rounds
        and summary.net_pnl_wei >= policy.min_net_pnl_wei
        and summary.max_drawdown_wei <= policy.max_drawdown_wei
        and summary.brier_score <= policy.max_brier_score
        and summary.average_expected_pnl_wei >= policy.min_average_expected_pnl_wei
    )
    payload = {
        "summary": {
            "resolved_rounds": summary.resolved_rounds,
            "net_pnl_wei": summary.net_pnl_wei,
            "max_drawdown_wei": summary.max_drawdown_wei,
            "brier_score": summary.brier_score,
            "accuracy": summary.accuracy,
            "average_expected_pnl_wei": summary.average_expected_pnl_wei,
        },
        "policy": {
            "min_resolved_rounds": policy.min_resolved_rounds,
            "min_net_pnl_wei": policy.min_net_pnl_wei,
            "max_drawdown_wei": policy.max_drawdown_wei,
            "max_brier_score": policy.max_brier_score,
            "min_average_expected_pnl_wei": policy.min_average_expected_pnl_wei,
        },
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    digest = hashlib.sha256(canonical).hexdigest()
    return Evidence(
        kind=EvidenceKind.SHADOW_ECONOMICS,
        origin=EvidenceOrigin.OBSERVED,
        passed=passed,
        artifact_sha256=digest,
        recorded_at=recorded_at or datetime.now(timezone.utc).isoformat(),
        payload=payload,
    )
