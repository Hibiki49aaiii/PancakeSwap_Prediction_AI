param(
    [string]$OutputPath = "artifacts\stage4-windows-feedback.txt",
    [string]$PreflightPath = "evidence\stage4-preflight.json",
    [string]$BootstrapPath = "artifacts\recent-bootstrap.json",
    [switch]$RunQuality
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Continue"

$lines = [System.Collections.Generic.List[string]]::new()

function Add-Line {
    param([string]$Text = "")
    $script:lines.Add($Text)
}

function Add-Section {
    param([string]$Name)
    Add-Line
    Add-Line "[$Name]"
}

function Invoke-ReportCommand {
    param(
        [string]$Label,
        [scriptblock]$Command,
        [int]$TailLines = 0
    )

    Add-Line "${Label}:"
    try {
        $output = @(& $Command 2>&1)
        $exitCode = $LASTEXITCODE

        if ($TailLines -gt 0 -and $output.Count -gt $TailLines) {
            $output = $output | Select-Object -Last $TailLines
            Add-Line "(showing last $TailLines lines)"
        }

        if ($output.Count -eq 0) {
            Add-Line "<no output>"
        }
        else {
            foreach ($item in $output) {
                Add-Line ([string]$item)
            }
        }

        if ($null -ne $exitCode) {
            Add-Line "exit_code=$exitCode"
        }
    }
    catch {
        Add-Line ("PowerShellError: " + $_.Exception.GetType().Name)
        Add-Line ("message=" + $_.Exception.Message)
    }
}

function Add-SafeBootstrapSummary {
    param([string]$Path)

    Add-Line "bootstrap_path=$Path"
    if (-not (Test-Path -LiteralPath $Path)) {
        Add-Line "bootstrap_file_exists=false"
        return
    }

    Add-Line "bootstrap_file_exists=true"
    try {
        $payload = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json

        Add-Line ("success=" + [string]$payload.success)
        Add-Line ("market=" + [string]$payload.market)
        Add-Line ("chainlink_requested=" + [string]$payload.chainlink_requested)
        Add-Line ("chainlink_collected=" + [string]$payload.chainlink_collected)
        Add-Line ("route_proof_mode=" + [string]$payload.route_proof_mode)

        if ($null -ne $payload.selected) {
            Add-Line ("selected_authenticated=" + [string]$payload.selected.authenticated)
            if ($null -ne $payload.selected.report) {
                Add-Line ("replay_rounds=" + [string]$payload.selected.report.replay_rounds)
                if ($null -ne $payload.selected.report.quality) {
                    Add-Line (
                        "quality=" +
                        ($payload.selected.report.quality | ConvertTo-Json -Compress -Depth 8)
                    )
                }
            }
        }

        if ($null -ne $payload.attempts) {
            $safeAttempts = @(
                foreach ($attempt in $payload.attempts) {
                    [ordered]@{
                        authenticated = $attempt.authenticated
                        outcome = $attempt.outcome
                        error_type = if ($null -eq $attempt.error) {
                            $null
                        }
                        else {
                            ([string]$attempt.error -split ":", 2)[0]
                        }
                    }
                }
            )
            Add-Line (
                "attempts_redacted=" +
                ($safeAttempts | ConvertTo-Json -Compress -Depth 8)
            )
        }
    }
    catch {
        Add-Line ("bootstrap_parse_error=" + $_.Exception.GetType().Name)
    }
}

function Add-Preflight {
    param([string]$Path)

    Add-Line "preflight_path=$Path"
    if (-not (Test-Path -LiteralPath $Path)) {
        Add-Line "preflight_file_exists=false"
        return
    }

    Add-Line "preflight_file_exists=true"
    try {
        $payload = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
        Add-Line ("preflight_json=" + ($payload | ConvertTo-Json -Compress -Depth 100))
    }
    catch {
        Add-Line ("preflight_parse_error=" + $_.Exception.GetType().Name)
    }
}

Add-Line "PancakeSwap Prediction AI - Stage 4 Windows feedback"
Add-Line ("generated_at_utc=" + [DateTimeOffset]::UtcNow.ToString("O"))
Add-Line "secret_values_collected=false"
Add-Line "NOTE: BSC_RPC_URL, API keys, passwords, tokens, private keys and mnemonics are intentionally not read."

Add-Section "Environment"
Invoke-ReportCommand "git --version" { git --version }
Invoke-ReportCommand "py -3.12 --version" { py -3.12 --version }
Invoke-ReportCommand "python --version" { python --version }
Invoke-ReportCommand "docker --version" { docker --version }
Invoke-ReportCommand "docker info" { docker info } 20

Add-Section "Repository"
Invoke-ReportCommand "git rev-parse HEAD" { git rev-parse HEAD }
Invoke-ReportCommand "git branch --show-current" { git branch --show-current }
Invoke-ReportCommand "git status --short --branch" { git status --short --branch }

Add-Section "Project"
Invoke-ReportCommand "pcs-prediction status" { pcs-prediction status }
Invoke-ReportCommand "pcs-shadow-runtime --help" { pcs-shadow-runtime --help } 20

Add-Section "ClickHouse"
Invoke-ReportCommand "docker ps --filter name=pcs-clickhouse" {
    docker ps --filter name=pcs-clickhouse
}
Invoke-ReportCommand "pcs-clickhouse ping" { pcs-clickhouse ping }
Invoke-ReportCommand "pcs-clickhouse schema-check" { pcs-clickhouse schema-check }

if ($RunQuality) {
    Add-Section "Quality"
    Invoke-ReportCommand "ruff check ." { ruff check . } 40
    Invoke-ReportCommand "mypy src tests" { mypy src tests } 40
    Invoke-ReportCommand "pytest coverage" {
        pytest --cov=src/pancake_prediction --cov-report=term-missing
    } 50
    Invoke-ReportCommand "bandit" {
        bandit -c pyproject.toml -r src
    } 40
    Invoke-ReportCommand "pip-audit" { pip-audit } 40
}
else {
    Add-Section "Quality"
    Add-Line "not_run_by_feedback_script=true"
    Add-Line "Run this script with -RunQuality to capture quality command results."
}

Add-Section "CanonicalBootstrap"
Add-SafeBootstrapSummary -Path $BootstrapPath

Add-Section "Stage4Preflight"
Add-Preflight -Path $PreflightPath

Add-Section "ArtifactPresence"
foreach ($path in @(
    "artifacts\bnbusd-stage4.sqlite",
    "artifacts\stage4-shadow.sqlite3",
    "artifacts\stage4-runtime-status.json",
    "evidence\stage4-runtime-latest.json",
    "evidence\stage4-campaign-latest.json",
    "evidence\stage4-campaign-last-success.json"
)) {
    if (Test-Path -LiteralPath $path) {
        $item = Get-Item -LiteralPath $path
        Add-Line ("$path exists=true bytes=" + $item.Length)
    }
    else {
        Add-Line "$path exists=false"
    }
}

$parent = Split-Path -Parent $OutputPath
if ($parent) {
    New-Item -ItemType Directory -Force $parent | Out-Null
}

$lines | Set-Content -LiteralPath $OutputPath -Encoding utf8
Write-Output "Stage 4 feedback written to: $OutputPath"
Write-Output "This script does not read BSC_RPC_URL or other secret environment values."
