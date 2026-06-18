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


def test_compute_adj_close_uses_date_specific_cum_nav_factor():
    raw = pd.DataFrame({
        "datetime": pd.to_datetime(
            ["2022-01-12", "2022-01-13", "2022-01-14"],
            utc=True,
        ),
        "open": [5.10, 1.02, 1.03],
        "high": [5.20, 1.04, 1.05],
        "low": [5.00, 1.00, 1.01],
        "close": [5.10, 1.03, 1.04],
        "volume": [100, 500, 400],
    })
    nav = pd.DataFrame({
        "datetime": pd.to_datetime(
            ["2022-01-12", "2022-01-13", "2022-01-14"],
            utc=True,
        ),
        "nav": [5.10, 1.03, 1.04],
        "cum_nav": [5.10, 5.15, 5.20],
    })
    raw["datetime"] = raw["datetime"].astype("datetime64[us, UTC]")
    nav["datetime"] = nav["datetime"].astype("datetime64[s, UTC]")

    adjusted = update_ashare_data._compute_adj_close(raw, nav)
    returns = adjusted["adj_close"].pct_change().dropna()

    assert adjusted["adj_close"].tolist() == pytest.approx([5.10, 5.15, 5.20])
    assert returns.abs().max() < 0.02


def test_compute_adj_close_delays_factor_until_price_split_day():
    raw = pd.DataFrame({
        "datetime": pd.to_datetime(
            ["2022-07-01", "2022-07-04", "2022-07-05", "2022-07-06"],
            utc=True,
        ),
        "open": [2.37, 2.384, 0.604, 0.611],
        "high": [2.38, 2.390, 0.610, 0.617],
        "low": [2.36, 2.370, 0.600, 0.608],
        "close": [2.37, 2.384, 0.604, 0.611],
        "volume": [100, 120, 500, 300],
    })
    nav = pd.DataFrame({
        "datetime": pd.to_datetime(
            ["2022-07-01", "2022-07-04", "2022-07-05", "2022-07-06"],
            utc=True,
        ),
        "nav": [2.37, 0.596, 0.604, 0.611],
        "cum_nav": [2.37, 2.384, 2.416, 2.444],
    })

    adjusted = update_ashare_data._compute_adj_close(raw, nav)
    returns = adjusted["adj_close"].pct_change().dropna()

    assert adjusted["adj_close"].tolist() == pytest.approx([2.37, 2.384, 2.416, 2.444])
    assert returns.abs().max() < 0.02


def test_merge_existing_prefers_refetched_overlap_and_recomputes_adj_close():
    existing = pd.DataFrame({
        "datetime": pd.to_datetime(
            ["2026-06-03", "2026-06-04"],
            utc=True,
        ),
        "open": [2.30, 2.24],
        "high": [2.35, 2.28],
        "low": [2.25, 2.20],
        "close": [2.30, 2.24],
        "volume": [100, 500],
        "adj_close": [11.50, 2.24],
    })
    refetched = pd.DataFrame({
        "datetime": pd.to_datetime(
            ["2026-06-04", "2026-06-05"],
            utc=True,
        ),
        "open": [2.24, 2.21],
        "high": [2.28, 2.25],
        "low": [2.20, 2.20],
        "close": [2.24, 2.22],
        "volume": [500, 300],
    })
    nav = pd.DataFrame({
        "datetime": pd.to_datetime(
            ["2026-06-03", "2026-06-04", "2026-06-05"],
            utc=True,
        ),
        "nav": [2.30, 2.07, 2.08],
        "cum_nav": [11.50, 10.35, 10.40],
    })

    combined = update_ashare_data._merge_existing_daily(existing, refetched, nav)

    assert combined.loc[combined["datetime"] == pd.Timestamp("2026-06-04", tz="UTC"), "adj_close"].iloc[0] == pytest.approx(11.2)
    assert combined.loc[combined["datetime"] == pd.Timestamp("2026-06-05", tz="UTC"), "adj_close"].iloc[0] == pytest.approx(11.1)


# --- B1: Pinned split factor tests ---

def test_pinned_factor_overrides_nav_derived_factor(monkeypatch):
    """When split_factors.yaml has an entry, its factor takes precedence over NAV."""
    # Simulate: NAV says factor=1 (corrupted), but pinned says factor=5
    raw = pd.DataFrame({
        "datetime": pd.to_datetime(
            ["2026-06-10", "2026-06-11", "2026-06-12"],
            utc=True,
        ),
        "open": [2.14, 2.12, 2.21],
        "high": [2.17, 2.13, 2.22],
        "low": [2.13, 2.09, 2.16],
        "close": [2.143, 2.130, 2.162],
        "volume": [4000000, 4800000, 4700000],
    })
    # Bad NAV: cum_nav ≈ nav → factor ≈ 1
    bad_nav = pd.DataFrame({
        "datetime": pd.to_datetime(
            ["2026-06-10", "2026-06-11", "2026-06-12"],
            utc=True,
        ),
        "nav": [1.94, 2.00, 2.02],
        "cum_nav": [1.94, 2.00, 2.02],  # corrupted: should be ~9.7, ~10.0, ~10.1
    })

    # Inject pinned factor for 513100
    monkeypatch.setattr(
        update_ashare_data,
        "PINNED_SPLITS",
        {"513100": [{"event_date": pd.Timestamp("2022-01-14", tz="UTC"), "factor": 5.0, "pre_factor": 1.0}]},
    )

    adjusted = update_ashare_data._compute_adj_close(raw, nav_df=bad_nav, code="513100")

    # Pinned factor=5 should win over NAV-derived factor=1
    assert adjusted["adj_close"].iloc[0] == pytest.approx(2.143 * 5, rel=1e-4)
    assert adjusted["adj_close"].iloc[1] == pytest.approx(2.130 * 5, rel=1e-4)
    assert adjusted["adj_close"].iloc[2] == pytest.approx(2.162 * 5, rel=1e-4)


def test_pinned_factor_applies_pre_factor_before_event_date(monkeypatch):
    """Dates before the split event use pre_factor (typically 1.0)."""
    raw = pd.DataFrame({
        "datetime": pd.to_datetime(
            ["2022-01-12", "2022-01-13", "2022-01-14", "2022-01-17"],
            utc=True,
        ),
        "open": [5.0, 5.1, 1.0, 1.02],
        "high": [5.1, 5.2, 1.05, 1.04],
        "low": [4.9, 5.0, 0.98, 1.00],
        "close": [5.048, 5.10, 1.01, 1.03],
        "volume": [100, 120, 500, 400],
    })

    monkeypatch.setattr(
        update_ashare_data,
        "PINNED_SPLITS",
        {"513100": [{"event_date": pd.Timestamp("2022-01-14", tz="UTC"), "factor": 5.0, "pre_factor": 1.0}]},
    )

    adjusted = update_ashare_data._compute_adj_close(raw, code="513100")

    # Before event: adj_close = close * 1.0
    assert adjusted["adj_close"].iloc[0] == pytest.approx(5.048, rel=1e-4)
    assert adjusted["adj_close"].iloc[1] == pytest.approx(5.10, rel=1e-4)
    # On/after event: adj_close = close * 5.0
    assert adjusted["adj_close"].iloc[2] == pytest.approx(1.01 * 5, rel=1e-4)
    assert adjusted["adj_close"].iloc[3] == pytest.approx(1.03 * 5, rel=1e-4)


# --- B3: Regression guard tests ---

def test_regression_guard_rejects_large_adj_close_drift():
    """If recomputed adj_close diverges >10% from existing, raise ValueError."""
    existing = pd.DataFrame({
        "datetime": pd.to_datetime(
            ["2026-06-09", "2026-06-10", "2026-06-11"],
            utc=True,
        ),
        "close": [2.20, 2.143, 2.13],
        "adj_close": [11.0, 10.715, 10.65],  # factor=5
    })
    # Simulates corrupted recomputation (factor=1)
    recomputed = pd.DataFrame({
        "datetime": pd.to_datetime(
            ["2026-06-09", "2026-06-10", "2026-06-11"],
            utc=True,
        ),
        "close": [2.20, 2.143, 2.13],
        "adj_close": [2.20, 2.143, 2.13],  # factor=1 (broken!)
    })

    with pytest.raises(ValueError, match="REGRESSION GUARD"):
        update_ashare_data._check_adj_close_regression(existing, recomputed, "513100")


def test_regression_guard_allows_small_drift():
    """Small differences (<10%) from rounding or NAV adjustments are OK."""
    existing = pd.DataFrame({
        "datetime": pd.to_datetime(
            ["2026-06-09", "2026-06-10"],
            utc=True,
        ),
        "close": [2.20, 2.143],
        "adj_close": [11.0, 10.715],
    })
    # Tiny drift (< 1%) — acceptable
    recomputed = pd.DataFrame({
        "datetime": pd.to_datetime(
            ["2026-06-09", "2026-06-10"],
            utc=True,
        ),
        "close": [2.20, 2.143],
        "adj_close": [11.05, 10.75],  # ~0.5% drift
    })

    # Should NOT raise
    update_ashare_data._check_adj_close_regression(existing, recomputed, "513100")


def test_regression_guard_allows_first_run():
    """First run (no existing data) should never trigger the guard."""
    recomputed = pd.DataFrame({
        "datetime": pd.to_datetime(["2026-06-09"], utc=True),
        "close": [2.20],
        "adj_close": [2.20],
    })

    # Empty existing — should NOT raise
    update_ashare_data._check_adj_close_regression(pd.DataFrame(), recomputed, "513100")
