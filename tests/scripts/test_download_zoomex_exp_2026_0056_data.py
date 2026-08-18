import pandas as pd
import pytest

from scripts.download_zoomex_exp_2026_0056_data import (
    SEALED_TARGET_SYMBOLS,
    SOURCE_SYMBOLS,
    _safe_public_summary,
    normalize_15m_rows,
)


def test_source_and_new_target_are_disjoint():
    """既知sourceと新しい未観測targetが重複しない。"""

    assert set(SOURCE_SYMBOLS).isdisjoint(SEALED_TARGET_SYMBOLS)
    assert SEALED_TARGET_SYMBOLS == ("ETCUSDT", "FILUSDT", "TRXUSDT", "XLMUSDT")


def test_normalize_15m_rows_rejects_duplicate():
    """15分足の重複時刻を安全に拒否する。"""

    row = ["1767225600000", "1", "2", "0.5", "1.5", "10", "15"]

    with pytest.raises(ValueError, match="duplicate"):
        normalize_15m_rows([row, row], "TESTUSDT")


def test_public_summary_hides_target_values():
    """公開要約へ価格、Funding、instrument値を含めない。"""

    metadata = {
        "snapshot_id": "DATA-2026-0008", "data_end_exclusive": "2026-08-01T00:00:00Z",
        "symbols": [{
            "symbol": "ETCUSDT", "instrument": {"secret": "SPEC"},
            "artifacts": {"trade_15m": {
                "rows": 1, "start_utc": pd.Timestamp("2022-01-01", tz="UTC").isoformat(),
                "end_utc": pd.Timestamp("2026-07-31T23:45:00", tz="UTC").isoformat(),
                "sha256": "abc", "duplicate_count": 0, "missing_row_count": 0,
                "missing_segment_count": 0, "interpolated_row_count": 0,
                "close": "SECRET_PRICE",
            }},
        }],
    }

    rendered = repr(_safe_public_summary(metadata))

    assert "SECRET_PRICE" not in rendered
    assert "SPEC" not in rendered
