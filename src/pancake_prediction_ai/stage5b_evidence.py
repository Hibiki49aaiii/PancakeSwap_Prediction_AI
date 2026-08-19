from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Mapping

from .evidence_gate import Evidence, EvidenceKind, EvidenceOrigin
from .fork_execution import Stage5BExecutionResult
from .fork_harness import ForkProbeResult
from .stage5b_source_fingerprint import capture_stage5b_source_fingerprint


STAGE5B_EXECUTION_SCHEMA = "stage5b_verified_local_bsc_fork_execution_v4"


def _canonical(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


def make_stage5b_execution_evidence(
    fork_result: ForkProbeResult,
    execution_result: Stage5BExecutionResult,
    *,
    recorded_at: str | None = None,
) -> Evidence:
    """Bind upstream fork provenance, local execution, and generator source.

    The resulting evidence is still strictly local-fork evidence. The execution
    section may contain local `eth_sendTransaction` hashes from a loopback Anvil
    node, but it must state that no private key, raw signed transaction, or
    mainnet transaction broadcast was used.

    v4 additionally embeds a SHA-256 manifest of the installed source files that
    implement the Stage 5B trust path. Stage 6A recomputes that manifest and
    refuses evidence produced by a byte-for-byte different implementation.
    """

    fork_payload = asdict(fork_result)
    execution_payload = asdict(execution_result)
    source_fingerprint = capture_stage5b_source_fingerprint()
    payload: dict[str, Any] = {
        "schema": STAGE5B_EXECUTION_SCHEMA,
        "probe_type": "verified_local_bsc_fork_prediction_execution",
        "execution_transport": "loopback_impersonated_eth_sendTransaction",
        "generator_source_fingerprint": source_fingerprint,
        "fork_provenance": fork_payload,
        "prediction_execution": execution_payload,
        "private_key_used": execution_result.private_key_used,
        "raw_signed_transaction_used": execution_result.raw_signed_transaction_used,
        "mainnet_transaction_broadcast": execution_result.mainnet_transaction_broadcast,
    }
    digest = hashlib.sha256(_canonical(payload)).hexdigest()
    lineage_matches = (
        fork_result.prediction_contract.lower() == execution_result.prediction_contract.lower()
        and fork_result.initial_block == execution_result.fork_base_block
        and str(fork_result.upstream_fork_block_hash).lower()
        == execution_result.fork_base_block_hash.lower()
    )
    return Evidence(
        kind=EvidenceKind.STAGE5B_FORK,
        origin=EvidenceOrigin.OBSERVED,
        passed=(fork_result.verified_passed and execution_result.passed and lineage_matches),
        artifact_sha256=digest,
        recorded_at=recorded_at or datetime.now(timezone.utc).isoformat(),
        payload=payload,
    )
