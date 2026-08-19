from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _ensure_common_nested_tmp_dirs(tmp_path):
    # A few integration-style tests intentionally isolate multiple SQLite files
    # under named child directories of pytest's per-test temporary directory.
    for name in ("missing", "mismatch"):
        (tmp_path / name).mkdir(exist_ok=True)
