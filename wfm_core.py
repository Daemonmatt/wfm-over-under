"""Pure WFM logic (no Streamlit). Used by app.py and sanity_check.py."""

from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

import pandas as pd

SAMPLE_DIR = Path(__file__).resolve().parent / "sample_data"
SAMPLE_VOL_CSV = SAMPLE_DIR / "sample_hourly_volume_by_channel.csv"
SAMPLE_STAFF_CSV = SAMPLE_DIR / "sample_staff_by_hour_by_channel.csv"

# Default channels (slug, display label)
CHANNEL_SLUGS: tuple[str, ...] = ("case", "chat")
CHANNEL_LABELS: dict[str, str] = {"case": "Case", "chat": "Chat"}


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
                    "interval": f"{hour_label(h)} · {CHANNEL_LABELS.get(ch, ch)}",
                    "volume": 0.0,
                    "staff_available": 0.0,
                }
            )
    return pd.DataFrame(rows)


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

    up = uploaded[[hcol, vcol]].copy()
    up.columns = ["hour", "volume"]
    up["hour"] = _coerce_hour_series(up["hour"])
    up["volume"] = pd.to_numeric(uploaded[vcol], errors="coerce").fillna(0)

    if chcol is not None:
        up["channel"] = uploaded[chcol].map(_normalize_channel)
        bad = up["channel"].isna()
        if bad.any():
            raise ValueError(
                "Unknown channel value(s). Use case or chat (or ticket/email for case; messaging for chat)."
            )
        up = up.groupby(["hour", "channel"], as_index=False)["volume"].sum()
        df = df.drop(columns=["volume"]).merge(up, on=["hour", "channel"], how="left")
    else:
        up = up.groupby("hour", as_index=False)["volume"].sum()
        df = df.drop(columns=["volume"]).merge(up, on="hour", how="left")
        df["volume"] = df["volume"].fillna(0).astype(float)
        return df

    df["volume"] = df["volume"].fillna(0).astype(float)
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

    up = uploaded[[hcol, scol]].copy()
    up.columns = ["hour", "staff_available"]
    up["hour"] = _coerce_hour_series(uploaded[hcol])
    up["staff_available"] = pd.to_numeric(uploaded[scol], errors="coerce").fillna(0)

    if chcol is not None:
        up["channel"] = uploaded[chcol].map(_normalize_channel)
        if up["channel"].isna().any():
            raise ValueError(
                "Unknown channel value(s). Use case or chat (or ticket/email for case; messaging for chat)."
            )
        up = up.groupby(["hour", "channel"], as_index=False)["staff_available"].sum()
        df = df.drop(columns=["staff_available"]).merge(
            up, on=["hour", "channel"], how="left"
        )
    else:
        up = up.groupby("hour", as_index=False)["staff_available"].sum()
        df = df.drop(columns=["staff_available"]).merge(up, on="hour", how="left")

    df["staff_available"] = df["staff_available"].fillna(0).astype(float)
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
    """Shared floor: split hourly headcount 50/50 across case and chat."""
    df = df_base.copy()
    n = len(CHANNEL_SLUGS)
    for h in HOURS:
        half = counts.get(h, 0.0) / float(n)
        for ch in CHANNEL_SLUGS:
            m = (df["hour"] == h) & (df["channel"] == ch)
            df.loc[m, "staff_available"] = half
    return df


def required_hc_series(
    volume: pd.Series, aht_sec: float, shrinkage: float
) -> pd.Series:
    if shrinkage >= 0.999:
        shrinkage = 0.0
    raw = volume.astype(float) * float(aht_sec) / 3600.0
    return raw / (1.0 - float(shrinkage))


def add_metrics(df: pd.DataFrame, aht_sec: float, shrinkage: float) -> pd.DataFrame:
    d = df.copy()
    d["hc_required"] = required_hc_series(d["volume"], aht_sec, shrinkage)
    d["variance"] = d["staff_available"] - d["hc_required"]
    d["status"] = d["variance"].apply(
        lambda x: "Over" if x > 1e-6 else ("Under" if x < -1e-6 else "Balanced")
    )
    return d


def aggregate_all_channels(df: pd.DataFrame, aht_sec: float, shrinkage: float) -> pd.DataFrame:
    """One row per hour: summed volume & staff; required HC from combined volume."""
    g = (
        df.groupby("hour", as_index=False)
        .agg(
            volume=("volume", "sum"),
            staff_available=("staff_available", "sum"),
        )
        .sort_values("hour")
    )
    g["channel"] = "all"
    g["interval"] = g["hour"].map(hour_label)
    return add_metrics(g, aht_sec, shrinkage)


def filter_by_channel(
    df: pd.DataFrame,
    channel: str,
    aht_sec: float,
    shrinkage: float,
) -> pd.DataFrame:
    if channel == "all":
        return aggregate_all_channels(df, aht_sec, shrinkage)
    return df[df["channel"] == channel].copy()


def template_volume() -> bytes:
    rows = "\n".join([f"{h},{hour_label(h)},0" for h in HOURS])
    return f"hour,interval,volume\n{rows}\n".encode("utf-8")


def template_staff_hour() -> bytes:
    rows = "\n".join([f"{h},{hour_label(h)},0" for h in HOURS])
    return f"hour,interval,staff_available\n{rows}\n".encode("utf-8")


def template_volume_by_channel() -> bytes:
    lines = ["hour,interval,channel,volume"]
    for h in range(8, 18):
        lines.append(f"{h},{hour_label(h)},case,0")
        lines.append(f"{h},{hour_label(h)},chat,0")
    return ("\n".join(lines) + "\n").encode("utf-8")


def template_staff_by_channel() -> bytes:
    lines = ["hour,interval,channel,staff_available"]
    for h in range(8, 18):
        lines.append(f"{h},{hour_label(h)},case,0")
        lines.append(f"{h},{hour_label(h)},chat,0")
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
