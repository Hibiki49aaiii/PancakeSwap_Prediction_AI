from __future__ import annotations

import argparse
import http.client
import json
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

PREDICTION_BNBUSD = "0x18B2A687610328590Bc8F2e5fEdDe3b582A49cdA"
PREDICTION_CREATION_BLOCK = 10_333_825
PAUSED_SELECTOR = "0x5c975abb"

PUBLIC_BSC_ENDPOINTS = (
    "https://bsc-dataseed.bnbchain.org",
    "https://bsc-dataseed-public.bnbchain.org",
    "https://bsc-dataseed.nariox.org",
    "https://bsc-dataseed.defibit.io",
    "https://bsc-dataseed.ninicoin.io",
    "https://bsc.nodereal.io",
    "https://bnb.rpc.subquery.network/public",
    "https://rpc.ankr.com/bsc",
    "https://bsc-rpc.publicnode.com",
    "https://public.1rpc.io/bnb",
)

BINANCE_ARCHIVES = (
    {
        "venue": "spot",
        "zip": (
            "https://data.binance.vision/data/spot/daily/aggTrades/BNBUSDT/"
            "BNBUSDT-aggTrades-2026-08-01.zip"
        ),
        "checksum": (
            "https://data.binance.vision/data/spot/daily/aggTrades/BNBUSDT/"
            "BNBUSDT-aggTrades-2026-08-01.zip.CHECKSUM"
        ),
    },
    {
        "venue": "um_futures",
        "zip": (
            "https://data.binance.vision/data/futures/um/daily/aggTrades/BNBUSDT/"
            "BNBUSDT-aggTrades-2026-08-01.zip"
        ),
        "checksum": (
            "https://data.binance.vision/data/futures/um/daily/aggTrades/BNBUSDT/"
            "BNBUSDT-aggTrades-2026-08-01.zip.CHECKSUM"
        ),
    },
)


def _http_request(
    url: str,
    *,
    method: str,
    timeout: float,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
    read_limit: int | None = None,
) -> tuple[int, dict[str, str], bytes]:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
        raise ValueError("URL must use http or https with a hostname")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("credentials must not be embedded in probe URLs")
    if parsed.fragment:
        raise ValueError("probe URL must not contain a fragment")

    connection_type = (
        http.client.HTTPSConnection
        if parsed.scheme == "https"
        else http.client.HTTPConnection
    )
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    connection = connection_type(parsed.hostname, port, timeout=timeout)
    target = parsed.path or "/"
    if parsed.query:
        target += "?" + parsed.query
    try:
        connection.request(method, target, body=body, headers=headers or {})
        response = connection.getresponse()
        payload = response.read() if read_limit is None else response.read(read_limit)
        response_headers = {name: value for name, value in response.getheaders()}
        if response.status >= 400:
            detail = payload[:1_000].decode(errors="replace").strip()
            suffix = f": {detail}" if detail else ""
            raise RuntimeError(f"HTTP {response.status}{suffix}")
        return response.status, response_headers, payload
    except (OSError, http.client.HTTPException) as exc:
        raise RuntimeError(f"HTTP request failed: {exc}") from exc
    finally:
        connection.close()


def _json_rpc(endpoint: str, method: str, params: list[object], timeout: float) -> object:
    payload = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        separators=(",", ":"),
    ).encode()
    _, _, raw = _http_request(
        endpoint,
        method="POST",
        timeout=timeout,
        body=payload,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "pcs-public-input-probe/1",
        },
    )
    body = json.loads(raw.decode())
    if not isinstance(body, dict):
        raise RuntimeError("RPC response is not an object")
    if "error" in body:
        error = json.dumps(body["error"], sort_keys=True, separators=(",", ":"))
        raise RuntimeError(error)
    if "result" not in body:
        raise RuntimeError("RPC response has no result")
    return body["result"]


def _probe_rpc(endpoint: str, timeout: float) -> dict[str, object]:
    started = time.monotonic()
    result: dict[str, object] = {
        "endpoint": endpoint,
        "chain_id": None,
        "latest_block": None,
        "historical_code": False,
        "historical_paused_call": False,
        "archive_ready": False,
        "error": None,
    }
    try:
        chain_id_raw = _json_rpc(endpoint, "eth_chainId", [], timeout)
        if not isinstance(chain_id_raw, str):
            raise RuntimeError("eth_chainId did not return a hex string")
        chain_id = int(chain_id_raw, 16)
        result["chain_id"] = chain_id

        latest_raw = _json_rpc(endpoint, "eth_blockNumber", [], timeout)
        if not isinstance(latest_raw, str):
            raise RuntimeError("eth_blockNumber did not return a hex string")
        result["latest_block"] = int(latest_raw, 16)

        historical_block = hex(PREDICTION_CREATION_BLOCK + 1)
        code = _json_rpc(
            endpoint,
            "eth_getCode",
            [PREDICTION_BNBUSD, historical_block],
            timeout,
        )
        result["historical_code"] = (
            isinstance(code, str) and code not in {"0x", "0x0", ""}
        )

        paused = _json_rpc(
            endpoint,
            "eth_call",
            [{"to": PREDICTION_BNBUSD, "data": PAUSED_SELECTOR}, historical_block],
            timeout,
        )
        result["historical_paused_call"] = (
            isinstance(paused, str) and paused.startswith("0x") and len(paused) >= 66
        )
        result["archive_ready"] = bool(
            chain_id == 56
            and result["historical_code"]
            and result["historical_paused_call"]
        )
    except (OSError, ValueError, RuntimeError, http.client.HTTPException) as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    result["elapsed_ms"] = round((time.monotonic() - started) * 1000)
    return result


def _probe_binance_archive(item: dict[str, str], timeout: float) -> dict[str, object]:
    result: dict[str, object] = {
        "venue": item["venue"],
        "zip_url": item["zip"],
        "checksum_url": item["checksum"],
        "zip_accessible": False,
        "checksum_accessible": False,
        "checksum_sha256": None,
        "error": None,
    }
    try:
        _, _, checksum_bytes = _http_request(
            item["checksum"],
            method="GET",
            timeout=timeout,
            headers={"User-Agent": "pcs-public-input-probe/1"},
            read_limit=4_096,
        )
        checksum_text = checksum_bytes.decode().strip()
        match = re.search(r"\b([0-9a-fA-F]{64})\b", checksum_text)
        if match is None:
            raise RuntimeError("checksum file did not contain SHA-256")
        result["checksum_accessible"] = True
        result["checksum_sha256"] = match.group(1).lower()

        status, response_headers, first_byte = _http_request(
            item["zip"],
            method="GET",
            timeout=timeout,
            headers={
                "Range": "bytes=0-0",
                "User-Agent": "pcs-public-input-probe/1",
            },
            read_limit=1,
        )
        result["zip_http_status"] = status
        result["zip_content_length"] = response_headers.get("Content-Length")
        result["zip_content_range"] = response_headers.get("Content-Range")
        result["zip_accessible"] = len(first_byte) == 1
    except (OSError, ValueError, RuntimeError, http.client.HTTPException) as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def build_report(timeout: float) -> dict[str, Any]:
    rpc_results = [_probe_rpc(endpoint, timeout) for endpoint in PUBLIC_BSC_ENDPOINTS]
    binance_results = [_probe_binance_archive(item, timeout) for item in BINANCE_ARCHIVES]
    return {
        "probe_version": 1,
        "prediction_contract": PREDICTION_BNBUSD.lower(),
        "historical_probe_block": PREDICTION_CREATION_BLOCK + 1,
        "rpc_results": rpc_results,
        "archive_ready_endpoints": [
            item["endpoint"] for item in rpc_results if item["archive_ready"]
        ],
        "binance_results": binance_results,
        "binance_ready": all(
            item["zip_accessible"] and item["checksum_accessible"]
            for item in binance_results
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=12.0)
    args = parser.parse_args()
    report = build_report(args.timeout)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
