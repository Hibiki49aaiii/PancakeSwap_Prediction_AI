from __future__ import annotations

import json
import sqlite3
from bisect import bisect_right
from contextlib import closing
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import cast

from .replay import ChainEvent

EventPosition = tuple[int, int, int]


@dataclass(frozen=True, slots=True)
class OracleAnchor:
    block_number: int
    address: str

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class OracleActivation:
    block_number: int
    tx_index: int
    log_index: int
    address: str
    source: str

    @property
    def position(self) -> EventPosition:
        return (self.block_number, self.tx_index, self.log_index)


@dataclass(frozen=True, slots=True)
class AddressedChainlinkEvent:
    contract_address: str
    event: ChainEvent

    @property
    def position(self) -> EventPosition:
        return (
            self.event.block_number,
            self.event.tx_index,
            self.event.log_index,
        )


@dataclass(frozen=True, slots=True)
class ActiveOracleHistory:
    market: str
    anchor: OracleAnchor
    activations: tuple[OracleActivation, ...]
    events: tuple[ChainEvent, ...]
    canonical_answer_updates: int
    excluded_inactive_oracle: int
    excluded_unanchored: int

    def as_dict(self) -> dict[str, object]:
        return {
            "market": self.market,
            "anchor": self.anchor.as_dict(),
            "activation_count": len(self.activations),
            "active_answer_updates": len(self.events),
            "canonical_answer_updates": self.canonical_answer_updates,
            "excluded_inactive_oracle": self.excluded_inactive_oracle,
            "excluded_unanchored": self.excluded_unanchored,
        }


def _connection(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def _decode_object(payload: object) -> dict[str, object]:
    if payload is None:
        return {}
    parsed = json.loads(str(payload))
    if not isinstance(parsed, dict):
        raise ValueError("event decoded_json must contain a JSON object")
    return cast(dict[str, object], parsed)


def _validate_address(address: str) -> str:
    normalized = address.lower()
    if not normalized.startswith("0x") or len(normalized) != 42:
        raise ValueError(f"invalid oracle address: {address!r}")
    try:
        int(normalized[2:], 16)
    except ValueError as exc:
        raise ValueError(f"invalid oracle address: {address!r}") from exc
    return normalized


def load_oracle_anchor(path: Path, market: str) -> OracleAnchor:
    with closing(_connection(path)) as connection:
        rows = connection.execute(
            "SELECT key,value FROM metadata WHERE key IN (?,?)",
            (
                f"{market}.oracle_anchor_block",
                f"{market}.oracle_anchor_address",
            ),
        ).fetchall()
    values = {str(row["key"]): str(row["value"]) for row in rows}
    block_text = values.get(f"{market}.oracle_anchor_block")
    address = values.get(f"{market}.oracle_anchor_address")
    if block_text is None or address is None:
        raise ValueError(
            f"missing oracle anchor metadata for {market}; rerun historical-bootstrap "
            "with Chainlink collection enabled"
        )
    try:
        block_number = int(block_text)
    except ValueError as exc:
        raise ValueError(f"invalid oracle anchor block for {market}") from exc
    if block_number < 0:
        raise ValueError(f"invalid oracle anchor block for {market}")
    return OracleAnchor(block_number, _validate_address(address))


def _load_new_oracle_activations(
    connection: sqlite3.Connection,
    market: str,
    anchor: OracleAnchor,
) -> tuple[OracleActivation, ...]:
    rows = connection.execute(
        """
        SELECT e.block_number,e.tx_index,e.log_index,e.decoded_json
        FROM events AS e
        JOIN blocks AS b
          ON b.chain_id=e.chain_id
         AND b.number=e.block_number
         AND b.hash=e.block_hash
        WHERE b.canonical=1
          AND e.market=?
          AND e.source='prediction'
          AND e.event_name='NewOracle'
          AND e.block_number>=?
          AND e.decoded_json IS NOT NULL
        ORDER BY e.block_number,e.tx_index,e.log_index,e.tx_hash
        """,
        (market, anchor.block_number),
    ).fetchall()
    explicit: list[OracleActivation] = []
    seen_positions: set[EventPosition] = set()
    for row in rows:
        decoded = _decode_object(row["decoded_json"])
        address = decoded.get("oracle")
        if not isinstance(address, str):
            raise ValueError("NewOracle event is missing decoded oracle address")
        activation = OracleActivation(
            block_number=int(row["block_number"]),
            tx_index=int(row["tx_index"]),
            log_index=int(row["log_index"]),
            address=_validate_address(address),
            source="NewOracle",
        )
        if activation.position in seen_positions:
            raise ValueError("duplicate NewOracle activation position")
        seen_positions.add(activation.position)
        explicit.append(activation)

    same_anchor_block = [
        activation
        for activation in explicit
        if activation.block_number == anchor.block_number
    ]
    if same_anchor_block and same_anchor_block[-1].address != anchor.address:
        raise ValueError(
            "historical oracle anchor disagrees with the final NewOracle event "
            "in the anchor block"
        )

    activations = [
        *explicit,
        OracleActivation(
            block_number=anchor.block_number + 1,
            tx_index=-1,
            log_index=-1,
            address=anchor.address,
            source="historical-anchor",
        ),
    ]
    activations.sort(key=lambda item: item.position)
    return tuple(activations)


def _load_canonical_answer_updates(
    connection: sqlite3.Connection,
    market: str,
    anchor: OracleAnchor,
) -> tuple[AddressedChainlinkEvent, ...]:
    rows = connection.execute(
        """
        SELECT e.contract_address,e.block_number,e.block_hash,
               b.timestamp AS block_timestamp,e.tx_hash,e.tx_index,e.log_index,
               e.decoded_json
        FROM events AS e
        JOIN blocks AS b
          ON b.chain_id=e.chain_id
         AND b.number=e.block_number
         AND b.hash=e.block_hash
        WHERE b.canonical=1
          AND e.market=?
          AND e.source='chainlink'
          AND e.event_name='AnswerUpdated'
          AND e.block_number>=?
          AND e.decoded_json IS NOT NULL
        ORDER BY e.block_number,e.tx_index,e.log_index,e.tx_hash
        """,
        (market, anchor.block_number),
    ).fetchall()
    events: list[AddressedChainlinkEvent] = []
    for row in rows:
        events.append(
            AddressedChainlinkEvent(
                contract_address=_validate_address(str(row["contract_address"])),
                event=ChainEvent(
                    block_number=int(row["block_number"]),
                    block_hash=str(row["block_hash"]),
                    block_timestamp=int(row["block_timestamp"]),
                    tx_hash=str(row["tx_hash"]),
                    tx_index=int(row["tx_index"]),
                    log_index=int(row["log_index"]),
                    event_name="AnswerUpdated",
                    decoded=_decode_object(row["decoded_json"]),
                ),
            )
        )
    return tuple(events)


def build_active_oracle_history(path: Path, market: str) -> ActiveOracleHistory:
    anchor = load_oracle_anchor(path, market)
    with closing(_connection(path)) as connection:
        activations = _load_new_oracle_activations(connection, market, anchor)
        candidates = _load_canonical_answer_updates(connection, market, anchor)

    activation_positions = tuple(activation.position for activation in activations)
    selected: list[ChainEvent] = []
    excluded_inactive = 0
    excluded_unanchored = 0
    for candidate in candidates:
        activation_index = bisect_right(activation_positions, candidate.position) - 1
        if activation_index < 0:
            excluded_unanchored += 1
            continue
        active_address = activations[activation_index].address
        if candidate.contract_address != active_address:
            excluded_inactive += 1
            continue
        selected.append(candidate.event)

    return ActiveOracleHistory(
        market=market,
        anchor=anchor,
        activations=activations,
        events=tuple(selected),
        canonical_answer_updates=len(candidates),
        excluded_inactive_oracle=excluded_inactive,
        excluded_unanchored=excluded_unanchored,
    )


def active_chainlink_events(path: Path, market: str) -> tuple[ChainEvent, ...]:
    return build_active_oracle_history(path, market).events
