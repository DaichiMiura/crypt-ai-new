import pytest
import pandas as pd

from scripts.download_zoomex_exp_2026_0058_premium_data import (
    DATA_END,
    DATA_START,
    normalize_premium_rows,
    safe_public_summary,
)


def _rows() -> list[list[str]]:
    """正規化テスト用の逆順premium行を返す。"""

    return [
        [str(int((DATA_END.timestamp() - 900) * 1000)), "-0.001", "0.002", "-0.002", "0.001"],
        [str(int(DATA_START.timestamp() * 1000)), "0", "0.001", "-0.001", "-0.0005"],
    ]


def test_normalize_premium_accepts_signed_values_and_reports_gap():
    """premiumの負値を保持し、欠測をmetadataだけへ記録する。"""

    frame, quality = normalize_premium_rows(_rows(), "LINKUSDT")

    assert list(frame["close"]) == [-0.0005, 0.001]
    assert not frame["is_interpolated"].any()
    assert quality["missing_segment_count"] == 1
    assert quality["interpolated_row_count"] == 0


def test_normalize_premium_rejects_duplicate_timestamp():
    """同一時刻のpremium行を拒否する。"""

    rows = _rows()
    rows.append(rows[-1])

    with pytest.raises(ValueError, match="duplicate premium rows"):
        normalize_premium_rows(rows, "LINKUSDT")


def test_safe_summary_does_not_contain_premium_values():
    """封印取得の表示にpremium OHLCを含めない。"""

    artifact = {
        "rows": 2,
        "start_utc": DATA_START.isoformat(),
        "end_utc": (DATA_END - pd.Timedelta(minutes=15)).isoformat(),
        "sha256": "abc",
        "duplicate_count": 0,
        "missing_row_count": 0,
        "missing_segment_count": 0,
        "interpolated_row_count": 0,
        "minimum": -0.5,
    }
    metadata = {
        "snapshot_id": "DATA-2026-0009",
        "data_end_exclusive": DATA_END.isoformat(),
        "symbols": [{"symbol": "ETCUSDT", "artifact": artifact}],
    }

    summary = safe_public_summary(metadata)

    assert summary["sealed_target_content_opened"] is False
    assert "minimum" not in summary["symbols"]["ETCUSDT"]
