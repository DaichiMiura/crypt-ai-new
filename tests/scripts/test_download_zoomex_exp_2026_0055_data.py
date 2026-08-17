from scripts.download_zoomex_exp_2026_0055_data import (
    SEALED_TARGET_SYMBOLS,
    SOURCE_SUPPLEMENT_SYMBOLS,
    _safe_public_summary,
)


def test_source_and_target_symbols_are_disjoint():
    """学習sourceと最終unseen targetが重複しない。"""

    assert set(SOURCE_SUPPLEMENT_SYMBOLS).isdisjoint(SEALED_TARGET_SYMBOLS)
    assert SEALED_TARGET_SYMBOLS == (
        "BCHUSDT",
        "LTCUSDT",
        "DOTUSDT",
        "DOGEUSDT",
    )


def test_public_summary_hides_market_and_instrument_values():
    """stdout要約へ価格、Funding、instrument仕様を出さない。"""

    metadata = {
        "snapshot_id": "DATA-2026-0007",
        "data_end_exclusive": "2026-08-01T00:00:00+00:00",
        "sealed_holdout": {
            "start_utc": "2026-01-01T00:00:00+00:00",
            "end_utc_exclusive": "2026-08-01T00:00:00+00:00",
        },
        "symbols": [
            {
                "symbol": "BCHUSDT",
                "instrument": {"priceFilter": {"tickSize": "SECRET_SPEC"}},
                "artifacts": {
                    "trade": {
                        "rows": 2,
                        "start_utc": "2021-01-01T00:00:00+00:00",
                        "end_utc": "2026-07-31T23:00:00+00:00",
                        "sha256": "abc",
                        "duplicate_count": 0,
                        "missing_row_count": 0,
                        "missing_segment_count": 0,
                        "interpolated_row_count": 0,
                        "close": "SECRET_PRICE",
                    }
                },
            }
        ],
    }

    summary = _safe_public_summary(metadata)
    rendered = repr(summary)

    assert summary["sealed_target_content_opened"] is False
    assert "SECRET_PRICE" not in rendered
    assert "SECRET_SPEC" not in rendered
