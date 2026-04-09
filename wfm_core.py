"""Pure WFM logic (no Streamlit). Used by app.py and sanity_check.py."""

from __future__ import annotations

import io
import math
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

import pandas as pd


@dataclass(frozen=True)
class HCParams:
    """Parameters for required headcount calculation."""

    model: str  # "simple" | "erlang" | "hybrid"
    aht_sec: float
    shrinkage: float
    occupancy: float  # 0–1 target (e.g. 0.85); 1.0 = no occupancy divisor
    utilization: float  # 0–1; 1.0 = no utilization divisor
    sla_target: float  # 0–1 fraction answered within service time (Erlang path)
    service_time_sec: float  # target answer time cap in seconds (Erlang path)
    chat_concurrency: float  # ≥1; only **chat** HC is divided by this (simultaneous chats per agent)


def _clamp01(x: float, lo: float = 0.05, hi: float = 1.0) -> float:
    return max(lo, min(hi, float(x)))


def erlang_c_delay_probability(A: float, n: int) -> float:
    """Erlang C: probability an arrival waits (all servers busy). A = offered load (Erlangs), n = agents."""
    if n <= 0:
        return 1.0
    if A <= 0:
        return 0.0
    if A >= n:
        return 1.0
    s = 0.0
    term = 1.0
    for _k in range(1, n):
        term *= A / _k
        s += term
    term_n = term * (A / n)
    block = term_n * (n / (n - A))
    denom = s + block
    if denom <= 0:
        return 1.0
    return block / denom


def service_level_mm_c(
    A: float,
    n: int,
    aht_sec: float,
    volume_per_hour: float,
    service_time_sec: float,
) -> float:
    """
    M/M/c-style service level: fraction of calls with wait ≤ service_time_sec.
    P(W ≤ T) = 1 − P(delay) × exp(−(nμ − λ)T), λ = calls/s, μ = 1/AHT.
    """
    lam = volume_per_hour / 3600.0
    mu = 1.0 / max(aht_sec, 1e-9)
    pw = erlang_c_delay_probability(A, n)
    diff = n * mu - lam
    if diff <= 1e-12:
        return max(0.0, min(1.0, 1.0 - pw))
    return max(0.0, min(1.0, 1.0 - pw * math.exp(-diff * service_time_sec)))


def min_agents_erlang_sla(
    volume: float,
    aht_sec: float,
    sla_target: float,
    service_time_sec: float,
) -> float:
    """Smallest integer n (agents) so that SL(service_time) ≥ sla_target; 0 volume → 0."""
    if volume <= 1e-12:
        return 0.0
    aht_sec = max(aht_sec, 1.0)
    service_time_sec = max(service_time_sec, 0.0)
    sla_target = _clamp01(sla_target, 0.01, 0.999)
    A = volume * aht_sec / 3600.0
    n0 = max(1, int(math.floor(A)) + 1)
    for n in range(n0, n0 + 2000):
        if n <= A:
            continue
        sl = service_level_mm_c(A, n, aht_sec, volume, service_time_sec)
        if sl + 1e-9 >= sla_target:
            return float(n)
    return float(n0 + 1999)


def required_hc_simple_hour(
    volume: float,
    aht_sec: float,
    shrinkage: float,
    occupancy: float,
    utilization: float,
) -> float:
    """Workload FTE, inflated for shrinkage, then divided by occupancy & utilization targets."""
    shrinkage = float(shrinkage)
    if shrinkage >= 0.999:
        shrinkage = 0.0
    occ = _clamp01(occupancy)
    util = _clamp01(utilization)
    raw = float(volume) * float(aht_sec) / 3600.0
    return raw / (1.0 - shrinkage) / occ / util


def _required_hc_erlang_inflated(volume: float, p: HCParams) -> float:
    """Erlang C minimum agents for SLA, then shrinkage / occupancy / utilization."""
    n = min_agents_erlang_sla(
        volume,
        p.aht_sec,
        p.sla_target,
        p.service_time_sec,
    )
    shrink = p.shrinkage
    if shrink >= 0.999:
        shrink = 0.0
    occ = _clamp01(p.occupancy)
    util = _clamp01(p.utilization)
    return n / (1.0 - shrink) / occ / util


def _chat_concurrency_factor(p: HCParams) -> float:
    c = float(p.chat_concurrency)
    return max(1.0, c)


def summary_hc_case_chat(
    df_full: pd.DataFrame,
    p: HCParams,
    team_filter: str,
    manual_case_vol: float,
    manual_chat_vol: float,
    spread_hours: int,
) -> tuple[float, float, str, str]:
    """
    Overall required HC for Case vs Chat for the day.

    If manual volume for a channel is > 0, HC is computed from **average hourly** volume
    (manual total ÷ spread_hours) using ``required_hc_for_volume`` (same model as the grid).

    If manual is 0, uses the sum of ``hc_required`` from ``df_full`` for that channel (optional team filter).
    """
    sh = max(1, int(spread_hours))
    d = df_full.copy()
    if "hc_required" not in d.columns:
        d = add_metrics(d, p)
    if team_filter != "all":
        d = d[d["team"].astype(str) == str(team_filter)]
    grid_case = float(d[d["channel"] == "case"]["hc_required"].sum())
    grid_chat = float(d[d["channel"] == "chat"]["hc_required"].sum())

    if float(manual_case_vol) > 1e-12:
        hc_c = required_hc_for_volume(float(manual_case_vol) / float(sh), p, "case")
        src_c = "Manual ÷ hours"
    else:
        hc_c = grid_case
        src_c = "Grid sum"

    if float(manual_chat_vol) > 1e-12:
        hc_h = required_hc_for_volume(float(manual_chat_vol) / float(sh), p, "chat")
        src_h = "Manual ÷ hours"
    else:
        hc_h = grid_chat
        src_h = "Grid sum"

    return hc_c, hc_h, src_c, src_h


def required_hc_for_volume(
    volume: float,
    p: HCParams,
    channel: str | None = None,
) -> float:
    """
    Required HC for one hourly bucket and channel.

    **Chat** headcount uses standard WFM practice: compute base HC (workload / Erlang / hybrid),
    then divide by **chat concurrency** (simultaneous live chats per agent). **Case** is unchanged.
    """
    if volume <= 0:
        return 0.0
    wl = required_hc_simple_hour(
        volume,
        p.aht_sec,
        p.shrinkage,
        p.occupancy,
        p.utilization,
    )
    if p.model == "simple":
        base = wl
    elif p.model == "erlang":
        base = _required_hc_erlang_inflated(volume, p)
    elif p.model == "hybrid":
        base = max(wl, _required_hc_erlang_inflated(volume, p))
    else:
        base = wl
    if channel == "chat":
        return base / _chat_concurrency_factor(p)
    return base

SAMPLE_DIR = Path(__file__).resolve().parent / "sample_data"
SAMPLE_VOL_CSV = SAMPLE_DIR / "sample_hourly_volume_by_channel.csv"
SAMPLE_STAFF_CSV = SAMPLE_DIR / "sample_staff_by_hour_by_channel.csv"

# Default channels (slug, display label)
CHANNEL_SLUGS: tuple[str, ...] = ("case", "chat")
CHANNEL_LABELS: dict[str, str] = {"case": "Case", "chat": "Chat"}

# Default team bucket when CSV has no team column (slug; avoids clash with filter "all teams")
DEFAULT_TEAM_SLUG = "primary"


def team_display_label(slug: str) -> str:
    if str(slug) == DEFAULT_TEAM_SLUG:
        return "Primary"
    s = str(slug).strip().replace("_", " ")
    return s.title() if s else slug


def _normalize_team(val) -> str:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return DEFAULT_TEAM_SLUG
    s = str(val).strip().lower().replace(" ", "_")
    s = re.sub(r"[^a-z0-9_]", "", s)
    return s if s else DEFAULT_TEAM_SLUG


def _interval_label(hour: int, _channel: str | None = None, _team: str | None = None) -> str:
    """Clock time only; channel and team are separate columns in the grid."""
    return hour_label(int(hour))


def sample_pack_zip_bytes() -> bytes:
    """ZIP of bundled sample volume + staffing CSVs (by channel)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        if SAMPLE_VOL_CSV.is_file():
            zf.write(SAMPLE_VOL_CSV, arcname="sample_hourly_volume_by_channel.csv")
        if SAMPLE_STAFF_CSV.is_file():
            zf.write(SAMPLE_STAFF_CSV, arcname="sample_staff_by_hour_by_channel.csv")
    return buf.getvalue()


HOURS = list(range(24))


def hour_label(h: int) -> str:
    if h == 0:
        return "12:00 AM"
    if h < 12:
        return f"{h}:00 AM"
    if h == 12:
        return "12:00 PM"
    return f"{h - 12}:00 PM"


def _norm_col(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def _find_column(df: pd.DataFrame, candidates: list[str]) -> str | None:
    normalized = {_norm_col(c): c for c in df.columns}
    for cand in candidates:
        key = _norm_col(cand)
        if key in normalized:
            return normalized[key]
    return None


def _normalize_channel(val) -> str | None:
    if pd.isna(val):
        return None
    s = str(val).strip().lower()
    if s in ("case", "cases", "ticket", "tickets", "email", "async"):
        return "case"
    if s in ("chat", "chats", "messaging", "live_chat", "live chat", "livechat"):
        return "chat"
    return None


def _coerce_hour_series(s: pd.Series) -> pd.Series:
    out = pd.to_numeric(s, errors="coerce")
    if out.notna().all() and (out >= 0).all() and (out <= 23).all():
        return out.astype(int)
    parsed = pd.to_datetime(s.astype(str), errors="coerce")
    if parsed.notna().any():
        return parsed.dt.hour.fillna(0).astype(int)
    return out.fillna(0).astype(int).clip(0, 23)


def build_empty_frame() -> pd.DataFrame:
    rows = []
    for ch in CHANNEL_SLUGS:
        for h in HOURS:
            rows.append(
                {
                    "hour": h,
                    "channel": ch,
                    "team": DEFAULT_TEAM_SLUG,
                    "interval": _interval_label(h, ch, DEFAULT_TEAM_SLUG),
                    "volume": 0.0,
                    "staff_available": 0.0,
                }
            )
    return pd.DataFrame(rows)


def _ensure_team_row(df: pd.DataFrame, hour: int, channel: str, team: str) -> pd.DataFrame:
    """Append (hour, channel, team) row if missing."""
    m = (
        (df["hour"] == hour)
        & (df["channel"] == channel)
        & (df["team"].astype(str) == team)
    )
    if m.any():
        return df
    new_row = {
        "hour": hour,
        "channel": channel,
        "team": team,
        "interval": _interval_label(hour, channel, team),
        "volume": 0.0,
        "staff_available": 0.0,
    }
    return pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)


def merge_hourly_volume(df_base: pd.DataFrame, uploaded: pd.DataFrame) -> pd.DataFrame:
    df = df_base.copy()
    hcol = _find_column(uploaded, ["hour", "hr", "hourofday", "intervalhour"])
    if hcol is None:
        raise ValueError("Add an hour column (0–23), e.g. hour.")
    vcol = _find_column(
        uploaded,
        ["volume", "volumearrival", "contacts", "calls", "workload", "arrivals"],
    )
    if vcol is None:
        raise ValueError("Add a volume column, e.g. volume or contacts.")
    chcol = _find_column(uploaded, ["channel", "queue", "media", "type", "skill"])
    tcol = _find_column(uploaded, ["team", "squad", "group", "pod", "teamname"])

    up = uploaded.copy()
    up["_hour"] = _coerce_hour_series(up[hcol])
    up["_vol"] = pd.to_numeric(up[vcol], errors="coerce").fillna(0)
    up["_team"] = up[tcol].map(_normalize_team) if tcol is not None else DEFAULT_TEAM_SLUG

    rows_agg: list[dict] = []
    for _, r in up.iterrows():
        h = int(r["_hour"])
        vol = float(r["_vol"])
        team = str(r["_team"])
        if chcol is not None:
            ch = _normalize_channel(r[chcol])
            if ch is None:
                raise ValueError(
                    "Unknown channel value(s). Use case or chat (or ticket/email for case; messaging for chat)."
                )
            rows_agg.append({"hour": h, "channel": ch, "team": team, "volume": vol})
        else:
            for ch in CHANNEL_SLUGS:
                rows_agg.append({"hour": h, "channel": ch, "team": team, "volume": vol})

    agg = pd.DataFrame(rows_agg)
    if agg.empty:
        return df
    agg = agg.groupby(["hour", "channel", "team"], as_index=False)["volume"].sum()

    for _, r in agg.iterrows():
        h, ch, t = int(r["hour"]), str(r["channel"]), str(r["team"])
        v = float(r["volume"])
        df = _ensure_team_row(df, h, ch, t)
        m = (df["hour"] == h) & (df["channel"] == ch) & (df["team"].astype(str) == t)
        df.loc[m, "volume"] = v
        df.loc[m, "interval"] = _interval_label(h, ch, t)
    return df


def merge_staff_by_hour(df_base: pd.DataFrame, uploaded: pd.DataFrame) -> pd.DataFrame:
    df = df_base.copy()
    hcol = _find_column(uploaded, ["hour", "hr", "hourofday"])
    if hcol is None:
        raise ValueError("Add an hour column (0–23).")
    scol = _find_column(
        uploaded,
        [
            "staffavailable",
            "staff",
            "staffing",
            "available",
            "headcount",
            "fte",
            "scheduled",
            "roster",
            "agents",
        ],
    )
    if scol is None:
        raise ValueError("Add a staffing column, e.g. staff_available or headcount.")
    chcol = _find_column(uploaded, ["channel", "queue", "media", "type", "skill"])
    tcol = _find_column(uploaded, ["team", "squad", "group", "pod", "teamname"])

    up = uploaded.copy()
    up["_hour"] = _coerce_hour_series(up[hcol])
    up["_st"] = pd.to_numeric(up[scol], errors="coerce").fillna(0)
    up["_team"] = up[tcol].map(_normalize_team) if tcol is not None else DEFAULT_TEAM_SLUG

    rows_agg: list[dict] = []
    for _, r in up.iterrows():
        h = int(r["_hour"])
        stf = float(r["_st"])
        team = str(r["_team"])
        if chcol is not None:
            ch = _normalize_channel(r[chcol])
            if ch is None:
                raise ValueError(
                    "Unknown channel value(s). Use case or chat (or ticket/email for case; messaging for chat)."
                )
            rows_agg.append(
                {"hour": h, "channel": ch, "team": team, "staff_available": stf}
            )
        else:
            for ch in CHANNEL_SLUGS:
                rows_agg.append(
                    {"hour": h, "channel": ch, "team": team, "staff_available": stf}
                )

    agg = pd.DataFrame(rows_agg)
    if agg.empty:
        return df
    agg = agg.groupby(["hour", "channel", "team"], as_index=False)["staff_available"].sum()

    for _, r in agg.iterrows():
        h, ch, t = int(r["hour"]), str(r["channel"]), str(r["team"])
        stf = float(r["staff_available"])
        df = _ensure_team_row(df, h, ch, t)
        m = (df["hour"] == h) & (df["channel"] == ch) & (df["team"].astype(str) == t)
        df.loc[m, "staff_available"] = stf
        df.loc[m, "interval"] = _interval_label(h, ch, t)
    return df


def _parse_hour_value(val) -> float | None:
    if pd.isna(val):
        return None
    if isinstance(val, (int, float)) and not pd.isna(val):
        h = float(val)
        if 0 <= h <= 23:
            return h
    s = str(val).strip()
    m = re.match(r"^(\d{1,2})(?::(\d{2}))?\s*(AM|PM|am|pm)?$", s)
    if m:
        hh = int(m.group(1))
        mm = int(m.group(2) or 0)
        ap = m.group(3)
        if ap:
            ap = ap.upper()
            if ap == "AM" and hh == 12:
                hh = 0
            elif ap == "PM" and hh != 12:
                hh += 12
        frac = hh + mm / 60.0
        return min(max(frac, 0.0), 23.999)
    ts = pd.to_datetime(s, errors="coerce")
    if pd.notna(ts):
        return float(ts.hour) + ts.minute / 60.0 + ts.second / 3600.0
    return None


def _bucket_covers_hour(hi: int, start_h: float, end_h: float) -> bool:
    return max(start_h, float(hi)) < min(end_h, float(hi + 1))


def hours_covered(start_h: float, end_h: float) -> set[int]:
    out: set[int] = set()
    if end_h > start_h:
        for hi in HOURS:
            if _bucket_covers_hour(hi, start_h, end_h):
                out.add(hi)
        return out
    if end_h <= start_h and (start_h > 0 or end_h > 0):
        for hi in HOURS:
            if hi >= start_h:
                if _bucket_covers_hour(hi, start_h, 24.0):
                    out.add(hi)
            if hi < end_h:
                if _bucket_covers_hour(hi, 0.0, end_h):
                    out.add(hi)
        return out
    return out


def shifts_to_hourly_counts(uploaded: pd.DataFrame) -> pd.Series:
    sc = _find_column(
        uploaded,
        ["starthour", "start", "shiftstart", "shift_start", "from", "begin"],
    )
    ec = _find_column(
        uploaded,
        ["endhour", "end", "shiftend", "shift_end", "to", "finish"],
    )
    if sc is None or ec is None:
        raise ValueError(
            "Shift file needs start and end columns (e.g. start_hour, end_hour or 9:00 AM, 5:00 PM)."
        )
    counts = pd.Series(0.0, index=HOURS, dtype=float)
    for _, row in uploaded.iterrows():
        sh = _parse_hour_value(row[sc])
        eh = _parse_hour_value(row[ec])
        if sh is None or eh is None:
            continue
        for hi in hours_covered(sh, eh):
            counts[hi] += 1.0
    return counts


def apply_shift_counts_split(df_base: pd.DataFrame, counts: pd.Series) -> pd.DataFrame:
    """Shared floor: split hourly headcount 50/50 across case and chat; split each half across teams on that channel."""
    df = df_base.copy()
    n_ch = len(CHANNEL_SLUGS)
    for h in HOURS:
        half = counts.get(h, 0.0) / float(n_ch)
        for ch in CHANNEL_SLUGS:
            idx = df.index[(df["hour"] == h) & (df["channel"] == ch)]
            if len(idx) == 0:
                continue
            per = half / float(len(idx))
            for i in idx:
                df.loc[i, "staff_available"] = per
    return df


def required_hc_series(
    volume: pd.Series,
    p: HCParams,
    channel: str = "case",
) -> pd.Series:
    """Required HC per row when a single channel applies to the whole series (e.g. tests)."""
    return volume.astype(float).map(
        lambda v: required_hc_for_volume(float(v), p, channel)
    )


def add_metrics(df: pd.DataFrame, p: HCParams) -> pd.DataFrame:
    d = df.copy()
    d["hc_required"] = [
        required_hc_for_volume(float(v), p, str(ch))
        for v, ch in zip(d["volume"].astype(float), d["channel"].astype(str))
    ]
    d["variance"] = d["staff_available"] - d["hc_required"]
    d["status"] = d["variance"].apply(
        lambda x: "Over" if x > 1e-6 else ("Under" if x < -1e-6 else "Balanced")
    )
    return d


def aggregate_all_channels(df: pd.DataFrame, p: HCParams) -> pd.DataFrame:
    """All channels & all teams: one row per hour."""
    return filter_view(df, "all", "all", p)


def filter_view(
    df: pd.DataFrame,
    channel: str,
    team: str,
    p: HCParams,
) -> pd.DataFrame:
    """Filter by channel and/or team; aggregate to one row per hour when multiple rows match."""
    d = df.copy()
    if "hc_required" not in d.columns:
        d = add_metrics(d, p)
    if team != "all":
        d = d[d["team"].astype(str) == team]
    if channel != "all":
        d = d[d["channel"] == channel]
    if d.empty:
        return d
    if len(d) > d["hour"].nunique():
        g = (
            d.groupby("hour", as_index=False)
            .agg(
                volume=("volume", "sum"),
                staff_available=("staff_available", "sum"),
                hc_required=("hc_required", "sum"),
            )
            .sort_values("hour")
        )
        g["channel"] = channel
        g["team"] = team
        g["interval"] = g["hour"].map(hour_label)
        g["variance"] = g["staff_available"] - g["hc_required"]
        g["status"] = g["variance"].apply(
            lambda x: "Over" if x > 1e-6 else ("Under" if x < -1e-6 else "Balanced")
        )
        return g
    d["variance"] = d["staff_available"] - d["hc_required"]
    d["status"] = d["variance"].apply(
        lambda x: "Over" if x > 1e-6 else ("Under" if x < -1e-6 else "Balanced")
    )
    return d


def filter_by_channel(
    df: pd.DataFrame,
    channel: str,
    p: HCParams,
    team: str = "all",
) -> pd.DataFrame:
    """Backward-compatible wrapper."""
    return filter_view(df, channel, team, p)


def template_volume() -> bytes:
    rows = "\n".join([f"{h},{hour_label(h)},0" for h in HOURS])
    return f"hour,interval,volume\n{rows}\n".encode("utf-8")


def template_staff_hour() -> bytes:
    rows = "\n".join([f"{h},{hour_label(h)},0" for h in HOURS])
    return f"hour,interval,staff_available\n{rows}\n".encode("utf-8")


def template_volume_by_channel() -> bytes:
    lines = ["hour,interval,channel,team,volume"]
    for h in range(8, 18):
        lines.append(f"{h},{hour_label(h)},case,primary,0")
        lines.append(f"{h},{hour_label(h)},chat,primary,0")
    return ("\n".join(lines) + "\n").encode("utf-8")


def template_staff_by_channel() -> bytes:
    lines = ["hour,interval,channel,team,staff_available"]
    for h in range(8, 18):
        lines.append(f"{h},{hour_label(h)},case,primary,0")
        lines.append(f"{h},{hour_label(h)},chat,primary,0")
    return ("\n".join(lines) + "\n").encode("utf-8")


def template_shifts() -> bytes:
    lines = [
        "agent_id,start_hour,end_hour",
        "Example1,8,17",
        "Example2,9,17",
        "Example3,8,16",
    ]
    note = (
        "# end_hour is exclusive: 8–17 means hours 8–16. "
        "Headcount is split 50/50 between case and chat. Overnight: 22,6 → 22,23,0–5.\n"
    )
    return (note + "\n".join(lines) + "\n").encode("utf-8")
