"""
One-day WFM view: hourly volume upload, staffing (by hour or shifts), required HC
(simple, Erlang C + SLA, or hybrid max), occupancy/utilization, and hourly over- / under-staffing.
Supports channels (e.g. case, chat).
"""

from __future__ import annotations

import io
from datetime import date

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from wfm_core import (
    HCParams,
    SAMPLE_STAFF_CSV,
    SAMPLE_VOL_CSV,
    add_metrics,
    apply_shift_counts_split,
    build_empty_frame,
    filter_view,
    hour_label,
    merge_hourly_volume,
    merge_staff_by_hour,
    sample_pack_zip_bytes,
    shifts_to_hourly_counts,
    summary_hc_case_chat,
    team_display_label,
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
    .wfm-filter-foot { color: #64748b; font-size: 0.88rem; margin-top: 0.35rem; }
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

    st.markdown("### Required HC model")
    hc_model_label = st.radio(
        "Model",
        (
            "Simple workload",
            "Erlang C + SLA",
            "Hybrid (max: workload & SLA)",
        ),
        index=0,
        help="Simple: pure workload FTE. Erlang C: queue/SLA sizing (M/M/c). Hybrid: max(workload, Erlang) — "
        "common in WFM tools to avoid understaffing when one method is optimistic.",
    )
    _model_map = {
        "Simple workload": "simple",
        "Erlang C + SLA": "erlang",
        "Hybrid (max: workload & SLA)": "hybrid",
    }
    hc_model = _model_map[hc_model_label]
    _sla_inputs_active = hc_model in ("erlang", "hybrid")

    st.markdown("### Demand inputs")
    aht_sec = st.number_input(
        "AHT (seconds)",
        min_value=1.0,
        max_value=7200.0,
        value=300.0,
        step=1.0,
        help="Average handle time per work unit (call/chat). Used for workload and Erlang offered load.",
    )
    shrink_pct = st.slider(
        "Shrinkage (%)",
        min_value=0,
        max_value=50,
        value=15,
        help="Non-productive time. After base HC, divide by (1 − shrinkage).",
    )
    shrinkage = shrink_pct / 100.0

    st.markdown("### Occupancy & utilization")
    occ_pct = st.slider(
        "Occupancy target (%)",
        min_value=5,
        max_value=100,
        value=100,
        help="Share of logged-in time spent on handled work. Required HC is divided by this (e.g. 85% → divide by 0.85). 100% = no adjustment.",
    )
    util_pct = st.slider(
        "Utilization target (%)",
        min_value=5,
        max_value=100,
        value=100,
        help="Scheduled productive utilization target. Required HC is divided by this. 100% = no adjustment.",
    )

    st.markdown("### Chat concurrency (messaging)")
    chat_concurrency = st.number_input(
        "Concurrent chats per agent (chat only)",
        min_value=1.0,
        max_value=20.0,
        value=2.0,
        step=0.5,
        help="Typical WFM rule: one agent handles N simultaneous chat sessions. "
        "Required **chat** HC = base requirement ÷ N. **Case** is unchanged. "
        "All channels view sums case HC + chat HC.",
    )

    st.markdown("### Queue SLA (Erlang & Hybrid models)")
    sla_target = st.number_input(
        "SLA target (fraction answered within service time)",
        min_value=0.5,
        max_value=0.999,
        value=0.95,
        step=0.01,
        format="%.3f",
        help="e.g. 0.95 = 95% of contacts answered within the service time threshold.",
        disabled=not _sla_inputs_active,
    )
    service_time_sec = st.number_input(
        "Service time (seconds)",
        min_value=0.0,
        max_value=3600.0,
        value=15.0,
        step=1.0,
        help="Target max answer/wait time for the SLA (e.g. 15 s).",
        disabled=not _sla_inputs_active,
    )

    hc_params = HCParams(
        model=hc_model,
        aht_sec=float(aht_sec),
        shrinkage=float(shrinkage),
        occupancy=occ_pct / 100.0,
        utilization=util_pct / 100.0,
        sla_target=float(sla_target),
        service_time_sec=float(service_time_sec),
        chat_concurrency=float(chat_concurrency),
    )

    st.markdown("---")
    st.markdown("### Overall HC — combined volume")
    st.caption(
        "Optional **day totals** by channel. Required HC uses your selected **Model** (Simple / Erlang / Hybrid) "
        "and sidebar parameters. Totals are divided by **operating hours** to get average hourly volume. "
        "Use **0** to ignore manual volume and show HC from the **hourly grid** only (respects **Team** filter)."
    )
    _ov1, _ov2 = st.columns(2)
    with _ov1:
        manual_case_vol = st.number_input(
            "Case volume (combined)",
            min_value=0.0,
            value=0.0,
            step=1.0,
            key="wfm_manual_case_vol",
            help="Total Case work units for the operating day.",
        )
    with _ov2:
        manual_chat_vol = st.number_input(
            "Chat volume (combined)",
            min_value=0.0,
            value=0.0,
            step=1.0,
            key="wfm_manual_chat_vol",
            help="Total Chat work units for the operating day.",
        )
    spread_hours = st.number_input(
        "Operating hours (spread)",
        min_value=1,
        max_value=24,
        value=24,
        step=1,
        key="wfm_spread_hours",
        help="Divide day totals by this many hours for average hourly workload before HC math.",
    )

    st.markdown("---")
    st.markdown("### Uploads (CSV / Excel)")
    st.caption(
        "Include **channel** (`case` / `chat`) and optional **team** (e.g. `primary`, `north`). "
        "Without **channel**, values apply to both channels; without **team**, rows use team **primary**."
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

df_full = add_metrics(st.session_state.wfm_df.copy(), hc_params)

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


def grid_team_label(t) -> str:
    if str(t) == "all":
        return "All teams"
    return team_display_label(str(t))


ft_col1, ft_col2 = st.columns(2)
with ft_col1:
    channel_filter = st.selectbox(
        "Channel",
        options=["all", "case", "chat"],
        index=0,
        format_func=lambda k: ch_display[k],
        help="All channels: hourly totals across case + chat. Case/Chat: that channel only.",
    )
with ft_col2:
    _teams = sorted(set(df_full["team"].astype(str).unique()))
    team_filter_options = ["all"] + [t for t in _teams if t != "all"]
    team_filter = st.selectbox(
        "Team",
        options=team_filter_options,
        index=0,
        format_func=lambda k: "All teams" if k == "all" else team_display_label(k),
        help="Filter to one team or show all teams (aggregated per hour). Edit grid when both channel and team are specific.",
    )

view_df = filter_view(df_full, channel_filter, team_filter, hc_params)

tab_grid, tab_charts = st.tabs(["Hourly grid", "Charts"])

with tab_grid:
    _readonly = channel_filter == "all" or team_filter == "all"
    if _readonly:
        st.caption(
            "Aggregated view (read-only). Choose a **specific channel** and **specific team** to edit volume and staff in the grid."
        )
        show = pd.DataFrame(
            {
                "Time": view_df["interval"],
                "Channel": view_df["channel"].map(lambda c: ch_display.get(c, str(c))),
                "Team": view_df["team"].map(grid_team_label),
                "Volume (view)": view_df["volume"],
                "HC required (view)": view_df["hc_required"],
                "Staff available (view)": view_df["staff_available"],
                "Variance": view_df["variance"],
                "Status": view_df["status"],
            }
        )
        st.dataframe(show, use_container_width=True, hide_index=True)
    else:
        st.caption(
            f"Editing **{ch_display[channel_filter]} · {team_display_label(team_filter)}**. "
            "Required HC uses the sidebar model and parameters."
        )
        edit_df = view_df[["interval", "volume", "staff_available", "hour"]].copy()
        edit_df.insert(1, "Channel", ch_display[channel_filter])
        edit_df.insert(2, "Team", grid_team_label(team_filter))
        edited = st.data_editor(
            edit_df.set_index("hour"),
            column_config={
                "interval": st.column_config.TextColumn("Time", disabled=True),
                "Channel": st.column_config.TextColumn("Channel", disabled=True),
                "Team": st.column_config.TextColumn("Team", disabled=True),
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
            key=f"grid_{channel_filter}_{team_filter}",
        )
        out = edited.reset_index()
        for _, row in out.iterrows():
            h = int(row["hour"])
            m = (
                (st.session_state.wfm_df["hour"] == h)
                & (st.session_state.wfm_df["channel"] == channel_filter)
                & (st.session_state.wfm_df["team"].astype(str) == team_filter)
            )
            st.session_state.wfm_df.loc[m, "volume"] = float(row["volume"])
            st.session_state.wfm_df.loc[m, "staff_available"] = float(
                row["staff_available"]
            )
        df_full = add_metrics(st.session_state.wfm_df.copy(), hc_params)
        view_df = filter_view(df_full, channel_filter, team_filter, hc_params)

        st.markdown("##### Hourly result")
        disp = pd.DataFrame(
            {
                "Time": view_df["interval"],
                "Channel": view_df["channel"].map(lambda c: ch_display.get(c, str(c))),
                "Team": view_df["team"].map(grid_team_label),
                "Volume": view_df["volume"],
                "HC required": view_df["hc_required"],
                "Staff available": view_df["staff_available"],
                "Variance": view_df["variance"],
                "Status": view_df["status"],
            }
        )
        st.dataframe(disp, use_container_width=True, hide_index=True)

    _ft_team = "All teams" if team_filter == "all" else team_display_label(team_filter)
    st.markdown(
        f'<p class="wfm-filter-foot">Active filters · <strong>Channel:</strong> {ch_display[channel_filter]} · <strong>Team:</strong> {_ft_team}</p>',
        unsafe_allow_html=True,
    )

    st.markdown("##### Summary")
    hc_case, hc_chat, _src_case, _src_chat = summary_hc_case_chat(
        df_full,
        hc_params,
        team_filter,
        manual_case_vol,
        manual_chat_vol,
        spread_hours,
    )
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Total volume (view)", f"{view_df['volume'].sum():,.1f}")
    c2.metric("Hours over-staffed", f"{(view_df['variance'] > 1e-6).sum()}")
    c3.metric("Hours under-staffed", f"{(view_df['variance'] < -1e-6).sum()}")
    if len(view_df) > 0:
        worst = view_df.loc[view_df["variance"].idxmin()]
        c4.metric(
            "Largest gap (under)",
            f"{worst['variance']:.2f}",
            help="Most negative variance in this view",
        )
    else:
        c4.metric("Largest gap (under)", "—")
    c5.metric(
        "Required HC (Case)",
        f"{hc_case:,.2f}",
        help="FTE from **Model** + sidebar inputs. Manual volume → average hourly; else sum of grid HC (Case).",
    )
    c6.metric(
        "Required HC (Chat)",
        f"{hc_chat:,.2f}",
        help="FTE for Chat (includes ÷ concurrent chats). Manual volume → average hourly; else grid sum.",
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
    "**Simple:** workload FTE. **Erlang C + SLA:** queue sizing for the SLA. **Hybrid:** max(simple, Erlang). "
    "**Chat** required HC = base ÷ concurrent chats per agent (case unchanged). "
    "**All channels** sums case HC + chat HC per hour. Shift uploads split headcount evenly between case and chat."
)
