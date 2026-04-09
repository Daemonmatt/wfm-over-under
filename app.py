"""
One-day WFM view: hourly volume upload, staffing (by hour or shifts), required HC from AHT,
and hourly over- / under-staffing vs available roster. Supports channels (e.g. case, chat).
"""

from __future__ import annotations

import io
from datetime import date

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from wfm_core import (
    SAMPLE_STAFF_CSV,
    SAMPLE_VOL_CSV,
    add_metrics,
    apply_shift_counts_split,
    build_empty_frame,
    filter_by_channel,
    hour_label,
    merge_hourly_volume,
    merge_staff_by_hour,
    sample_pack_zip_bytes,
    shifts_to_hourly_counts,
    template_shifts,
    template_staff_by_channel,
    template_staff_hour,
    template_volume,
    template_volume_by_channel,
)

# --- Page ---
st.set_page_config(
    page_title="WFM — Daily staffing vs demand",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
<style>
    .block-container { padding-top: 1.1rem; max-width: 1380px; }
    div[data-testid="stMetricValue"] { font-size: 1.45rem; }
    .wfm-title { font-size: 1.6rem; font-weight: 650; letter-spacing: -0.02em;
      color: #0f172a; margin-bottom: 0.1rem; }
    .wfm-sub { color: #64748b; font-size: 0.92rem; margin-bottom: 1rem; }
</style>
""",
    unsafe_allow_html=True,
)

if "plan_date" not in st.session_state:
    st.session_state.plan_date = date.today()
if "wfm_df" not in st.session_state:
    st.session_state.wfm_df = build_empty_frame()


def reset_day():
    st.session_state.wfm_df = build_empty_frame()


with st.sidebar:
    st.markdown("### Operating day")
    plan_date = st.date_input(
        "Date",
        value=st.session_state.plan_date,
        help="Single-day plan: 24 hourly buckets (12:00 AM – 11:00 PM).",
    )
    st.session_state.plan_date = plan_date

    st.markdown("### Demand → required HC")
    aht_sec = st.number_input(
        "AHT (seconds)",
        min_value=1.0,
        max_value=7200.0,
        value=300.0,
        step=1.0,
        help="Required FTE per hour ≈ (volume × AHT) ÷ 3600, before shrinkage (same for all channels unless you export and model separately).",
    )
    shrink_pct = st.slider(
        "Shrinkage (%)",
        min_value=0,
        max_value=50,
        value=15,
        help="Non-productive time (breaks, meetings). Required HC = raw ÷ (1 − shrinkage).",
    )
    shrinkage = shrink_pct / 100.0

    st.markdown("---")
    st.markdown("### Uploads (CSV / Excel)")
    st.caption(
        "Include a **channel** column (`case` or `chat`; also accepts ticket/email → case, messaging → chat). "
        "Legacy files without **channel** apply the same values to **both** channels."
    )

    f_vol = st.file_uploader("① Hourly volume", type=["csv", "xlsx", "xls"])
    staff_mode = st.radio(
        "② Staffing format",
        ("By hour (headcount per hour)", "By shift (one row per agent shift)"),
        help="By hour: hour + staff (+ optional channel). Shifts: floor count split 50/50 between case and chat.",
    )
    f_staff = st.file_uploader("② Staffing file", type=["csv", "xlsx", "xls"])

    if st.button("Clear loaded data", use_container_width=True):
        reset_day()
        st.rerun()

    st.markdown("---")
    st.markdown("### Templates")
    st.download_button(
        "Volume (legacy, no channel)",
        data=template_volume(),
        file_name="wfm_hourly_volume_template.csv",
        mime="text/csv",
        use_container_width=True,
    )
    st.download_button(
        "Volume by channel (case / chat)",
        data=template_volume_by_channel(),
        file_name="wfm_hourly_volume_by_channel_template.csv",
        mime="text/csv",
        use_container_width=True,
    )
    st.download_button(
        "Staffing (legacy, no channel)",
        data=template_staff_hour(),
        file_name="wfm_staff_by_hour_template.csv",
        mime="text/csv",
        use_container_width=True,
    )
    st.download_button(
        "Staffing by channel",
        data=template_staff_by_channel(),
        file_name="wfm_staff_by_hour_by_channel_template.csv",
        mime="text/csv",
        use_container_width=True,
    )
    st.download_button(
        "Shift roster (example)",
        data=template_shifts(),
        file_name="wfm_shift_roster_template.csv",
        mime="text/csv",
        use_container_width=True,
    )

    st.markdown("---")
    st.markdown("### Try sample data")
    st.caption("AHT **300** s, shrinkage **15%**. Upload ① then ② (by hour), with channel columns.")
    if SAMPLE_VOL_CSV.is_file() and SAMPLE_STAFF_CSV.is_file():
        csa, csb = st.columns(2)
        with csa:
            st.download_button(
                "Sample volume (by channel)",
                data=SAMPLE_VOL_CSV.read_bytes(),
                file_name="sample_hourly_volume_by_channel.csv",
                mime="text/csv",
                use_container_width=True,
            )
        with csb:
            st.download_button(
                "Sample staffing (by channel)",
                data=SAMPLE_STAFF_CSV.read_bytes(),
                file_name="sample_staff_by_hour_by_channel.csv",
                mime="text/csv",
                use_container_width=True,
            )
        st.download_button(
            "Both samples (ZIP)",
            data=sample_pack_zip_bytes(),
            file_name="wfm_sample_volume_and_staff_by_channel.zip",
            mime="application/zip",
            use_container_width=True,
        )
    else:
        st.caption("Sample files not found on disk (expected under `sample_data/`).")


def read_upload(f) -> pd.DataFrame:
    name = f.name.lower()
    if name.endswith(".csv"):
        return pd.read_csv(f, comment="#")
    return pd.read_excel(f)


err: list[str] = []
if f_vol is not None:
    try:
        st.session_state.wfm_df = merge_hourly_volume(
            st.session_state.wfm_df, read_upload(f_vol)
        )
    except Exception as e:
        err.append(f"Volume: {e}")

if f_staff is not None:
    try:
        raw = read_upload(f_staff)
        if staff_mode.startswith("By hour"):
            st.session_state.wfm_df = merge_staff_by_hour(st.session_state.wfm_df, raw)
        else:
            counts = shifts_to_hourly_counts(raw)
            st.session_state.wfm_df = apply_shift_counts_split(
                st.session_state.wfm_df, counts
            )
    except Exception as e:
        err.append(f"Staffing: {e}")

for e in err:
    st.error(e)

df_full = add_metrics(st.session_state.wfm_df.copy(), aht_sec, shrinkage)

st.markdown(
    '<p class="wfm-title">Daily staffing vs demand</p>', unsafe_allow_html=True
)
st.markdown(
    f'<p class="wfm-sub">Single day: <strong>{plan_date.strftime("%A, %B %d, %Y")}</strong> · '
    "Channels: <strong>case</strong> & <strong>chat</strong>. "
    "<strong>Variance</strong> = available staff − required HC.</p>",
    unsafe_allow_html=True,
)

ch_display = {"all": "All channels", "case": "Case", "chat": "Chat"}
channel_filter = st.selectbox(
    "Channel",
    options=["all", "case", "chat"],
    index=0,
    format_func=lambda k: ch_display[k],
    help="All channels: hourly totals. Case/Chat: that channel only (edit grid here).",
)

view_df = filter_by_channel(df_full, channel_filter, aht_sec, shrinkage)

tab_grid, tab_charts = st.tabs(["Hourly grid", "Charts"])

with tab_grid:
    if channel_filter == "all":
        st.caption(
            "Totals combine **case + chat**. Select **Case** or **Chat** in the dropdown above to edit values."
        )
        show = view_df[
            [
                "interval",
                "volume",
                "hc_required",
                "staff_available",
                "variance",
                "status",
            ]
        ].copy()
        show.columns = [
            "Time",
            "Volume (total)",
            "HC required (total)",
            "Staff available (total)",
            "Variance",
            "Status",
        ]
        st.dataframe(show, use_container_width=True, hide_index=True)
    else:
        st.caption(
            f"Editing **{ch_display[channel_filter]}**. Required HC uses the same AHT and shrinkage as the sidebar."
        )
        edit_df = view_df[
            ["interval", "volume", "staff_available", "hour", "channel"]
        ].copy()
        edited = st.data_editor(
            edit_df.set_index("hour"),
            column_config={
                "interval": st.column_config.TextColumn("Time", disabled=True),
                "channel": st.column_config.TextColumn("Channel", disabled=True),
                "volume": st.column_config.NumberColumn(
                    "Volume (work units)", min_value=0.0, format="%.1f"
                ),
                "staff_available": st.column_config.NumberColumn(
                    "Staff available", min_value=0.0, format="%.2f"
                ),
            },
            hide_index=False,
            use_container_width=True,
            num_rows="fixed",
            key=f"grid_{channel_filter}",
        )
        out = edited.reset_index()
        for _, row in out.iterrows():
            h = int(row["hour"])
            ch = str(row["channel"])
            m = (st.session_state.wfm_df["hour"] == h) & (
                st.session_state.wfm_df["channel"] == ch
            )
            st.session_state.wfm_df.loc[m, "volume"] = float(row["volume"])
            st.session_state.wfm_df.loc[m, "staff_available"] = float(
                row["staff_available"]
            )
        df_full = add_metrics(st.session_state.wfm_df.copy(), aht_sec, shrinkage)
        view_df = filter_by_channel(df_full, channel_filter, aht_sec, shrinkage)

        st.markdown("##### Hourly result")
        disp = view_df[
            [
                "interval",
                "volume",
                "hc_required",
                "staff_available",
                "variance",
                "status",
            ]
        ].copy()
        disp.columns = [
            "Time",
            "Volume",
            "HC required",
            "Staff available",
            "Variance",
            "Status",
        ]
        st.dataframe(disp, use_container_width=True, hide_index=True)

    st.markdown("##### Summary")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total volume (view)", f"{view_df['volume'].sum():,.1f}")
    c2.metric("Hours over-staffed", f"{(view_df['variance'] > 1e-6).sum()}")
    c3.metric("Hours under-staffed", f"{(view_df['variance'] < -1e-6).sum()}")
    worst = view_df.loc[view_df["variance"].idxmin()]
    c4.metric(
        "Largest gap (under)",
        f"{worst['variance']:.2f}",
        help="Most negative variance in this view",
    )

    x1, x2 = st.columns(2)
    csv_out = df_full.to_csv(index=False).encode("utf-8")
    x1.download_button(
        "Export CSV (all channels)",
        data=csv_out,
        file_name=f"wfm_day_{plan_date.isoformat()}_all_channels.csv",
        mime="text/csv",
        use_container_width=True,
    )
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df_full.to_excel(writer, sheet_name="Hourly", index=False)
    x2.download_button(
        "Export Excel (all channels)",
        data=buf.getvalue(),
        file_name=f"wfm_day_{plan_date.isoformat()}_all_channels.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )

with tab_charts:
    # Short, consistent time labels on X (avoid long "interval" strings crowding the axis)
    chart_x = view_df["hour"].astype(int).map(hour_label).tolist()
    ch_name = ch_display[channel_filter]

    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.16,
        row_heights=[0.52, 0.48],
        subplot_titles=(
            f"Staff vs requirement (FTE) · {ch_name}",
            "Variance (FTE)",
        ),
    )
    fig.add_trace(
        go.Scatter(
            x=chart_x,
            y=view_df["staff_available"],
            name="Staff available",
            mode="lines+markers",
            line=dict(color="#059669", width=2.5),
            marker=dict(size=7, line=dict(width=0.5, color="#ffffff")),
            hovertemplate="%{x}<br>Staff: %{y:.2f}<extra></extra>",
        ),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=chart_x,
            y=view_df["hc_required"],
            name="HC required",
            mode="lines+markers",
            line=dict(color="#b91c1c", width=2.5),
            marker=dict(size=7, line=dict(width=0.5, color="#ffffff")),
            hovertemplate="%{x}<br>Required: %{y:.2f}<extra></extra>",
        ),
        row=1,
        col=1,
    )
    colors = ["#15803d" if v >= 0 else "#b91c1c" for v in view_df["variance"]]
    fig.add_trace(
        go.Bar(
            x=chart_x,
            y=view_df["variance"],
            name="Variance",
            marker_color=colors,
            opacity=0.92,
            hovertemplate="%{x}<br>Variance: %{y:.2f}<extra></extra>",
        ),
        row=2,
        col=1,
    )
    fig.add_hline(
        y=0,
        line_dash="dash",
        line_width=1.5,
        line_color="#64748b",
        row=2,
        col=1,
    )

    _axis_text = "#0f172a"
    _grid = "#e2e8f0"
    _line = "#94a3b8"

    # Shared tick styling; only the *bottom* subplot gets the x-axis title (avoids duplicate / overlap)
    x_axis_ticks = dict(
        title=dict(text=""),
        tickangle=-40,
        tickfont=dict(size=11, color=_axis_text),
        showticklabels=True,
        showgrid=True,
        gridcolor=_grid,
        gridwidth=1,
        showline=True,
        linewidth=1.2,
        linecolor=_line,
        mirror=False,
        automargin=True,
    )
    x_axis_bottom = {
        **x_axis_ticks,
        "title": dict(text="Hour of day", font=dict(size=13, color=_axis_text)),
    }
    y_top = dict(
        title=dict(text="FTE (headcount)", font=dict(size=13, color=_axis_text)),
        tickfont=dict(size=11, color=_axis_text),
        showgrid=True,
        gridcolor=_grid,
        showline=True,
        linewidth=1.2,
        linecolor=_line,
        zeroline=False,
    )
    y_bot = dict(
        title=dict(text="Variance (FTE)", font=dict(size=13, color=_axis_text)),
        tickfont=dict(size=11, color=_axis_text),
        showgrid=True,
        gridcolor=_grid,
        showline=True,
        linewidth=1.2,
        linecolor=_line,
        zeroline=True,
        zerolinewidth=1,
        zerolinecolor="#cbd5e1",
    )

    fig.update_layout(
        height=720,
        showlegend=True,
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.06,
            x=0,
            xanchor="left",
            bgcolor="rgba(255,255,255,0.97)",
            bordercolor="#cbd5e1",
            borderwidth=1,
            font=dict(size=12, color=_axis_text),
        ),
        margin=dict(l=64, r=32, t=72, b=132),
        paper_bgcolor="#f8fafc",
        plot_bgcolor="#ffffff",
        font=dict(family="system-ui, -apple-system, sans-serif", color=_axis_text, size=12),
    )
    fig.update_xaxes(x_axis_ticks, row=1, col=1)
    fig.update_xaxes(x_axis_bottom, row=2, col=1)
    fig.update_xaxes(title_standoff=32, row=2, col=1)
    fig.update_xaxes(showticklabels=True, row=1, col=1)
    fig.update_yaxes(y_top, row=1, col=1)
    fig.update_yaxes(y_bot, row=2, col=1)

    fig.update_annotations(
        font=dict(size=13, color=_axis_text, family="system-ui, -apple-system, sans-serif")
    )

    st.plotly_chart(fig, use_container_width=True)
    st.caption(
        "Variance chart: **green** = over-staffed (surplus), **red** = under-staffed (gap vs requirement)."
    )

st.markdown("---")
st.caption(
    "**HC required** = (hourly volume × AHT ÷ 3600) ÷ (1 − shrinkage). "
    "**All channels** sums case + chat per hour. Shift uploads split headcount evenly between case and chat."
)
