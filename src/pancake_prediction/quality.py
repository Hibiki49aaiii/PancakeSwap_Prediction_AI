from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class QualityReport:
    market: str
    starts: int
    locks: int
    ends: int
    bets: int
    unresolved_started_rounds: int
    duplicate_canonical_heights: int
    reorgs: int

    def as_dict(self) -> dict[str, object]:
        return {
            "market": self.market,
            "starts": self.starts,
            "locks": self.locks,
            "ends": self.ends,
            "bets": self.bets,
            "unresolved_started_rounds": self.unresolved_started_rounds,
            "duplicate_canonical_heights": self.duplicate_canonical_heights,
            "reorgs": self.reorgs,
        }


def _epochs(rows: list[sqlite3.Row]) -> set[int]:
    result: set[int] = set()
    for row in rows:
        if row["decoded_json"]:
            data = json.loads(str(row["decoded_json"]))
            if isinstance(data, dict) and "epoch" in data:
                result.add(int(data["epoch"]))
    return result


def build_quality_report(path: Path, market: str) -> QualityReport:
    with closing(sqlite3.connect(path)) as conn:
        conn.row_factory = sqlite3.Row
        canonical = """
            EXISTS (
              SELECT 1 FROM blocks b
              WHERE b.chain_id=e.chain_id AND b.number=e.block_number
                AND b.hash=e.block_hash AND b.canonical=1
            )
        """
        rows = conn.execute(
            (
                "SELECT event_name,decoded_json FROM events e "
                f"WHERE market=? AND source='prediction' AND {canonical}"  # noqa: S608
            ),
            (market,),
        ).fetchall()
        starts_rows = [row for row in rows if row["event_name"] == "StartRound"]
        locks_rows = [row for row in rows if row["event_name"] == "LockRound"]
        ends_rows = [row for row in rows if row["event_name"] == "EndRound"]
        bets = sum(1 for row in rows if row["event_name"] in ("BetBull", "BetBear"))
        starts = _epochs(starts_rows)
        ends = _epochs(ends_rows)
        duplicate_heights = conn.execute(
            """SELECT COUNT(*) FROM (
                 SELECT chain_id,number FROM blocks WHERE canonical=1
                 GROUP BY chain_id,number HAVING COUNT(*) > 1
               )"""
        ).fetchone()[0]
        reorg_count = conn.execute("SELECT COUNT(*) FROM reorgs").fetchone()[0]
    return QualityReport(
        market=market,
        starts=len(starts_rows),
        locks=len(locks_rows),
        ends=len(ends_rows),
        bets=bets,
        unresolved_started_rounds=len(starts - ends),
        duplicate_canonical_heights=int(duplicate_heights),
        reorgs=int(reorg_count),
    )
