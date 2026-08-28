from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import cast

from .research_ledger import ResearchPredictionRecord, validate_research_prediction

ZERO_DIGEST = "0" * 64
_OUTCOMES = {"bull", "bear", "tie"}


@dataclass(frozen=True, slots=True)
class ShadowSettlementRecord:
    market: str
    epoch: int
    settled_timestamp_ms: int
    outcome: str
    result_source_digest: str
    realized_pnl_wei: int | None = None
    metadata: Mapping[str, object] | None = None

    def canonical_payload(self) -> dict[str, object]:
        payload = asdict(self)
        if payload.get("metadata") is None:
            payload["metadata"] = {}
        return payload


@dataclass(frozen=True, slots=True)
class ShadowLedgerEvent:
    sequence: int
    kind: str
    market: str
    epoch: int
    payload: dict[str, object]
    previous_digest: str
    event_digest: str

    def as_dict(self) -> dict[str, object]:
        return {
            "sequence": self.sequence,
            "kind": self.kind,
            "market": self.market,
            "epoch": self.epoch,
            "payload": self.payload,
            "previous_digest": self.previous_digest,
            "event_digest": self.event_digest,
        }


@dataclass(frozen=True, slots=True)
class ShadowLedgerAuditReport:
    event_count: int
    head_digest: str
    prediction_count: int
    settlement_count: int
    unresolved_count: int
    actionable_prediction_count: int
    settled_actionable_count: int
    probability_scored_count: int
    brier_score: float | None
    directional_accuracy: float | None
    observed_pnl_count: int
    observed_pnl_wei: int
    first_decision_timestamp_ms: int | None
    last_decision_timestamp_ms: int | None
    markets: tuple[str, ...]
    model_ids: tuple[str, ...]
    feature_set_ids: tuple[str, ...]
    action_counts: dict[str, int]
    integrity_errors: tuple[str, ...]

    @property
    def integrity_ready(self) -> bool:
        return not self.integrity_errors

    @property
    def decision_span_ms(self) -> int:
        if (
            self.first_decision_timestamp_ms is None
            or self.last_decision_timestamp_ms is None
        ):
            return 0
        return max(0, self.last_decision_timestamp_ms - self.first_decision_timestamp_ms)

    def as_dict(self) -> dict[str, object]:
        return {
            "event_count": self.event_count,
            "head_digest": self.head_digest,
            "prediction_count": self.prediction_count,
            "settlement_count": self.settlement_count,
            "unresolved_count": self.unresolved_count,
            "actionable_prediction_count": self.actionable_prediction_count,
            "settled_actionable_count": self.settled_actionable_count,
            "probability_scored_count": self.probability_scored_count,
            "brier_score": self.brier_score,
            "directional_accuracy": self.directional_accuracy,
            "observed_pnl_count": self.observed_pnl_count,
            "observed_pnl_wei": self.observed_pnl_wei,
            "first_decision_timestamp_ms": self.first_decision_timestamp_ms,
            "last_decision_timestamp_ms": self.last_decision_timestamp_ms,
            "decision_span_ms": self.decision_span_ms,
            "markets": list(self.markets),
            "model_ids": list(self.model_ids),
            "feature_set_ids": list(self.feature_set_ids),
            "action_counts": dict(self.action_counts),
            "integrity_errors": list(self.integrity_errors),
            "integrity_ready": self.integrity_ready,
            "profitability_gate_eligible": False,
            "full_historical_gate_satisfied": False,
            "signing_enabled": False,
            "live_broadcast": False,
        }


def _is_digest(value: str) -> bool:
    return len(value) == 64 and all(char in "0123456789abcdef" for char in value.lower())


def _strict_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    return value


def _strict_string(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _canonical_json(payload: Mapping[str, object]) -> str:
    return json.dumps(
        dict(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _event_digest(previous_digest: str, payload_json: str) -> str:
    if not _is_digest(previous_digest):
        raise ValueError("previous_digest must be a SHA-256 hex digest")
    raw = f"{previous_digest}\n{payload_json}\n".encode()
    return hashlib.sha256(raw).hexdigest()


def validate_shadow_settlement(
    record: ShadowSettlementRecord,
    *,
    prediction: ResearchPredictionRecord | None = None,
) -> None:
    if not record.market:
        raise ValueError("settlement market must be non-empty")
    if record.epoch < 0 or record.settled_timestamp_ms < 0:
        raise ValueError("settlement epoch and timestamp must be non-negative")
    if record.outcome not in _OUTCOMES:
        raise ValueError("settlement outcome must be bull, bear, or tie")
    if not _is_digest(record.result_source_digest):
        raise ValueError("result_source_digest must be a SHA-256 hex digest")
    if prediction is None:
        return
    if record.market != prediction.market or record.epoch != prediction.epoch:
        raise ValueError("settlement identity does not match prediction")
    if record.settled_timestamp_ms < prediction.decision_timestamp_ms:
        raise ValueError("settlement timestamp precedes prediction decision timestamp")
    if prediction.action == "skip" and record.realized_pnl_wei not in (None, 0):
        raise ValueError("skip prediction cannot have non-zero realized PnL")


class ShadowLedgerStore:
    def __init__(self, path: Path):
        self.path = path

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS shadow_ledger_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL CHECK(kind IN ('prediction', 'settlement')),
                    market TEXT NOT NULL,
                    epoch INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    previous_digest TEXT NOT NULL,
                    event_digest TEXT NOT NULL UNIQUE,
                    UNIQUE(kind, market, epoch)
                );

                CREATE TABLE IF NOT EXISTS shadow_ledger_state (
                    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                    event_count INTEGER NOT NULL,
                    head_digest TEXT NOT NULL
                );

                INSERT OR IGNORE INTO shadow_ledger_state(
                    singleton,
                    event_count,
                    head_digest
                ) VALUES (1, 0, '0000000000000000000000000000000000000000000000000000000000000000');

                CREATE TRIGGER IF NOT EXISTS shadow_events_no_update
                BEFORE UPDATE ON shadow_ledger_events
                BEGIN
                    SELECT RAISE(ABORT, 'shadow ledger events are append-only');
                END;

                CREATE TRIGGER IF NOT EXISTS shadow_events_no_delete
                BEFORE DELETE ON shadow_ledger_events
                BEGIN
                    SELECT RAISE(ABORT, 'shadow ledger events are append-only');
                END;
                """
            )

    @staticmethod
    def _event_from_row(row: sqlite3.Row) -> ShadowLedgerEvent:
        payload = json.loads(str(row["payload_json"]))
        if not isinstance(payload, dict):
            raise ValueError("shadow ledger payload root must be an object")
        return ShadowLedgerEvent(
            sequence=int(row["sequence"]),
            kind=str(row["kind"]),
            market=str(row["market"]),
            epoch=int(row["epoch"]),
            payload=payload,
            previous_digest=str(row["previous_digest"]),
            event_digest=str(row["event_digest"]),
        )

    def _state(self, connection: sqlite3.Connection) -> tuple[int, str]:
        row = connection.execute(
            "SELECT event_count, head_digest FROM shadow_ledger_state WHERE singleton = 1"
        ).fetchone()
        if row is None:
            raise ValueError("shadow ledger state row is missing")
        return int(row["event_count"]), str(row["head_digest"])

    def _existing(
        self,
        connection: sqlite3.Connection,
        *,
        kind: str,
        market: str,
        epoch: int,
    ) -> sqlite3.Row | None:
        row = connection.execute(
            """
            SELECT sequence, kind, market, epoch, payload_json, previous_digest, event_digest
            FROM shadow_ledger_events
            WHERE kind = ? AND market = ? AND epoch = ?
            """,
            (kind, market, epoch),
        ).fetchone()
        return cast(sqlite3.Row | None, row)

    def _append(
        self,
        connection: sqlite3.Connection,
        *,
        kind: str,
        market: str,
        epoch: int,
        payload: Mapping[str, object],
    ) -> ShadowLedgerEvent:
        payload_json = _canonical_json(payload)
        existing = self._existing(
            connection,
            kind=kind,
            market=market,
            epoch=epoch,
        )
        if existing is not None:
            if str(existing["payload_json"]) != payload_json:
                raise ValueError(
                    f"conflicting shadow {kind} already exists for {market} epoch {epoch}"
                )
            return self._event_from_row(existing)

        event_count, head_digest = self._state(connection)
        max_row = connection.execute(
            "SELECT COALESCE(MAX(sequence), 0) AS max_sequence, COUNT(*) AS count "
            "FROM shadow_ledger_events"
        ).fetchone()
        if max_row is None:
            raise ValueError("could not inspect shadow ledger event state")
        if (
            int(max_row["max_sequence"]) != event_count
            or int(max_row["count"]) != event_count
        ):
            raise ValueError("shadow ledger state/event count mismatch before append")
        if not _is_digest(head_digest):
            raise ValueError("shadow ledger head digest is invalid")

        digest = _event_digest(head_digest, payload_json)
        cursor = connection.execute(
            """
            INSERT INTO shadow_ledger_events(
                kind,
                market,
                epoch,
                payload_json,
                previous_digest,
                event_digest
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (kind, market, epoch, payload_json, head_digest, digest),
        )
        if cursor.lastrowid is None:
            raise ValueError("shadow ledger insert did not return a row id")
        sequence = int(cursor.lastrowid)
        if sequence != event_count + 1:
            raise ValueError("shadow ledger sequence is not contiguous")
        connection.execute(
            """
            UPDATE shadow_ledger_state
            SET event_count = ?, head_digest = ?
            WHERE singleton = 1
            """,
            (sequence, digest),
        )
        row = connection.execute(
            """
            SELECT sequence, kind, market, epoch, payload_json, previous_digest, event_digest
            FROM shadow_ledger_events
            WHERE sequence = ?
            """,
            (sequence,),
        ).fetchone()
        if row is None:
            raise ValueError("appended shadow ledger event could not be reloaded")
        return self._event_from_row(row)

    def append_prediction(
        self,
        record: ResearchPredictionRecord,
        *,
        purge_rounds: int = 2,
    ) -> ShadowLedgerEvent:
        validate_research_prediction(record, purge_rounds=purge_rounds)
        payload = {
            "kind": "prediction",
            "record": record.canonical_payload(),
        }
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            return self._append(
                connection,
                kind="prediction",
                market=record.market,
                epoch=record.epoch,
                payload=payload,
            )

    def _prediction_for(
        self,
        connection: sqlite3.Connection,
        *,
        market: str,
        epoch: int,
    ) -> ResearchPredictionRecord:
        row = self._existing(
            connection,
            kind="prediction",
            market=market,
            epoch=epoch,
        )
        if row is None:
            raise ValueError(f"shadow prediction missing for {market} epoch {epoch}")
        event = self._event_from_row(row)
        raw_record = event.payload.get("record")
        if not isinstance(raw_record, dict):
            raise ValueError("shadow prediction record payload is invalid")
        return prediction_from_payload(raw_record)

    def append_settlement(self, record: ShadowSettlementRecord) -> ShadowLedgerEvent:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            prediction = self._prediction_for(
                connection,
                market=record.market,
                epoch=record.epoch,
            )
            validate_shadow_settlement(record, prediction=prediction)
            payload = {
                "kind": "settlement",
                "record": record.canonical_payload(),
            }
            return self._append(
                connection,
                kind="settlement",
                market=record.market,
                epoch=record.epoch,
                payload=payload,
            )

    def events(self) -> tuple[ShadowLedgerEvent, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT sequence, kind, market, epoch, payload_json, previous_digest, event_digest
                FROM shadow_ledger_events
                ORDER BY sequence
                """
            ).fetchall()
        return tuple(self._event_from_row(row) for row in rows)

    def audit(self, *, purge_rounds: int = 2) -> ShadowLedgerAuditReport:
        errors: list[str] = []
        predictions: dict[tuple[str, int], ResearchPredictionRecord] = {}
        settlements: dict[tuple[str, int], ShadowSettlementRecord] = {}
        events = self.events()
        expected_previous = ZERO_DIGEST

        for expected_sequence, event in enumerate(events, start=1):
            if event.sequence != expected_sequence:
                errors.append(
                    f"non-contiguous sequence: expected {expected_sequence}, got {event.sequence}"
                )
            payload_json = _canonical_json(event.payload)
            expected_digest = _event_digest(expected_previous, payload_json)
            if event.previous_digest != expected_previous:
                errors.append(f"sequence {event.sequence}: previous digest mismatch")
            if event.event_digest != expected_digest:
                errors.append(f"sequence {event.sequence}: event digest mismatch")
            if event.payload.get("kind") != event.kind:
                errors.append(f"sequence {event.sequence}: payload kind mismatch")
            raw_record = event.payload.get("record")
            if not isinstance(raw_record, dict):
                errors.append(f"sequence {event.sequence}: record payload is invalid")
                expected_previous = event.event_digest
                continue
            try:
                if event.kind == "prediction":
                    prediction_record = prediction_from_payload(raw_record)
                    validate_research_prediction(
                        prediction_record,
                        purge_rounds=purge_rounds,
                    )
                    if (
                        prediction_record.market != event.market
                        or prediction_record.epoch != event.epoch
                    ):
                        raise ValueError("prediction identity does not match event columns")
                    prediction_key = (
                        prediction_record.market,
                        prediction_record.epoch,
                    )
                    if prediction_key in predictions:
                        raise ValueError("duplicate prediction identity")
                    predictions[prediction_key] = prediction_record
                elif event.kind == "settlement":
                    settlement_record = settlement_from_payload(raw_record)
                    prediction = predictions.get(
                        (settlement_record.market, settlement_record.epoch)
                    )
                    if prediction is None:
                        raise ValueError("settlement appears before its prediction")
                    validate_shadow_settlement(
                        settlement_record,
                        prediction=prediction,
                    )
                    if (
                        settlement_record.market != event.market
                        or settlement_record.epoch != event.epoch
                    ):
                        raise ValueError("settlement identity does not match event columns")
                    settlement_key = (
                        settlement_record.market,
                        settlement_record.epoch,
                    )
                    if settlement_key in settlements:
                        raise ValueError("duplicate settlement identity")
                    settlements[settlement_key] = settlement_record
                else:
                    raise ValueError(f"unsupported shadow ledger event kind: {event.kind}")
            except (KeyError, TypeError, ValueError) as exc:
                errors.append(f"sequence {event.sequence}: {exc}")
            expected_previous = event.event_digest

        with self._connect() as connection:
            state_count, state_head = self._state(connection)
        if state_count != len(events):
            errors.append("ledger state event_count does not match event table")
        expected_head = events[-1].event_digest if events else ZERO_DIGEST
        if state_head != expected_head:
            errors.append("ledger state head_digest does not match event chain")

        settled_keys = set(settlements)
        actionable = [item for item in predictions.values() if item.action != "skip"]
        settled_actionable = [
            item
            for key, item in predictions.items()
            if key in settled_keys and item.action != "skip"
        ]

        probability_errors: list[float] = []
        direction_correct = 0
        direction_total = 0
        observed_pnl_count = 0
        observed_pnl_wei = 0
        for key, settlement in settlements.items():
            prediction = predictions.get(key)
            if prediction is None:
                continue
            if settlement.outcome != "tie":
                target = 1.0 if settlement.outcome == "bull" else 0.0
                probability = prediction.calibrated_probability_ppm / 1_000_000
                probability_errors.append((probability - target) ** 2)
                if prediction.action in {"bull", "bear"}:
                    direction_total += 1
                    if prediction.action == settlement.outcome:
                        direction_correct += 1
            if (
                prediction.action in {"bull", "bear"}
                and settlement.realized_pnl_wei is not None
            ):
                observed_pnl_count += 1
                observed_pnl_wei += settlement.realized_pnl_wei

        decision_timestamps = sorted(item.decision_timestamp_ms for item in predictions.values())
        return ShadowLedgerAuditReport(
            event_count=len(events),
            head_digest=expected_head,
            prediction_count=len(predictions),
            settlement_count=len(settlements),
            unresolved_count=len(set(predictions) - settled_keys),
            actionable_prediction_count=len(actionable),
            settled_actionable_count=len(settled_actionable),
            probability_scored_count=len(probability_errors),
            brier_score=(
                None
                if not probability_errors
                else sum(probability_errors) / len(probability_errors)
            ),
            directional_accuracy=(
                None if direction_total == 0 else direction_correct / direction_total
            ),
            observed_pnl_count=observed_pnl_count,
            observed_pnl_wei=observed_pnl_wei,
            first_decision_timestamp_ms=(
                None if not decision_timestamps else decision_timestamps[0]
            ),
            last_decision_timestamp_ms=(
                None if not decision_timestamps else decision_timestamps[-1]
            ),
            markets=tuple(sorted({item.market for item in predictions.values()})),
            model_ids=tuple(sorted({item.model_id for item in predictions.values()})),
            feature_set_ids=tuple(
                sorted({item.feature_set_id for item in predictions.values()})
            ),
            action_counts={
                action: sum(1 for item in predictions.values() if item.action == action)
                for action in ("bull", "bear", "skip")
            },
            integrity_errors=tuple(errors),
        )


def prediction_from_payload(payload: Mapping[str, object]) -> ResearchPredictionRecord:
    metadata = payload.get("metadata")
    if metadata is not None and not isinstance(metadata, dict):
        raise ValueError("prediction metadata must be an object")
    expected_value = payload.get("expected_value_wei")
    if expected_value is not None:
        expected_value = _strict_int(expected_value, field="expected_value_wei")
    return ResearchPredictionRecord(
        market=_strict_string(payload.get("market"), field="market"),
        epoch=_strict_int(payload.get("epoch"), field="epoch"),
        decision_timestamp_ms=_strict_int(
            payload.get("decision_timestamp_ms"),
            field="decision_timestamp_ms",
        ),
        model_id=_strict_string(payload.get("model_id"), field="model_id"),
        feature_set_id=_strict_string(
            payload.get("feature_set_id"),
            field="feature_set_id",
        ),
        raw_probability_ppm=_strict_int(
            payload.get("raw_probability_ppm"),
            field="raw_probability_ppm",
        ),
        calibrated_probability_ppm=_strict_int(
            payload.get("calibrated_probability_ppm"),
            field="calibrated_probability_ppm",
        ),
        expected_value_wei=expected_value,
        action=_strict_string(payload.get("action"), field="action"),
        feature_digest=_strict_string(
            payload.get("feature_digest"),
            field="feature_digest",
        ),
        train_max_epoch=_strict_int(
            payload.get("train_max_epoch"),
            field="train_max_epoch",
        ),
        metadata=metadata,
    )


def settlement_from_payload(payload: Mapping[str, object]) -> ShadowSettlementRecord:
    metadata = payload.get("metadata")
    if metadata is not None and not isinstance(metadata, dict):
        raise ValueError("settlement metadata must be an object")
    realized_pnl = payload.get("realized_pnl_wei")
    if realized_pnl is not None:
        realized_pnl = _strict_int(realized_pnl, field="realized_pnl_wei")
    return ShadowSettlementRecord(
        market=_strict_string(payload.get("market"), field="market"),
        epoch=_strict_int(payload.get("epoch"), field="epoch"),
        settled_timestamp_ms=_strict_int(
            payload.get("settled_timestamp_ms"),
            field="settled_timestamp_ms",
        ),
        outcome=_strict_string(payload.get("outcome"), field="outcome"),
        result_source_digest=_strict_string(
            payload.get("result_source_digest"),
            field="result_source_digest",
        ),
        realized_pnl_wei=realized_pnl,
        metadata=metadata,
    )
