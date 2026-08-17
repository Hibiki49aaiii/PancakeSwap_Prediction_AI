from pathlib import Path

from pancake_prediction.research_inputs import load_canonical_research_inputs
from pancake_prediction.store import EventStore


def test_canonical_research_inputs_require_and_report_oracle_anchor(tmp_path: Path) -> None:
    database = tmp_path / "history.sqlite3"
    store = EventStore(database)
    store.initialize()
    store.record_metadata("BNBUSD.oracle_anchor_block", "100")
    store.record_metadata("BNBUSD.oracle_anchor_address", "0x" + "11" * 20)

    inputs = load_canonical_research_inputs(database, "BNBUSD")

    assert inputs.market == "BNBUSD"
    assert inputs.replay.rounds == ()
    assert inputs.events == ()
    assert inputs.prediction_event_count == 0
    assert inputs.oracle_history.anchor.block_number == 100
    assert inputs.oracle_history.anchor.address == "0x" + "11" * 20
    assert inputs.as_dict()["active_chainlink_event_count"] == 0
