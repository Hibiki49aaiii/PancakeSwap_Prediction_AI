from __future__ import annotations

import sqlite3

from .event_store import EventStore


_METADATA_KEY = "reconstruction_dataset_id"
_TRIGGER_NAME = "reconstructed_dataset_namespace_guard"


def reconstruction_dataset_id(store: EventStore) -> str | None:
    if store.mode != "reconstructed":
        raise ValueError("dataset namespace is only valid for reconstructed Event Store")
    row = store._conn.execute(
        "SELECT value FROM store_metadata WHERE key = ?",
        (_METADATA_KEY,),
    ).fetchone()
    return None if row is None else str(row[0])


def _event_dataset_id(store_event) -> str:
    metadata = store_event.event.payload.get("_availability_provenance")
    if not isinstance(metadata, dict):
        raise ValueError(
            f"reconstructed event {store_event.event.event_id} lacks availability provenance"
        )
    if metadata.get("mode") != "reconstructed":
        raise ValueError(
            f"reconstructed store contains non-reconstructed event {store_event.event.event_id}"
        )
    dataset_id = metadata.get("dataset_id")
    if not isinstance(dataset_id, str) or not dataset_id:
        raise ValueError(
            f"reconstructed event {store_event.event.event_id} lacks dataset_id"
        )
    return dataset_id


def bind_reconstruction_dataset(store: EventStore, dataset_id: str) -> str:
    """Permanently bind one reconstructed SQLite file to one dataset ID.

    A persisted SQLite trigger checks provenance on every future INSERT. Once
    bound, even low-level `EventStore.append()` calls cannot mix another
    reconstruction dataset/latency experiment into the same database.
    """

    if store.mode != "reconstructed":
        raise ValueError("dataset namespace binding requires reconstructed Event Store")
    if not isinstance(dataset_id, str) or not dataset_id:
        raise ValueError("dataset_id is required")

    # Persisted metadata is authoritative across process restarts. Check it
    # before inspecting rows so callers receive the same namespace error whether
    # the database is empty or already contains events.
    bound = reconstruction_dataset_id(store)
    if bound is not None and bound != dataset_id:
        raise ValueError(
            f"reconstructed Event Store is bound to dataset {bound}, not {dataset_id}"
        )

    existing_events = store.read_all_ingest_order()
    existing_ids = {_event_dataset_id(item) for item in existing_events}
    if len(existing_ids) > 1:
        raise ValueError(
            f"reconstructed Event Store is already contaminated by dataset IDs: {sorted(existing_ids)}"
        )
    if existing_ids and dataset_id not in existing_ids:
        raise ValueError(
            f"existing reconstructed events belong to dataset {next(iter(existing_ids))}, not {dataset_id}"
        )

    try:
        store._conn.execute("BEGIN IMMEDIATE")
        row = store._conn.execute(
            "SELECT value FROM store_metadata WHERE key = ?",
            (_METADATA_KEY,),
        ).fetchone()
        if row is None:
            store._conn.execute(
                "INSERT INTO store_metadata(key, value) VALUES (?, ?)",
                (_METADATA_KEY, dataset_id),
            )
        elif str(row[0]) != dataset_id:
            raise ValueError(
                f"reconstructed Event Store is bound to dataset {row[0]}, not {dataset_id}"
            )

        # json_extract is part of modern SQLite JSON support used by CPython.
        # The trigger is stored in the database and therefore survives process
        # restarts and protects all subsequent append paths.
        store._conn.execute(
            f"""
            CREATE TRIGGER IF NOT EXISTS {_TRIGGER_NAME}
            BEFORE INSERT ON events
            BEGIN
                SELECT CASE
                    WHEN json_extract(NEW.payload_json, '$._availability_provenance.mode') IS NOT 'reconstructed'
                    THEN RAISE(ABORT, 'reconstructed store requires reconstructed provenance')
                END;
                SELECT CASE
                    WHEN json_extract(NEW.payload_json, '$._availability_provenance.dataset_id')
                         IS NOT (SELECT value FROM store_metadata WHERE key = '{_METADATA_KEY}')
                    THEN RAISE(ABORT, 'reconstructed dataset namespace mismatch')
                END;
            END
            """
        )
        store._conn.commit()
    except sqlite3.DatabaseError:
        store._conn.rollback()
        raise
    except Exception:
        store._conn.rollback()
        raise

    return dataset_id


def verify_reconstruction_dataset_binding(store: EventStore) -> bool:
    if store.mode != "reconstructed":
        return False
    bound = reconstruction_dataset_id(store)
    if bound is None:
        return False
    try:
        if any(_event_dataset_id(item) != bound for item in store.read_all_ingest_order()):
            return False
    except ValueError:
        return False
    trigger = store._conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'trigger' AND name = ?",
        (_TRIGGER_NAME,),
    ).fetchone()
    return trigger is not None