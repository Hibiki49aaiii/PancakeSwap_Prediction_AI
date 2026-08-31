-- v0.7 analytical source-of-truth schema.
-- Raw BSC evidence remains immutable in the SQLite/event layer; ClickHouse is for normalized
-- high-volume research data and must never be treated as signing authority.

CREATE TABLE IF NOT EXISTS binance_agg_trades (
    venue LowCardinality(String),
    symbol LowCardinality(String),
    timestamp_unit LowCardinality(String),
    event_timestamp_ms UInt64,
    trade_timestamp_ms UInt64,
    aggregate_trade_id UInt64,
    price_e8 UInt64,
    quantity_e8 UInt64,
    aggressive_side Enum8('buy' = 1, 'sell' = 2),
    source_sha256 FixedString(64),
    source_name String,
    availability_lag_ms UInt32,
    ingest_version UInt64,
    ingested_at DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(ingest_version)
ORDER BY (venue, symbol, timestamp_unit, availability_lag_ms, aggregate_trade_id);

-- ReplacingMergeTree deduplication happens during background merges. Research reads that require
-- immediate retry-safe correctness must query binance_agg_trades FINAL.

CREATE TABLE IF NOT EXISTS chainlink_updates (
    market LowCardinality(String),
    block_number UInt64,
    tx_hash FixedString(66),
    log_index UInt32,
    observed_at_ms UInt64,
    answer Int128,
    oracle_round_id UInt64,
    canonical UInt8,
    ingested_at DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(ingested_at)
ORDER BY (market, block_number, tx_hash, log_index);

CREATE TABLE IF NOT EXISTS pancake_pool_snapshots (
    market LowCardinality(String),
    epoch UInt64,
    snapshot_timestamp_ms UInt64,
    scheduled_lock_timestamp_ms UInt64,
    bull_pool_wei UInt256,
    bear_pool_wei UInt256,
    bet_count UInt32,
    unique_bettors UInt32,
    source_digest FixedString(64)
)
ENGINE = MergeTree
ORDER BY (market, epoch, snapshot_timestamp_ms);

CREATE TABLE IF NOT EXISTS alpha_features (
    market LowCardinality(String),
    epoch UInt64,
    decision_timestamp_ms UInt64,
    feature_set_id LowCardinality(String),
    feature_json String,
    feature_digest FixedString(64),
    created_at DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = MergeTree
ORDER BY (market, epoch, decision_timestamp_ms, feature_set_id);

CREATE TABLE IF NOT EXISTS research_predictions (
    market LowCardinality(String),
    epoch UInt64,
    decision_timestamp_ms UInt64,
    model_id LowCardinality(String),
    feature_set_id LowCardinality(String),
    raw_probability_ppm UInt32,
    calibrated_probability_ppm UInt32,
    train_max_epoch UInt64,
    expected_value_wei Nullable(Int256),
    action Enum8('bull' = 1, 'bear' = 2, 'skip' = 3),
    feature_digest FixedString(64),
    record_digest FixedString(64),
    created_at DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = MergeTree
ORDER BY (market, epoch, decision_timestamp_ms, model_id);
