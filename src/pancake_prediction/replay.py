from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path

REPLAY_FORMAT_VERSION = 1
ANALYTIC_EVENT_NAMES = (
    "StartRound",
    "BetBull",
    "BetBear",
    "LockRound",
    "EndRound",
    "RewardsCalculated",
    "NewTreasuryFee",
    "NewBufferAndIntervalSeconds",
)


@dataclass(frozen=True, slots=True)
class ChainEvent:
    block_number: int
    block_hash: str
    block_timestamp: int
    tx_hash: str
    tx_index: int
    log_index: int
    event_name: str
    decoded: dict[str, object]

    def identity_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(slots=True)
class _RoundBuilder:
    epoch: int
    start_block: int | None = None
    start_timestamp: int | None = None
    lock_block: int | None = None
    lock_timestamp: int | None = None
    lock_round_id: int | None = None
    lock_price: int | None = None
    end_block: int | None = None
    end_timestamp: int | None = None
    close_round_id: int | None = None
    close_price: int | None = None
    bull_amount_wei: int = 0
    bear_amount_wei: int = 0
    bet_count: int = 0
    reward_base_cal_amount_wei: int | None = None
    reward_amount_wei: int | None = None
    treasury_amount_wei: int | None = None
    issues: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class RoundRecord:
    epoch: int
    start_block: int | None
    start_timestamp: int | None
    lock_block: int | None
    lock_timestamp: int | None
    lock_round_id: int | None
    lock_price: int | None
    end_block: int | None
    end_timestamp: int | None
    close_round_id: int | None
    close_price: int | None
    bull_amount_wei: int
    bear_amount_wei: int
    total_amount_wei: int
    bet_count: int
    reward_base_cal_amount_wei: int | None
    reward_amount_wei: int | None
    treasury_amount_wei: int | None
    label: str
    issues: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReplaySnapshot:
    format_version: int
    market: str
    input_digest: str
    rounds: tuple[RoundRecord, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "format_version": self.format_version,
            "market": self.market,
            "input_digest": self.input_digest,
            "rounds": [asdict(record) for record in self.rounds],
        }

    def to_bytes(self) -> bytes:
        return (
            json.dumps(
                self.as_dict(),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            )
            + "\n"
        ).encode()

    @property
    def output_digest(self) -> str:
        return hashlib.sha256(self.to_bytes()).hexdigest()


def canonical_prediction_events(path: Path, market: str) -> tuple[ChainEvent, ...]:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT e.block_number,e.block_hash,b.timestamp AS block_timestamp,
                   e.tx_hash,e.tx_index,e.log_index,e.event_name,e.decoded_json
            FROM events e
            JOIN blocks b
              ON b.chain_id=e.chain_id
             AND b.number=e.block_number
             AND b.hash=e.block_hash
             AND b.canonical=1
            WHERE e.market=? AND e.source='prediction'
              AND e.event_name IN (?,?,?,?,?,?,?,?)
            ORDER BY e.block_number,e.tx_index,e.log_index,e.tx_hash
            """,
            (market, *ANALYTIC_EVENT_NAMES),
        ).fetchall()
    finally:
        connection.close()
    result: list[ChainEvent] = []
    for row in rows:
        decoded: dict[str, object] = {}
        if row["decoded_json"]:
            parsed = json.loads(str(row["decoded_json"]))
            if isinstance(parsed, dict):
                decoded = {str(key): value for key, value in parsed.items()}
        result.append(
            ChainEvent(
                block_number=int(row["block_number"]),
                block_hash=str(row["block_hash"]),
                block_timestamp=int(row["block_timestamp"]),
                tx_hash=str(row["tx_hash"]),
                tx_index=int(row["tx_index"]),
                log_index=int(row["log_index"]),
                event_name=str(row["event_name"]),
                decoded=decoded,
            )
        )
    return tuple(result)


def _input_digest(events: Iterable[ChainEvent]) -> str:
    digest = hashlib.sha256()
    for event in events:
        payload = json.dumps(
            event.identity_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode()
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def _epoch(event: ChainEvent) -> int | None:
    value = event.decoded.get("epoch")
    return value if isinstance(value, int) else None


def _set_once(builder: _RoundBuilder, field_name: str, value: int, issue: str) -> None:
    current = getattr(builder, field_name)
    if current is None:
        setattr(builder, field_name, value)
    elif current != value and issue not in builder.issues:
        builder.issues.append(issue)


def _label(builder: _RoundBuilder) -> str:
    if builder.lock_price is None or builder.close_price is None:
        return "unresolved"
    if builder.close_price > builder.lock_price:
        return "bull"
    if builder.close_price < builder.lock_price:
        return "bear"
    return "tie"


def replay_events(market: str, events: Iterable[ChainEvent]) -> ReplaySnapshot:
    ordered = tuple(
        sorted(
            events,
            key=lambda event: (
                event.block_number,
                event.tx_index,
                event.log_index,
                event.tx_hash,
            ),
        )
    )
    builders: dict[int, _RoundBuilder] = {}
    for event in ordered:
        epoch = _epoch(event)
        if epoch is None:
            continue
        builder = builders.setdefault(epoch, _RoundBuilder(epoch=epoch))
        if event.event_name == "StartRound":
            _set_once(builder, "start_block", event.block_number, "multiple_start_blocks")
            _set_once(
                builder, "start_timestamp", event.block_timestamp, "multiple_start_timestamps"
            )
        elif event.event_name == "LockRound":
            _set_once(builder, "lock_block", event.block_number, "multiple_lock_blocks")
            _set_once(builder, "lock_timestamp", event.block_timestamp, "multiple_lock_timestamps")
            round_id = event.decoded.get("roundId")
            price = event.decoded.get("price")
            if isinstance(round_id, int):
                _set_once(builder, "lock_round_id", round_id, "multiple_lock_round_ids")
            if isinstance(price, int):
                _set_once(builder, "lock_price", price, "multiple_lock_prices")
        elif event.event_name == "EndRound":
            _set_once(builder, "end_block", event.block_number, "multiple_end_blocks")
            _set_once(builder, "end_timestamp", event.block_timestamp, "multiple_end_timestamps")
            round_id = event.decoded.get("roundId")
            price = event.decoded.get("price")
            if isinstance(round_id, int):
                _set_once(builder, "close_round_id", round_id, "multiple_close_round_ids")
            if isinstance(price, int):
                _set_once(builder, "close_price", price, "multiple_close_prices")
        elif event.event_name in ("BetBull", "BetBear"):
            amount = event.decoded.get("amount")
            if isinstance(amount, int) and amount >= 0:
                if event.event_name == "BetBull":
                    builder.bull_amount_wei += amount
                else:
                    builder.bear_amount_wei += amount
                builder.bet_count += 1
            elif "invalid_bet_amount" not in builder.issues:
                builder.issues.append("invalid_bet_amount")
        elif event.event_name == "RewardsCalculated":
            fields = (
                ("reward_base_cal_amount_wei", "rewardBaseCalAmount"),
                ("reward_amount_wei", "rewardAmount"),
                ("treasury_amount_wei", "treasuryAmount"),
            )
            for target, source in fields:
                value = event.decoded.get(source)
                if isinstance(value, int):
                    _set_once(builder, target, value, f"multiple_{target}")
    records: list[RoundRecord] = []
    for epoch in sorted(builders):
        builder = builders[epoch]
        total = builder.bull_amount_wei + builder.bear_amount_wei
        issues = list(builder.issues)
        if (
            builder.start_timestamp is not None
            and builder.lock_timestamp is not None
            and builder.lock_timestamp < builder.start_timestamp
        ):
            issues.append("lock_before_start")
        if (
            builder.lock_timestamp is not None
            and builder.end_timestamp is not None
            and builder.end_timestamp < builder.lock_timestamp
        ):
            issues.append("end_before_lock")
        if builder.reward_amount_wei is not None and builder.reward_amount_wei > total:
            issues.append("reward_exceeds_observed_pool")
        records.append(
            RoundRecord(
                epoch=epoch,
                start_block=builder.start_block,
                start_timestamp=builder.start_timestamp,
                lock_block=builder.lock_block,
                lock_timestamp=builder.lock_timestamp,
                lock_round_id=builder.lock_round_id,
                lock_price=builder.lock_price,
                end_block=builder.end_block,
                end_timestamp=builder.end_timestamp,
                close_round_id=builder.close_round_id,
                close_price=builder.close_price,
                bull_amount_wei=builder.bull_amount_wei,
                bear_amount_wei=builder.bear_amount_wei,
                total_amount_wei=total,
                bet_count=builder.bet_count,
                reward_base_cal_amount_wei=builder.reward_base_cal_amount_wei,
                reward_amount_wei=builder.reward_amount_wei,
                treasury_amount_wei=builder.treasury_amount_wei,
                label=_label(builder),
                issues=tuple(sorted(set(issues))),
            )
        )
    return ReplaySnapshot(
        format_version=REPLAY_FORMAT_VERSION,
        market=market,
        input_digest=_input_digest(ordered),
        rounds=tuple(records),
    )


def build_replay_snapshot(path: Path, market: str) -> ReplaySnapshot:
    return replay_events(market, canonical_prediction_events(path, market))
