# Stage 4 Windows 11 Campaign Runbook

This runbook is the canonical operator procedure for Issue #23 when the Stage 4
prospective Shadow campaign is run from a Windows 11 workstation.

It is intentionally limited to the no-signing Stage 4 boundary:

- no private key or mnemonic;
- no wallet unlock;
- no transaction signing;
- no mainnet transaction broadcast;
- no funded execution;
- `signing_enabled=false`;
- `live_broadcast=false`;
- `funded_execution=false`;
- `profitability_gate_eligible=false`.

The operator must not paste RPC credentials, API keys, passwords, tokens, private
keys, or mnemonics into GitHub Issues or ChatGPT feedback.

## Repository Windows portability evidence

The operator surface is mechanically checked by
`.github/workflows/windows-operator-smoke.yml` on `windows-latest`.

Issue #24 established Windows Python 3.12 install/CLI/test compatibility and verifies that a
synthetic `BSC_RPC_URL` sentinel is absent from the generated feedback report.

This CI is operator-tooling evidence only. It does not replace the real-host Stage 4 preflight
or the multi-day prospective campaign required by Issue #23.

## 1. Reference revision

Issue #23 was prepared from:

- repository: `Hibiki49aaiii/PancakeSwap_Prediction_AI`
- branch: `agent/v0.7-alpha-research`
- baseline HEAD: `15a77af17a95e2eaad8131794506adcc892e32d0`
- source quality evidence: 516 tests, 87% coverage, green Ruff/mypy/Bandit/pip-audit
- exact-head CI #1391: success

If the branch HEAD has advanced, record the exact SHA used for the campaign.
Do not silently mix code revisions within a campaign.

## 2. Windows prerequisites

Open an elevated PowerShell and install:

```powershell
winget install --id Git.Git -e
winget install --id Python.Python.3.12 -e
winget install --id Docker.DockerDesktop -e
```

If Docker Desktop requests WSL2:

```powershell
wsl --install
```

Restart Windows if required, launch Docker Desktop once, then verify:

```powershell
git --version
py -3.12 --version
docker --version
docker info
```

Python 3.12 is required by `pyproject.toml`.

## 3. Clone the campaign branch

```powershell
New-Item -ItemType Directory -Force C:\Dev | Out-Null
Set-Location C:\Dev

git clone --branch agent/v0.7-alpha-research --single-branch https://github.com/Hibiki49aaiii/PancakeSwap_Prediction_AI.git
Set-Location C:\Dev\PancakeSwap_Prediction_AI

git rev-parse HEAD
git status
```

Record the exact HEAD before the campaign starts.

## 4. Python environment and local verification

```powershell
Set-ExecutionPolicy -Scope Process Bypass
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Smoke check:

```powershell
pcs-prediction status
pcs-clickhouse --help
pcs-shadow-runtime --help
```

Run the same quality families used by CI:

```powershell
ruff check .
mypy src tests
pytest --cov=src/pancake_prediction --cov-report=term-missing
bandit -c pyproject.toml -r src
pip-audit
```

Do not continue if the project does not install or the CLI entry points are unavailable.
A Windows-only tooling discrepancy may be reported separately, but source failures must
not be ignored.

## 5. Start ClickHouse 25.8

```powershell
docker pull clickhouse/clickhouse-server:25.8

docker run -d `
  --name pcs-clickhouse `
  --restart unless-stopped `
  -p 127.0.0.1:8123:8123 `
  -p 127.0.0.1:9000:9000 `
  -v pcs-clickhouse-data:/var/lib/clickhouse `
  -e CLICKHOUSE_SKIP_USER_SETUP=1 `
  clickhouse/clickhouse-server:25.8
```

Set the process environment:

```powershell
$env:CLICKHOUSE_URL = "http://127.0.0.1:8123"
$env:CLICKHOUSE_DATABASE = "default"
```

Apply the repository schema:

```powershell
@'
from pathlib import Path
from pancake_prediction.clickhouse import ClickHouseHttpClient

client = ClickHouseHttpClient("http://127.0.0.1:8123")
text = Path("sql/clickhouse/v0_7_core.sql").read_text(encoding="utf-8")
sql = "\n".join(
    line for line in text.splitlines()
    if not line.lstrip().startswith("--")
)
for statement in sql.split(";"):
    if statement.strip():
        client.execute(statement)
print("ClickHouse schema applied successfully")
'@ | python -
```

Verify:

```powershell
pcs-clickhouse ping
pcs-clickhouse schema-check
```

Required result: ping succeeds and schema-check reports `ready=true`.

## 6. Create durable local paths

```powershell
New-Item -ItemType Directory -Force artifacts | Out-Null
New-Item -ItemType Directory -Force artifacts\binance\spot | Out-Null
New-Item -ItemType Directory -Force artifacts\binance\perp | Out-Null
New-Item -ItemType Directory -Force evidence | Out-Null
New-Item -ItemType Directory -Force logs | Out-Null
```

Do not place the canonical database, Shadow ledger, operational status, and campaign
Evidence in temporary directories.

## 7. Bootstrap recent canonical BSC data

A real Stage 4 campaign needs enough settled canonical history and a proven current
Prediction oracle proxy -> Chainlink aggregator route.

Example three-day window:

```powershell
$start = [DateTimeOffset]::UtcNow.AddDays(-3).ToUnixTimeSeconds()
$end   = [DateTimeOffset]::UtcNow.AddMinutes(-10).ToUnixTimeSeconds()

python scripts\run_recent_public_bootstrap.py `
  --market BNBUSD `
  --database artifacts\bnbusd-stage4.sqlite `
  --output artifacts\recent-bootstrap.json `
  --start-timestamp $start `
  --end-timestamp $end `
  --confirmations 64 `
  --chunk-size 2000 `
  --include-chainlink
```

Inspect:

```powershell
Get-Content artifacts\recent-bootstrap.json
```

Do not continue unless the bootstrap succeeded and Chainlink collection/route proof is
suitable for the campaign. If the public RPC candidates cannot satisfy the log workload,
use an authenticated BSC log/archive-capable RPC and keep its credential out of reports.

## 8. Prepare Binance historical lineages before live collection

Historical archive ingestion for an identical lineage is one-way: it must finish before
prospective live rows exist for that lineage.

Prepare three completed UTC days:

```powershell
$dates = @(
  [DateTimeOffset]::UtcNow.AddDays(-3).ToString("yyyy-MM-dd"),
  [DateTimeOffset]::UtcNow.AddDays(-2).ToString("yyyy-MM-dd"),
  [DateTimeOffset]::UtcNow.AddDays(-1).ToString("yyyy-MM-dd")
)
```

Download Spot:

```powershell
foreach ($d in $dates) {
  $file = "BNBUSDT-aggTrades-$d.zip"
  curl.exe -fL "https://data.binance.vision/data/spot/daily/aggTrades/BNBUSDT/$file" -o "artifacts\binance\spot\$file"
  curl.exe -fL "https://data.binance.vision/data/spot/daily/aggTrades/BNBUSDT/$file.CHECKSUM" -o "artifacts\binance\spot\$file.CHECKSUM"
}
```

Download USD-M futures:

```powershell
foreach ($d in $dates) {
  $file = "BNBUSDT-aggTrades-$d.zip"
  curl.exe -fL "https://data.binance.vision/data/futures/um/daily/aggTrades/BNBUSDT/$file" -o "artifacts\binance\perp\$file"
  curl.exe -fL "https://data.binance.vision/data/futures/um/daily/aggTrades/BNBUSDT/$file.CHECKSUM" -o "artifacts\binance\perp\$file.CHECKSUM"
}
```

Ingest Spot with the runtime lineage:

```powershell
foreach ($d in $dates) {
  $file = "BNBUSDT-aggTrades-$d.zip"
  pcs-clickhouse binance-ingest `
    --market BNBUSD `
    --archive "artifacts\binance\spot\$file" `
    --checksum "artifacts\binance\spot\$file.CHECKSUM" `
    --venue spot `
    --timestamp-unit auto `
    --availability-lag-ms 250
}
```

Ingest USD-M futures with the runtime lineage:

```powershell
foreach ($d in $dates) {
  $file = "BNBUSDT-aggTrades-$d.zip"
  pcs-clickhouse binance-ingest `
    --market BNBUSD `
    --archive "artifacts\binance\perp\$file" `
    --checksum "artifacts\binance\perp\$file.CHECKSUM" `
    --venue um_futures `
    --timestamp-unit milliseconds `
    --availability-lag-ms 250
}
```

Do not start a live writer before this step is complete.

## 9. Configure BSC RPC without exposing credentials

For a credential-bearing endpoint:

```powershell
$env:BSC_RPC_URL = Read-Host "BSC RPC URL"
if ($env:BSC_RPC_URL) { "BSC_RPC_URL is configured" }
```

Never echo or paste the URL if it embeds a credential.

## 10. Mandatory Stage 4 preflight

```powershell
pcs-shadow-runtime `
  --market BNBUSD `
  --canonical-db artifacts\bnbusd-stage4.sqlite `
  --shadow-db artifacts\stage4-shadow.sqlite3 `
  --preflight-only `
  --preflight-output evidence\stage4-preflight.json `
  --stake-wei 10000000000000000 `
  --bet-gas-wei 50000000000000 `
  --claim-gas-wei 30000000000000 `
  --inclusion-latency-seconds 2

$LASTEXITCODE
Get-Content evidence\stage4-preflight.json
```

Campaign start is blocked unless:

- exit code is 0;
- preflight reports `ready=true`;
- no source-integrity contradiction exists;
- no campaign/lineage lock conflict exists.

## 11. One-cycle smoke test

Only after a green preflight:

```powershell
pcs-shadow-runtime `
  --market BNBUSD `
  --canonical-db artifacts\bnbusd-stage4.sqlite `
  --shadow-db artifacts\stage4-shadow.sqlite3 `
  --once `
  --stake-wei 10000000000000000 `
  --bet-gas-wei 50000000000000 `
  --claim-gas-wei 30000000000000 `
  --inclusion-latency-seconds 2 `
  --status-output artifacts\stage4-runtime-status.json `
  --evidence-output evidence\stage4-runtime-latest.json `
  --campaign-evidence-output evidence\stage4-campaign-latest.json `
  --campaign-last-success-output evidence\stage4-campaign-last-success.json
```

Read-only health check:

```powershell
pcs-prediction shadow-runtime-health `
  --status-file artifacts\stage4-runtime-status.json `
  --max-status-age-seconds 180 `
  --max-last-success-age-seconds 900
```

A fresh retry may be operationally alive/degraded. A fatal, stale, malformed, or
contradictory status fails closed.

## 12. Continuous prospective campaign

After the smoke test passes:

```powershell
pcs-shadow-runtime `
  --market BNBUSD `
  --canonical-db artifacts\bnbusd-stage4.sqlite `
  --shadow-db artifacts\stage4-shadow.sqlite3 `
  --poll-seconds 1 `
  --max-consecutive-cycle-errors 5 `
  --stake-wei 10000000000000000 `
  --bet-gas-wei 50000000000000 `
  --claim-gas-wei 30000000000000 `
  --inclusion-latency-seconds 2 `
  --status-output artifacts\stage4-runtime-status.json `
  --evidence-output evidence\stage4-runtime-latest.json `
  --campaign-evidence-output evidence\stage4-campaign-latest.json `
  --campaign-last-success-output evidence\stage4-campaign-last-success.json `
  2>&1 | Tee-Object -FilePath logs\stage4-runtime.log -Append
```

Keep:

- Windows awake;
- Docker Desktop running;
- the runtime PowerShell process running;
- the same canonical DB / Shadow DB / output paths;
- the same source lineage and semantic campaign configuration.

On a process restart, re-run the read-only preflight before resuming.

## 13. Progress and completion checks

Operational health:

```powershell
pcs-prediction shadow-runtime-health `
  --status-file artifacts\stage4-runtime-status.json `
  --max-status-age-seconds 180 `
  --max-last-success-age-seconds 900
```

Campaign gate:

```powershell
pcs-prediction shadow-campaign-gate `
  --db artifacts\stage4-shadow.sqlite3 `
  --purge-rounds 2
```

Default Stage 4 empirical completion requires at least:

- predictions >= 1,000;
- settlements >= 900;
- probability-scored settlements >= 900;
- actionable predictions >= 200;
- decision span >= 7 days;
- unresolved rate <= 10%;
- both actionable Bull and Bear observations;
- 100% actionable settled PnL coverage;
- <= 1 model ID;
- <= 1 feature-set ID;
- valid immutable campaign manifest;
- valid Shadow ledger hash chain;
- consistent canonical reconciliation;
- valid source route/lineage assumptions;
- final Stage 4 campaign gate ready.

Positive PnL is not required for Stage 4 completion. Preserve unfavorable evidence.

## 14. Generate the feedback file automatically

After the mandatory preflight, generate a redacted operator report with:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\windows_stage4_feedback.ps1 -RunQuality
```

The default output is:

```text
artifacts\stage4-windows-feedback.txt
```

The collector intentionally does not read `BSC_RPC_URL` or other secret environment values.
It includes only a redacted bootstrap summary and the preflight JSON, whose runtime contract
excludes endpoint credentials.

If the normal setup has already been quality-checked and only a lightweight status report is
needed, omit `-RunQuality`.

## 15. Operator feedback template

Return this after the initial setup and mandatory preflight:

```text
[Environment]
git --version:
<output>

py -3.12 --version:
<output>

docker --version:
<output>

[Repository]
git rev-parse HEAD:
<output>

git status:
<output>

[Project]
pcs-prediction status:
<output>

[Quality]
ruff: PASS / FAIL
mypy: PASS / FAIL
pytest: <summary and final ~20 lines if failed>
bandit: PASS / FAIL
pip-audit: PASS / FAIL

[ClickHouse]
docker ps --filter name=pcs-clickhouse:
<output>

pcs-clickhouse ping:
<output>

pcs-clickhouse schema-check:
<output>

[Canonical bootstrap]
artifacts/recent-bootstrap.json:
<full JSON, redact only credentials if any>

[Stage 4 preflight]
$LASTEXITCODE:
<number>

evidence/stage4-preflight.json:
<full JSON>

[Operator changes]
<none, or exact configuration changes>

[Errors]
<none, or complete error text with secrets redacted>
```

If any command fails, stop the sequence and return:

```text
1. exact command executed
2. complete PowerShell error output
3. $LASTEXITCODE
4. last successfully completed runbook section
```

Do not include secrets.

## 16. Evidence handling

The following are distinct artifacts and must remain distinct:

- runtime cycle Evidence: `evidence/stage4-runtime-latest.json`;
- campaign latest Evidence: `evidence/stage4-campaign-latest.json`;
- campaign last-success Evidence: `evidence/stage4-campaign-last-success.json`;
- operational status: `artifacts/stage4-runtime-status.json`;
- append-only Shadow ledger: `artifacts/stage4-shadow.sqlite3`.

Operational health is not campaign Evidence. A green health check proves only that the
runtime checkpoint is fresh and semantically healthy enough for the configured check.
