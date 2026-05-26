from __future__ import annotations

import sys
from types import SimpleNamespace

import pandas as pd
import pytest

from pipeline import update_ashare_data


def test_fetch_etf_daily_falls_back_to_sina_when_em_disconnects(monkeypatch):
    def broken_em(**_kwargs):
        raise ConnectionError("remote disconnected")

    def sina(symbol: str):
        assert symbol == "sh513100"
        return pd.DataFrame(
            {
                "date": ["2026-04-30", "2026-05-06"],
                "open": [2.0, 2.1],
                "high": [2.1, 2.2],
                "low": [1.9, 2.0],
                "close": [2.05, 2.15],
                "volume": [1000, 2000],
                "amount": [2050, 4300],
            }
        )

    fake_akshare = SimpleNamespace(
        fund_etf_hist_em=broken_em,
        fund_etf_hist_sina=sina,
    )
    monkeypatch.setitem(sys.modules, "akshare", fake_akshare)

    df = update_ashare_data._fetch_etf_daily("513100", start_date="20260501")

    assert list(df.columns) == ["datetime", "open", "high", "low", "close", "volume"]
    assert df["datetime"].dt.tz is not None
    assert df["datetime"].dt.strftime("%Y-%m-%d").tolist() == ["2026-05-06"]
    assert df["close"].tolist() == [2.15]


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("513100", "sh513100"),
        ("159941", "sz159941"),
    ],
)
def test_sina_symbol_uses_exchange_prefix(code, expected):
    assert update_ashare_data._sina_symbol(code) == expected
