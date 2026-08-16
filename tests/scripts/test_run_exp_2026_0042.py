"""EXP-2026-0042ランナーをテストする。"""

import pandas as pd

from scripts.run_exp_2026_0042 import _apply_last_rank_exit, _live_ranks


def _frames() -> dict[str, pd.DataFrame]:
    """最下位転落テスト用の4銘柄DataFrameを作る。"""

    times = pd.date_range("2026-01-01", periods=5, freq="2h", tz="UTC")
    values = {
        "AAA": [0.4, 0.4, 0.1, 0.1, 0.1],
        "BBB": [0.3, 0.3, 0.4, 0.4, 0.4],
        "CCC": [0.2, 0.2, 0.3, 0.3, 0.3],
        "DDD": [0.1, 0.1, 0.2, 0.2, 0.2],
    }
    return {
        symbol: pd.DataFrame(
            {
                "event_time": times,
                "momentum_return": momentum,
                "desired_long_position": [0, 1, 1, 1, 0],
            }
        )
        for symbol, momentum in values.items()
    }


def test_live_ranks_are_recomputed_each_bar() -> None:
    """毎足のモメンタムから順位が再計算されることをテストする。"""

    ranks = _live_ranks(_frames())

    assert ranks["AAA"][1] == 1
    assert ranks["AAA"][2] == 4


def test_last_rank_exit_occurs_on_next_bar() -> None:
    """最下位triggerの次足で退出することをテストする。"""

    result = _apply_last_rank_exit(_frames())["AAA"]

    assert result.loc[2, "last_rank_exit_trigger"]
    assert result.loc[2, "desired_long_position"] == 1
    assert result.loc[3, "desired_long_position"] == 0


def test_last_rank_exit_blocks_reentry_until_base_reset() -> None:
    """退出後にbaseが0になるまで再entryしないことをテストする。"""

    result = _apply_last_rank_exit(_frames())["AAA"]

    assert result.loc[3, "base_desired_long_position"] == 1
    assert result.loc[3, "desired_long_position"] == 0
    assert result.loc[4, "base_desired_long_position"] == 0
