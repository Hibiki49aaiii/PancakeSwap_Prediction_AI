from __future__ import annotations

from dataclasses import dataclass

from .contracts import market
from .events import PredictionEvent, decode_prediction_log
from .snapshot import (
    CanonicalSnapshot,
    SnapshotLog,
    canonical_logs,
    raw_event_export_hash,
)
from .store import EventStore


class ReplayInvariantError(RuntimeError):
    pass


@dataclass(slots=True)
class _MutableRound:
    epoch: int
    start_block: int | None = None
    lock_block: int | None = None
    end_block: int | None = None
    lock_oracle_id: int | None = None
    close_oracle_id: int | None = None
    lock_price: int | None = None
    close_price: int | None = None
    bull_amount_wei: int = 0
    bear_amount_wei: int = 0
    bet_count: int = 0
    reward_base_cal_amount: int | None = None
    reward_amount: int | None = None
    treasury_amount: int | None = None


@dataclass(frozen=True, slots=True)
class ReplayedRound:
    epoch: int
    start_block: int | None
    lock_block: int | None
    end_block: int | None
    lock_oracle_id: int | None
    close_oracle_id: int | None
    lock_price: int | None
    close_price: int | None
    bull_amount_wei: int
    bear_amount_wei: int
    bet_count: int
    reward_base_cal_amount: int | None
    reward_amount: int | None
    treasury_amount: int | None

    @property
    def total_amount_wei(self) -> int:
        return self.bull_amount_wei + self.bear_amount_wei

    @property
    def outcome(self) -> str | None:
        if self.lock_price is None or self.close_price is None:
            return None
        if self.close_price > self.lock_price:
            return "bull"
        if self.close_price < self.lock_price:
            return "bear"
        return "house"


@dataclass(frozen=True, slots=True)
class ReplayReport:
    rounds: tuple[ReplayedRound, ...]
    recognized_events: int
    ignored_logs: int


def _round(rounds: dict[int, _MutableRound], epoch: int) -> _MutableRound:
    if epoch not in rounds:
        rounds[epoch] = _MutableRound(epoch=epoch)
    return rounds[epoch]


def _apply_event(rounds: dict[int, _MutableRound], event: PredictionEvent, *, strict: bool) -> None:
    round_ = _round(rounds, event.epoch)
    if event.name == "StartRound":
        if round_.start_block is not None:
            raise ReplayInvariantError(f"duplicate StartRound for epoch {event.epoch}")
        if strict and (round_.lock_block is not None or round_.end_block is not None):
            raise ReplayInvariantError(f"late StartRound for epoch {event.epoch}")
        round_.start_block = event.block_number
        return

    if event.name in {"BetBull", "BetBear"}:
        if event.amount_wei is None or event.amount_wei <= 0:
            raise ReplayInvariantError(f"invalid bet amount for epoch {event.epoch}")
        if strict and round_.start_block is None:
            raise ReplayInvariantError(f"bet before observed StartRound for epoch {event.epoch}")
        if round_.lock_block is not None or round_.end_block is not None:
            raise ReplayInvariantError(f"bet after lock for epoch {event.epoch}")
        if event.name == "BetBull":
            round_.bull_amount_wei += event.amount_wei
        else:
            round_.bear_amount_wei += event.amount_wei
        round_.bet_count += 1
        return

    if event.name == "LockRound":
        if event.oracle_round_id is None or event.price is None:
            raise ReplayInvariantError("LockRound missing decoded oracle data")
        if round_.lock_block is not None:
            raise ReplayInvariantError(f"duplicate LockRound for epoch {event.epoch}")
        if strict and round_.start_block is None:
            raise ReplayInvariantError(f"LockRound before observed StartRound for epoch {event.epoch}")
        if round_.end_block is not None:
            raise ReplayInvariantError(f"LockRound after EndRound for epoch {event.epoch}")
        round_.lock_block = event.block_number
        round_.lock_oracle_id = event.oracle_round_id
        round_.lock_price = event.price
        return

    if event.name == "EndRound":
        if event.oracle_round_id is None or event.price is None:
            raise ReplayInvariantError("EndRound missing decoded oracle data")
        if round_.end_block is not None:
            raise ReplayInvariantError(f"duplicate EndRound for epoch {event.epoch}")
        if strict and round_.lock_block is None:
            raise ReplayInvariantError(f"EndRound before observed LockRound for epoch {event.epoch}")
        round_.end_block = event.block_number
        round_.close_oracle_id = event.oracle_round_id
        round_.close_price = event.price
        return

    if event.name == "RewardsCalculated":
        if round_.reward_base_cal_amount is not None:
            raise ReplayInvariantError(f"duplicate RewardsCalculated for epoch {event.epoch}")
        if strict and round_.end_block is None:
            raise ReplayInvariantError(
                f"RewardsCalculated before observed EndRound for epoch {event.epoch}"
            )
        assert event.reward_base_cal_amount is not None
        assert event.reward_amount is not None
        assert event.treasury_amount is not None
        round_.reward_base_cal_amount = event.reward_base_cal_amount
        round_.reward_amount = event.reward_amount
        round_.treasury_amount = event.treasury_amount
        if strict and round_.lock_price is not None and round_.close_price is not None:
            if round_.close_price > round_.lock_price:
                expected_base = round_.bull_amount_wei
            elif round_.close_price < round_.lock_price:
                expected_base = round_.bear_amount_wei
            else:
                expected_base = 0
            if event.reward_base_cal_amount != expected_base:
                raise ReplayInvariantError(
                    f"reward base mismatch for epoch {event.epoch}: "
                    f"event={event.reward_base_cal_amount} replay={expected_base}"
                )
        return

    raise ReplayInvariantError(f"unsupported replay event {event.name}")


def replay_prediction_logs(
    logs: tuple[SnapshotLog, ...],
    *,
    strict_lifecycle: bool = True,
) -> ReplayReport:
    ordered = tuple(sorted(logs, key=lambda log: (log.block_number, log.tx_index, log.log_index)))
    if ordered != logs:
        raise ReplayInvariantError("snapshot logs must already be in deterministic chain order")

    rounds: dict[int, _MutableRound] = {}
    recognized = 0
    ignored = 0
    for log in logs:
        event = decode_prediction_log(log)
        if event is None:
            ignored += 1
            continue
        recognized += 1
        _apply_event(rounds, event, strict=strict_lifecycle)

    frozen = tuple(
        ReplayedRound(
            epoch=round_.epoch,
            start_block=round_.start_block,
            lock_block=round_.lock_block,
            end_block=round_.end_block,
            lock_oracle_id=round_.lock_oracle_id,
            close_oracle_id=round_.close_oracle_id,
            lock_price=round_.lock_price,
            close_price=round_.close_price,
            bull_amount_wei=round_.bull_amount_wei,
            bear_amount_wei=round_.bear_amount_wei,
            bet_count=round_.bet_count,
            reward_base_cal_amount=round_.reward_base_cal_amount,
            reward_amount=round_.reward_amount,
            treasury_amount=round_.treasury_amount,
        )
        for _, round_ in sorted(rounds.items())
    )
    return ReplayReport(rounds=frozen, recognized_events=recognized, ignored_logs=ignored)


@dataclass(frozen=True, slots=True)
class ReplayArtifact:
    market: str
    snapshot_hash: str
    raw_event_export_hash: str
    replay: ReplayReport


def replay_canonical_snapshot(
    store: EventStore,
    snapshot: CanonicalSnapshot,
    *,
    market_symbol: str,
    strict_lifecycle: bool = True,
) -> ReplayArtifact:
    config = market(market_symbol)
    logs = canonical_logs(store, snapshot, address=config.address)
    export_hash = raw_event_export_hash(snapshot, logs)
    report = replay_prediction_logs(logs, strict_lifecycle=strict_lifecycle)
    return ReplayArtifact(
        market=config.symbol,
        snapshot_hash=snapshot.snapshot_hash,
        raw_event_export_hash=export_hash,
        replay=report,
    )
