from pathlib import Path

from pancake_prediction.quality import build_quality_report
from pancake_prediction.store import EventStore


def test_empty_quality_report(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite3"
    EventStore(db).initialize()
    report = build_quality_report(db, "BNBUSD")
    assert report.starts == 0
    assert report.ends == 0
    assert report.unresolved_started_rounds == 0
