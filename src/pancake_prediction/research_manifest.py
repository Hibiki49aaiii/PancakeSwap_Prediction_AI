from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass

from .binance_archive import BinanceArchiveProvenance
from .research_dataset import BINANCE_SYMBOL_BY_MARKET
from .research_inputs import CanonicalResearchInputs


@dataclass(frozen=True, slots=True)
class ResearchTimingAssumptions:
    feature_lead_seconds: int = 20
    flow_lookback_ms: int = 60_000
    max_spot_age_ms: int = 5_000
    max_perp_age_ms: int = 5_000
    max_chainlink_age_ms: int | None = None
    chainlink_availability_lag_ms: int = 0
    oracle_history_updates: int = 512
    oracle_hazard_horizon_ms: int = 5_000
    oracle_hazard_min_intervals: int = 8

    def validate(self) -> None:
        if self.feature_lead_seconds < 0:
            raise ValueError("feature_lead_seconds must be non-negative")
        if self.flow_lookback_ms <= 0:
            raise ValueError("flow_lookback_ms must be positive")
        if self.max_spot_age_ms < 0 or self.max_perp_age_ms < 0:
            raise ValueError("Binance max age values must be non-negative")
        if self.max_chainlink_age_ms is not None and self.max_chainlink_age_ms < 0:
            raise ValueError("max_chainlink_age_ms must be non-negative")
        if self.chainlink_availability_lag_ms < 0:
            raise ValueError("chainlink_availability_lag_ms must be non-negative")
        if self.oracle_hazard_horizon_ms <= 0:
            raise ValueError("oracle_hazard_horizon_ms must be positive")
        if self.oracle_hazard_min_intervals < 1:
            raise ValueError("oracle_hazard_min_intervals must be positive")
        if self.oracle_history_updates < self.oracle_hazard_min_intervals + 1:
            raise ValueError("oracle_history_updates must cover hazard minimum intervals")


@dataclass(frozen=True, slots=True)
class ResearchCampaignManifest:
    schema_version: int
    market: str
    replay_input_digest: str
    replay_output_digest: str
    oracle_anchor_block: int
    oracle_anchor_address: str
    oracle_activation_count: int
    active_chainlink_event_count: int
    spot_archives: tuple[BinanceArchiveProvenance, ...]
    perp_archives: tuple[BinanceArchiveProvenance, ...]
    timing: ResearchTimingAssumptions

    def canonical_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "market": self.market,
            "replay_input_digest": self.replay_input_digest,
            "replay_output_digest": self.replay_output_digest,
            "oracle_anchor_block": self.oracle_anchor_block,
            "oracle_anchor_address": self.oracle_anchor_address,
            "oracle_activation_count": self.oracle_activation_count,
            "active_chainlink_event_count": self.active_chainlink_event_count,
            "spot_archives": [source.as_dict() for source in self.spot_archives],
            "perp_archives": [source.as_dict() for source in self.perp_archives],
            "timing": asdict(self.timing),
        }

    def canonical_bytes(self) -> bytes:
        return (
            json.dumps(
                self.canonical_payload(),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            )
            + "\n"
        ).encode()

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()


def _validate_digest(value: str, *, name: str) -> None:
    if len(value) != 64:
        raise ValueError(f"{name} must be a SHA-256 hex digest")
    try:
        int(value, 16)
    except ValueError as exc:
        raise ValueError(f"{name} must be a SHA-256 hex digest") from exc


def _validate_archive_group(
    sources: tuple[BinanceArchiveProvenance, ...],
    *,
    expected_symbol: str,
    venue_group: str,
) -> None:
    seen_hashes: set[str] = set()
    for source in sources:
        _validate_digest(source.source_sha256, name="source_sha256")
        if source.symbol != expected_symbol:
            raise ValueError(
                f"archive symbol mismatch: expected {expected_symbol}, got {source.symbol}"
            )
        if venue_group == "spot" and source.venue != "spot":
            raise ValueError("spot archive group contains non-spot source")
        if venue_group == "perp" and source.venue not in {"um_futures", "cm_futures"}:
            raise ValueError("perp archive group contains non-futures source")
        if source.source_sha256 in seen_hashes:
            raise ValueError("duplicate Binance archive source hash")
        seen_hashes.add(source.source_sha256)


def build_research_campaign_manifest(
    inputs: CanonicalResearchInputs,
    *,
    spot_archives: tuple[BinanceArchiveProvenance, ...],
    perp_archives: tuple[BinanceArchiveProvenance, ...] = (),
    timing: ResearchTimingAssumptions | None = None,
) -> ResearchCampaignManifest:
    resolved_timing = ResearchTimingAssumptions() if timing is None else timing
    resolved_timing.validate()
    expected_symbol = BINANCE_SYMBOL_BY_MARKET.get(inputs.market)
    if expected_symbol is None:
        raise ValueError(f"unsupported research market: {inputs.market}")
    if not spot_archives:
        raise ValueError("at least one spot archive is required")
    _validate_archive_group(
        spot_archives,
        expected_symbol=expected_symbol,
        venue_group="spot",
    )
    _validate_archive_group(
        perp_archives,
        expected_symbol=expected_symbol,
        venue_group="perp",
    )
    _validate_digest(inputs.replay.input_digest, name="replay_input_digest")
    _validate_digest(inputs.replay.output_digest, name="replay_output_digest")
    return ResearchCampaignManifest(
        schema_version=1,
        market=inputs.market,
        replay_input_digest=inputs.replay.input_digest,
        replay_output_digest=inputs.replay.output_digest,
        oracle_anchor_block=inputs.oracle_history.anchor.block_number,
        oracle_anchor_address=inputs.oracle_history.anchor.address,
        oracle_activation_count=len(inputs.oracle_history.activations),
        active_chainlink_event_count=len(inputs.oracle_history.events),
        spot_archives=spot_archives,
        perp_archives=perp_archives,
        timing=resolved_timing,
    )
