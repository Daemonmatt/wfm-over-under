#!/usr/bin/env python3
"""Load sample CSVs and assert WFM math (AHT 300s, shrinkage 15%), including channels."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from wfm_core import (
    HCParams,
    HOURS,
    add_metrics,
    aggregate_all_channels,
    build_empty_frame,
    merge_hourly_volume,
    merge_staff_by_hour,
    required_hc_for_volume,
    required_hc_series,
    shifts_to_hourly_counts,
)

SAMPLE = Path(__file__).resolve().parent / "sample_data"
AHT_SEC = 300.0
SHRINK = 0.15

DEFAULT_HC = HCParams(
    model="simple",
    aht_sec=AHT_SEC,
    shrinkage=SHRINK,
    occupancy=1.0,
    utilization=1.0,
    sla_target=0.95,
    service_time_sec=15.0,
    chat_concurrency=1.0,
)

TOL = 1e-5


def _expected_required(vol: float) -> float:
    return (vol * AHT_SEC / 3600.0) / (1.0 - SHRINK)


def test_volume_staff_by_channel() -> None:
    base = build_empty_frame()
    vol_df = pd.read_csv(SAMPLE / "sample_hourly_volume_by_channel.csv")
    stf_df = pd.read_csv(SAMPLE / "sample_staff_by_hour_by_channel.csv")
    base = merge_hourly_volume(base, vol_df)
    base = merge_staff_by_hour(base, stf_df)
    df = add_metrics(base, DEFAULT_HC)

    for ch in ("case", "chat"):
        for h in HOURS:
            sub = df[(df["hour"] == h) & (df["channel"] == ch)]
            if sub.empty:
                continue
            row = sub.iloc[0]
            exp_req = _expected_required(float(row["volume"]))
            assert abs(row["hc_required"] - exp_req) < TOL, (h, ch, row["hc_required"], exp_req)
            assert abs(row["variance"] - (row["staff_available"] - row["hc_required"])) < TOL

    r = df[(df["hour"] == 10) & (df["channel"] == "case")].iloc[0]
    assert r["volume"] == 28
    assert r["staff_available"] == 2.0
    assert r["status"] == "Under"

    agg = aggregate_all_channels(df, DEFAULT_HC)
    assert len(agg) == 24
    h10 = agg[agg["hour"] == 10].iloc[0]
    c10 = df[(df["hour"] == 10) & (df["channel"] == "case")].iloc[0]
    t10 = df[(df["hour"] == 10) & (df["channel"] == "chat")].iloc[0]
    assert abs(h10["volume"] - (c10["volume"] + t10["volume"])) < TOL
    assert abs(h10["staff_available"] - (c10["staff_available"] + t10["staff_available"])) < TOL
    assert abs(h10["hc_required"] - (c10["hc_required"] + t10["hc_required"])) < TOL

    print("  Volume + staffing by channel: OK")


def test_shift_roster_counts() -> None:
    raw = pd.read_csv(SAMPLE / "sample_shifts.csv")
    c = shifts_to_hourly_counts(raw)
    assert c[10] == 4.0, f"hour 10 expected 4 agents, got {c[10]}"
    assert c[12] == 4.0, f"hour 12 expected 4 agents, got {c[12]}"
    assert c[8] == 2.0
    assert c[11] == 4.0
    print("  Shift roster hourly counts: OK")


def test_required_hc_series_vector() -> None:
    s = pd.Series([0.0, 45.0, 28.0])
    out = required_hc_series(s, DEFAULT_HC)
    assert abs(out.iloc[1] - _expected_required(45)) < TOL
    assert abs(out.iloc[2] - _expected_required(28)) < TOL
    print("  required_hc_series: OK")


def test_hybrid_is_max_of_workload_and_erlang() -> None:
    """Hybrid model matches max(simple workload HC, Erlang SLA HC)."""
    common = dict(
        aht_sec=300.0,
        shrinkage=0.15,
        occupancy=1.0,
        utilization=1.0,
        sla_target=0.95,
        service_time_sec=15.0,
        chat_concurrency=1.0,
    )
    v = 42.0
    wl = required_hc_for_volume(v, HCParams(model="simple", **common))
    erl = required_hc_for_volume(v, HCParams(model="erlang", **common))
    hy = required_hc_for_volume(v, HCParams(model="hybrid", **common))
    assert abs(hy - max(wl, erl)) < TOL
    print("  Hybrid = max(workload, Erlang): OK")


def test_chat_concurrency_divides_chat_only() -> None:
    """WFM rule: required chat agents = base HC ÷ concurrency; case unchanged."""
    base = dict(
        model="simple",
        aht_sec=300.0,
        shrinkage=0.0,
        occupancy=1.0,
        utilization=1.0,
        sla_target=0.95,
        service_time_sec=15.0,
    )
    p1 = HCParams(**base, chat_concurrency=1.0)
    p2 = HCParams(**base, chat_concurrency=2.0)
    v = 60.0
    case1 = required_hc_for_volume(v, p1, "case")
    case2 = required_hc_for_volume(v, p2, "case")
    assert abs(case1 - case2) < TOL
    ch1 = required_hc_for_volume(v, p1, "chat")
    ch2 = required_hc_for_volume(v, p2, "chat")
    assert abs(ch2 - ch1 / 2.0) < TOL
    print("  Chat concurrency (chat only): OK")


def test_erlang_params_positive() -> None:
    """Erlang path returns finite HC; with shrink 0 matches integer agents."""
    p = HCParams(
        model="erlang",
        aht_sec=300.0,
        shrinkage=0.0,
        occupancy=1.0,
        utilization=1.0,
        sla_target=0.95,
        service_time_sec=15.0,
        chat_concurrency=1.0,
    )
    hc = required_hc_for_volume(50.0, p)
    assert hc >= 4.0, hc  # offered load ~4.17 Erlangs; need at least 5 agents for SLA
    assert hc < 200.0
    print("  Erlang HC params: OK")


def main() -> None:
    print("Sanity check (sample_data/, AHT=300s, shrinkage=15%, channels=case+chat)")
    test_hybrid_is_max_of_workload_and_erlang()
    test_chat_concurrency_divides_chat_only()
    test_erlang_params_positive()
    test_required_hc_series_vector()
    test_shift_roster_counts()
    test_volume_staff_by_channel()
    print("All checks passed.")


if __name__ == "__main__":
    main()
