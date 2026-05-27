"""
Medicaid Provider Spending Analytics — Streamlit dashboard.

Reads pre-aggregated marts from the Neon `medicaid_analytics` schema and presents
five insights across three sections:

  Section 1 — Providers
    1. Top providers by spend (ranked table + bar chart; year + state filters)
    2. One provider's spend trend YOY (search by name or NPI)

  Section 2 — Cost trends by procedure
    3. Single HCPCS cost-per-beneficiary trend (code + state selector; chart + table)
    4. Largest YOY cost-per-beneficiary increases (state filter)

  Section 3 — Trend analytics
    5. Cumulative % change vs CAGR scatter (sized by years observed)
"""
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
from db import run_query, SCHEMA

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Medicaid Provider Spending Analytics",
    page_icon="📊",
    layout="wide",
)

# Force a light theme + larger base font, regardless of the viewer's OS setting.
st.markdown(
    """
    <style>
      .stApp { background-color: #ffffff; color: #1a1f29; }
      html, body, [class*="css"] { font-size: 17px; }
      .stMarkdown p, .stMarkdown li { font-size: 1.05rem; line-height: 1.6; }
      h1 { font-size: 2.3rem !important; }
      h2 { font-size: 1.7rem !important; }
      h3 { font-size: 1.3rem !important; }
      .stDataFrame { font-size: 1.02rem; }
      /* Right-justify all dataframe cells and column headers */
      .stDataFrame [data-testid="stTable"] td,
      .stDataFrame [data-testid="stTable"] th,
      div[data-testid="stDataFrame"] [role="gridcell"],
      div[data-testid="stDataFrame"] [role="columnheader"] {
          text-align: right !important;
          justify-content: flex-end !important;
      }
      div[data-testid="stDataFrame"] [role="columnheader"] > div {
          justify-content: flex-end !important;
      }
      label, .stSelectbox label, .stTextInput label, .stSlider label {
          font-size: 1.05rem !important; font-weight: 600;
      }
      div[data-testid="stCaptionContainer"] p { font-size: 0.98rem !important; }
    </style>
    """,
    unsafe_allow_html=True,
)

# A distinct color per chart so the visuals don't all look alike.
PALETTES = {
    "provider_bar": "#1f6feb",          # blue
    "provider_trend": "#0a9396",        # teal
    "hcpcs_trend": "#ca6702",           # amber
    "yoy_increase": "Tealgrn",          # plotly sequential
    "scatter": "Plasma_r",              # reversed: dark at high end, visible on white
}

DEFAULT_NPI = "1689744450"
DEFAULT_HCPCS = "T1019"


def fmt_millions(value: float) -> str:
    """Format a dollar amount in millions, e.g. 1234567 -> '$1.23M'."""
    if value is None or value != value:  # None or NaN
        return "—"
    return f"${value / 1_000_000:,.1f}M"

# Canonical 50 states + DC. Used to scrub territory codes / junk from the
# state dropdowns.
US_STATES = {
    "AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN","IA",
    "KS","KY","LA","ME","MD","MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ",
    "NM","NY","NC","ND","OH","OK","OR","PA","RI","SC","SD","TN","TX","UT","VT",
    "VA","WA","WV","WI","WY","DC",
}

# ---------------------------------------------------------------------------
# Header + caveats
# ---------------------------------------------------------------------------
st.title("Medicaid Outpatient & Professional Spending")
st.markdown(
    "Analysis of the HHS Medicaid Provider Spending dataset (2018–2024), "
    "transformed through a dbt pipeline and served from Neon Postgres."
)

with st.expander("About this data — read before interpreting", expanded=False):
    st.markdown(
        """
        **Scope.** This dataset covers **outpatient and professional claims only** —
        Medicaid fee-for-service, Medicaid managed care, and CHIP. It **excludes
        inpatient hospital stays** (DRG-coded), long-term care, and pharmacy.

        **State attribution.** State is derived from the billing provider's NPPES
        registered practice address, not the location where the service was rendered.
        Multi-state systems introduce some attribution error.

        **Suppression.** Source rows with fewer than 12 total claims per
        provider–HCPCS–month are dropped, so very low-volume relationships are absent.

        **Cost per beneficiary.** Computed as total paid ÷ total beneficiaries. The same
        beneficiary can be counted across multiple providers for the same code, so this is
        an *upper-bound approximation* of true per-person cost. Trend metrics (YOY %, CAGR)
        are excluded where cost-per-beneficiary is non-positive in either endpoint
        (claim reversals can produce negative paid amounts).
        """
    )

# ---------------------------------------------------------------------------
# Shared filter data — scrubbed to real states + DC
# ---------------------------------------------------------------------------
_raw_states = run_query(
    f"SELECT DISTINCT practice_state FROM {SCHEMA}.mart_spend_by_state_month "
    f"ORDER BY practice_state"
)["practice_state"].tolist()
states = [s for s in _raw_states if s in US_STATES]

years = run_query(
    f"SELECT DISTINCT claim_year FROM {SCHEMA}.mart_top_providers_yoy "
    f"ORDER BY claim_year DESC"
)["claim_year"].tolist()

_raw_hcpcs_states = run_query(
    f"SELECT DISTINCT practice_state FROM {SCHEMA}.mart_cost_per_bene_hcpcs_yoy "
    f"ORDER BY practice_state"
)["practice_state"].tolist()
# Real states for the cost marts, with National offered explicitly as the first option.
hcpcs_state_options = ["National"] + [s for s in _raw_hcpcs_states if s in US_STATES]


def hcpcs_state_value(label: str) -> str:
    """Map the UI label back to the value stored in the mart."""
    return "NATIONAL" if label == "National" else label


# ===========================================================================
# SECTION 0 — GEOGRAPHY & SPEND OVERVIEW
# ===========================================================================
st.header("Geography & spend overview")

# --- Chart A: Total spend per year + YOY ------------------------------------
st.subheader("Total spend by year")
st.caption("National or state-level Medicaid outpatient and professional spend over time, "
           "with year-over-year change.")

a_scope = st.selectbox("Geography", ["National"] + states, index=0, key="a_scope")

if a_scope == "National":
    df = run_query(
        f"""
        SELECT EXTRACT(YEAR FROM claim_month)::int AS claim_year,
               SUM(total_paid)::numeric AS total_paid
        FROM {SCHEMA}.mart_spend_by_state_month
        GROUP BY EXTRACT(YEAR FROM claim_month)
        ORDER BY claim_year
        """
    )
else:
    df = run_query(
        f"""
        SELECT EXTRACT(YEAR FROM claim_month)::int AS claim_year,
               SUM(total_paid)::numeric AS total_paid
        FROM {SCHEMA}.mart_spend_by_state_month
        WHERE practice_state = %s
        GROUP BY EXTRACT(YEAR FROM claim_month)
        ORDER BY claim_year
        """,
        (a_scope,),
    )

if df.empty:
    st.info("No data for that geography.")
else:
    df["total_paid"] = df["total_paid"].astype(float)
    df = df.sort_values("claim_year").reset_index(drop=True)
    df["yoy_pct"] = df["total_paid"].pct_change() * 100

    # Line chart: spend over time
    line = go.Figure()
    line.add_trace(go.Scatter(
        x=df["claim_year"], y=df["total_paid"],
        mode="lines+markers+text",
        line=dict(color="#1f6feb", width=3, shape="spline"),
        marker=dict(size=10, color="#1f6feb"),
        text=df["total_paid"].map(fmt_millions),
        textposition="top center",
        textfont=dict(size=13, color="#1f6feb"),
        hovertemplate="Year %{x}<br>%{text}<extra></extra>",
    ))
    line.update_layout(
        height=380, xaxis=dict(dtick=1),
        yaxis=dict(tickprefix="$", tickformat=".2s", title="Total Paid"),
        margin=dict(l=10, r=10, t=40, b=10),
        plot_bgcolor="rgba(0,0,0,0)", font=dict(size=14),
    )
    st.plotly_chart(line, use_container_width=True)

    # YOY bar beneath
    bars = df.dropna(subset=["yoy_pct"]).copy()
    if not bars.empty:
        bars["color"] = bars["yoy_pct"].map(lambda v: "#0a9396" if v >= 0 else "#bb3e03")
        yoy_fig = go.Figure()
        yoy_fig.add_trace(go.Bar(
            x=bars["claim_year"], y=bars["yoy_pct"],
            marker_color=bars["color"],
            text=bars["yoy_pct"].map(lambda v: f"{v:+.1f}%"),
            textposition="outside",
            textfont=dict(size=13),
            hovertemplate="Year %{x}<br>%{text} vs prior year<extra></extra>",
        ))
        yoy_fig.update_layout(
            height=280, xaxis=dict(dtick=1, title=""),
            yaxis=dict(ticksuffix="%", title="YOY Change"),
            margin=dict(l=10, r=10, t=20, b=10),
            plot_bgcolor="rgba(0,0,0,0)", font=dict(size=14),
            showlegend=False,
        )
        st.plotly_chart(yoy_fig, use_container_width=True)

st.divider()

# --- Chart B: State choropleth ----------------------------------------------
st.subheader("Spend by state — map")
st.caption("Total paid and average cost per beneficiary across states. "
           "Average is computed as total paid ÷ total beneficiaries; the same beneficiary "
           "can be counted across multiple provider–HCPCS relationships, so this is "
           "an upper-bound approximation.")

c1, c2 = st.columns(2)
with c1:
    map_metric = st.selectbox(
        "Metric", ["Total paid", "Avg cost per beneficiary"], index=0, key="map_metric"
    )
with c2:
    map_year = st.slider(
        "Year", min_value=int(min(years)), max_value=int(max(years)),
        value=int(max(years)), key="map_year",
    )

map_df = run_query(
    f"""
    SELECT practice_state,
           SUM(total_paid)::numeric AS total_paid,
           SUM(total_beneficiaries)::bigint AS total_beneficiaries
    FROM {SCHEMA}.mart_spend_by_state_month
    WHERE EXTRACT(YEAR FROM claim_month) = %s
    GROUP BY practice_state
    """,
    (int(map_year),),
)

# Scrub to real states (drops territories etc.)
map_df = map_df[map_df["practice_state"].isin(US_STATES)].copy()

if map_df.empty:
    st.info("No data for that year.")
else:
    map_df["total_paid"] = map_df["total_paid"].astype(float)
    map_df["avg_cpb"] = (
        map_df["total_paid"] / map_df["total_beneficiaries"].replace(0, float("nan"))
    )

    if map_metric == "Total paid":
        color_col = "total_paid"
        color_label = "Total Paid"
        hover_fmt = map_df["total_paid"].map(fmt_millions)
        color_scale = "Blues"
    else:
        color_col = "avg_cpb"
        color_label = "Avg Cost / Beneficiary"
        hover_fmt = map_df["avg_cpb"].map(lambda v: f"${v:,.0f}" if v == v else "—")
        color_scale = "Purples"

    map_df["hover_value"] = hover_fmt

    fig_map = px.choropleth(
        map_df, locations="practice_state", locationmode="USA-states",
        color=color_col, scope="usa",
        color_continuous_scale=color_scale,
        labels={color_col: color_label},
        custom_data=["hover_value"],
    )
    fig_map.update_traces(
        hovertemplate="%{location}<br>%{customdata[0]}<extra></extra>"
    )
    fig_map.update_layout(
        height=500, margin=dict(l=10, r=10, t=20, b=10),
        font=dict(size=14),
        coloraxis_colorbar=dict(
            title=color_label,
            tickprefix="$" if color_col == "total_paid" else "$",
            tickformat=".2s" if color_col == "total_paid" else ",",
        ),
    )
    st.plotly_chart(fig_map, use_container_width=True)


# ===========================================================================
# SECTION 1 — PROVIDERS
# ===========================================================================
st.header("Providers")

# --- Insight 1: Top providers by spend (table + bar chart) -----------------
st.subheader("Top providers by spend")
st.caption("The highest-paid billing providers for a given year and geography.")

c1, c2 = st.columns(2)
with c1:
    p1_year = st.selectbox("Year", years, index=0, key="p1_year")
with c2:
    p1_scope = st.selectbox("Geography", ["National"] + states, index=0, key="p1_scope")

if p1_scope == "National":
    df = run_query(
        f"""
        SELECT national_rank AS rank, provider_name, billing_npi AS npi,
               practice_state AS state, primary_taxonomy AS taxonomy,
               total_beneficiaries, total_paid
        FROM {SCHEMA}.mart_top_providers_yoy
        WHERE claim_year = %s AND national_rank <= 25
        ORDER BY national_rank
        """,
        (int(p1_year),),
    )
else:
    df = run_query(
        f"""
        SELECT state_rank AS rank, provider_name, billing_npi AS npi,
               primary_taxonomy AS taxonomy, total_beneficiaries, total_paid
        FROM {SCHEMA}.mart_top_providers_yoy
        WHERE claim_year = %s AND practice_state = %s AND state_rank <= 25
        ORDER BY state_rank
        """,
        (int(p1_year), p1_scope),
    )

if df.empty:
    st.info("No providers found for that selection.")
else:
    # Bar chart: top 15 by spend
    # Collapse to one row per provider (an NPI can have >1 taxonomy row in NPPES,
    # which would otherwise plot as two bars on the same line). Sum across taxonomy.
    agg = (
        df.groupby(["provider_name"], as_index=False)
        .agg(total_paid=("total_paid", "sum"), rank=("rank", "min"))
    )
    top15 = agg.nsmallest(15, "rank").sort_values("total_paid")
    bar = px.bar(
        top15, x="total_paid", y="provider_name", orientation="h",
        labels={"total_paid": "Total Paid", "provider_name": ""},
    )
    bar.update_traces(
        marker_color=PALETTES["provider_bar"],
        text=top15["total_paid"].map(fmt_millions),
        textposition="outside", cliponaxis=False,
        textfont=dict(size=13),
        hovertemplate="%{y}<br>%{customdata}<extra></extra>",
        customdata=top15["total_paid"].map(fmt_millions),
    )
    bar.update_layout(
        height=520, margin=dict(l=10, r=120, t=20, b=10),
        plot_bgcolor="rgba(0,0,0,0)", font=dict(size=14),
        xaxis=dict(tickprefix="$", tickformat=".2s", title="Total Paid"),
    )
    st.plotly_chart(bar, use_container_width=True)

    # Ranked table — aggregate to one row per NPI (sums across any duplicate
    # taxonomy rows for the same provider), then format.
    group_keys = ["npi", "provider_name"] + (["state"] if "state" in df.columns else [])
    tbl = (
        df.groupby(group_keys, as_index=False)
        .agg(
            rank=("rank", "min"),
            total_beneficiaries=("total_beneficiaries", "sum"),
            total_paid=("total_paid", "sum"),
        )
        .sort_values("rank")
    )
    tbl["total_paid"] = tbl["total_paid"].map(fmt_millions)
    rename_map = {
        "rank": "Rank",
        "provider_name": "Provider Name",
        "npi": "NPI",
        "state": "State",
        "total_beneficiaries": "Beneficiaries",
        "total_paid": "Total Paid",
    }
    if "state" in tbl.columns:
        order = ["rank", "provider_name", "npi", "state",
                 "total_beneficiaries", "total_paid"]
    else:
        order = ["rank", "provider_name", "npi",
                 "total_beneficiaries", "total_paid"]
    display = tbl[order].rename(columns=rename_map)
    st.dataframe(
        display, use_container_width=True, hide_index=True,
        column_config={
            "Beneficiaries": st.column_config.NumberColumn(
                "Beneficiaries", format="%,d"
            ),
        },
    )

st.divider()

# --- Insight 2: One provider's spend trend YOY -----------------------------
st.subheader("Provider spend trend")
st.caption("Track a single provider's annual spend and year-over-year change. "
           "Search by name or enter an NPI directly.")

c1, c2 = st.columns([2, 1])
with c1:
    name_search = st.text_input(
        "Search provider by name", value="", key="p2_name",
        placeholder="e.g. start typing a provider or organization name",
    )
with c2:
    npi_input = st.text_input("…or enter NPI", value=DEFAULT_NPI, key="p2_npi")

selected_npi = None
if name_search.strip():
    matches = run_query(
        f"""
        SELECT DISTINCT billing_npi, provider_name, practice_state
        FROM {SCHEMA}.mart_top_providers_yoy
        WHERE provider_name ILIKE %s
        ORDER BY provider_name
        LIMIT 50
        """,
        (f"%{name_search.strip()}%",),
    )
    if matches.empty:
        st.info("No providers match that name in the top-provider data.")
    else:
        options = {
            f"{r.provider_name} — {r.practice_state} ({r.billing_npi})": r.billing_npi
            for r in matches.itertuples()
        }
        chosen = st.selectbox("Matching providers", list(options.keys()), key="p2_pick")
        selected_npi = options[chosen]
elif npi_input.strip():
    selected_npi = npi_input.strip()

if selected_npi:
    trend = run_query(
        f"""
        SELECT claim_year, provider_name, practice_state,
               total_paid, total_beneficiaries
        FROM {SCHEMA}.mart_top_providers_yoy
        WHERE billing_npi = %s
        ORDER BY claim_year
        """,
        (selected_npi,),
    )
    if trend.empty:
        st.info(f"NPI {selected_npi} not found in the top-provider data. "
                "Only providers ranking in the top 50 (state or national) per year "
                "are included.")
    else:
        pname = trend["provider_name"].iloc[0]
        pstate = trend["practice_state"].iloc[0]
        st.markdown(f"**{pname}** · {pstate} · NPI {selected_npi}")

        trend = trend.sort_values("claim_year").reset_index(drop=True)
        trend["yoy_pct"] = trend["total_paid"].pct_change() * 100

        # Label only the final data point with its value, shown above the marker.
        text_labels = [""] * len(trend)
        if len(trend) > 0:
            last_val = trend["total_paid"].iloc[-1]
            text_labels[-1] = fmt_millions(last_val)

        fig = go.Figure()
        fig.add_trace(
            go.Scatter(
                x=trend["claim_year"], y=trend["total_paid"],
                mode="lines+markers+text",
                line=dict(color=PALETTES["provider_trend"], width=3, shape="spline"),
                marker=dict(size=10, color=PALETTES["provider_trend"]),
                text=text_labels, textposition="top center",
                textfont=dict(size=18, color=PALETTES["provider_trend"],
                              family="Arial Black"),
                hovertemplate="Year %{x}<br>Paid %{customdata}<extra></extra>",
                customdata=trend["total_paid"].map(fmt_millions),
                name="Total paid",
            )
        )
        fig.update_layout(
            yaxis_title="Total Paid", xaxis_title="",
            yaxis=dict(tickprefix="$", tickformat=".2s"),
            xaxis=dict(dtick=1), height=400,
            margin=dict(l=10, r=10, t=40, b=10),
            plot_bgcolor="rgba(0,0,0,0)", font=dict(size=14),
        )
        st.plotly_chart(fig, use_container_width=True)

        yoy = trend[["claim_year", "total_paid", "yoy_pct"]].copy()
        yoy["total_paid"] = yoy["total_paid"].map(fmt_millions)
        yoy["yoy_pct"] = yoy["yoy_pct"].map(
            lambda v: "—" if v != v else f"{v:+.1f}%"
        )
        yoy = yoy.rename(columns={
            "claim_year": "Year", "total_paid": "Total Paid", "yoy_pct": "YOY Change"
        })
        st.dataframe(yoy, use_container_width=True, hide_index=True)

st.divider()

# --- Insight 2b: Provider rank movement over time (bump chart) --------------
st.subheader("Provider rank movement")
st.caption("How the top providers' spend ranking shifts across years. Lines track the "
           "10 highest-ranked providers in the most recent selected year; a line begins "
           "or ends where a provider enters or leaves the top 50.")

c1, c2 = st.columns(2)
with c1:
    b_scope = st.selectbox("Geography", ["National"] + states, index=0, key="b_scope")
with c2:
    yr_min, yr_max = int(min(years)), int(max(years))
    b_range = st.slider(
        "Year range", min_value=yr_min, max_value=yr_max,
        value=(yr_min, yr_max), key="b_range",
    )

rank_col = "national_rank" if b_scope == "National" else "state_rank"
scope_filter = "" if b_scope == "National" else "AND practice_state = %s"
params = [b_range[0], b_range[1]]
if b_scope != "National":
    params.append(b_scope)

bump = run_query(
    f"""
    SELECT claim_year, billing_npi, provider_name, {rank_col} AS rnk
    FROM {SCHEMA}.mart_top_providers_yoy
    WHERE claim_year BETWEEN %s AND %s
      {scope_filter}
      AND {rank_col} <= 50
    ORDER BY claim_year, rnk
    """,
    tuple(params),
)

if bump.empty:
    st.info("No ranked providers for that selection.")
else:
    # Identify the 10 providers holding the best ranks in the most recent year shown.
    last_yr = bump["claim_year"].max()
    top10_npis = (
        bump[bump["claim_year"] == last_yr]
        .nsmallest(10, "rnk")["billing_npi"]
        .tolist()
    )
    plot_df = bump[bump["billing_npi"].isin(top10_npis)].copy()

    # Short label for legend: provider name truncated + NPI
    def _short(name):
        return (name[:28] + "…") if isinstance(name, str) and len(name) > 28 else name
    plot_df["label"] = plot_df["provider_name"].map(_short)

    fig = px.line(
        plot_df, x="claim_year", y="rnk", color="label",
        markers=True,
        labels={"claim_year": "", "rnk": "Rank", "label": "Provider"},
    )
    fig.update_traces(line=dict(width=2.5), marker=dict(size=8))
    fig.update_layout(
        height=560, xaxis=dict(dtick=1),
        yaxis=dict(autorange="reversed", dtick=5),  # rank 1 at top
        margin=dict(l=10, r=10, t=20, b=10),
        plot_bgcolor="rgba(0,0,0,0)", font=dict(size=14),
        legend=dict(font=dict(size=11)),
    )
    st.plotly_chart(fig, use_container_width=True)


# ===========================================================================
# SECTION 2 — COST TRENDS BY PROCEDURE
# ===========================================================================
st.header("Cost trends by procedure")

# --- Insight 3: Single HCPCS cost-per-beneficiary trend (chart + table) ----
st.subheader("Cost per beneficiary — single procedure code")
st.caption("How the per-beneficiary cost of one HCPCS code has moved over time.")

c1, c2 = st.columns(2)
with c1:
    hcpcs_codes = run_query(
        f"SELECT DISTINCT hcpcs_code FROM {SCHEMA}.mart_cost_per_bene_hcpcs_yoy "
        f"ORDER BY hcpcs_code"
    )["hcpcs_code"].tolist()
    default_hcpcs_idx = hcpcs_codes.index(DEFAULT_HCPCS) if DEFAULT_HCPCS in hcpcs_codes else 0
    h3_code = st.selectbox("HCPCS code", hcpcs_codes, index=default_hcpcs_idx, key="h3_code")
with c2:
    h3_state_label = st.selectbox("Geography", hcpcs_state_options, index=0, key="h3_state")

h3_state = hcpcs_state_value(h3_state_label)

df = run_query(
    f"""
    SELECT claim_year, cost_per_beneficiary, total_beneficiaries,
           total_paid, yoy_pct_change
    FROM {SCHEMA}.mart_cost_per_bene_hcpcs_yoy
    WHERE hcpcs_code = %s AND practice_state = %s
    ORDER BY claim_year
    """,
    (h3_code, h3_state),
)

if df.empty:
    st.info("No data for that code/geography combination.")
else:
    fig = px.area(
        df, x="claim_year", y="cost_per_beneficiary",
        labels={"claim_year": "", "cost_per_beneficiary": "Cost per Beneficiary ($)"},
    )
    fig.update_traces(
        line_color=PALETTES["hcpcs_trend"], fillcolor="rgba(202,106,2,0.12)",
        mode="lines+markers", line_shape="spline",
    )
    fig.update_layout(
        height=400, xaxis=dict(dtick=1),
        yaxis=dict(tickprefix="$", tickformat=","),
        margin=dict(l=10, r=10, t=20, b=10),
        plot_bgcolor="rgba(0,0,0,0)", font=dict(size=14),
    )
    st.plotly_chart(fig, use_container_width=True)

    # Table beneath the chart
    tbl = df.copy()
    tbl["yoy_pct_change"] = tbl["yoy_pct_change"].map(
        lambda v: "—" if v != v or v is None else f"{v*100:+.1f}%"
    )
    tbl["cost_per_beneficiary"] = tbl["cost_per_beneficiary"].map(lambda v: f"${v:,.2f}")
    tbl["total_paid"] = tbl["total_paid"].map(fmt_millions)
    tbl = tbl.rename(columns={
        "claim_year": "Year",
        "cost_per_beneficiary": "Cost / Beneficiary",
        "total_beneficiaries": "Beneficiaries",
        "total_paid": "Total Paid",
        "yoy_pct_change": "YOY Change",
    })
    tbl = tbl[["Year", "Cost / Beneficiary", "YOY Change", "Beneficiaries", "Total Paid"]]
    st.dataframe(
        tbl, use_container_width=True, hide_index=True,
        column_config={
            "Beneficiaries": st.column_config.NumberColumn("Beneficiaries", format="%,d"),
        },
    )

st.divider()

# --- Insight 4: Largest YOY cost-per-beneficiary increases -----------------
st.subheader("Largest year-over-year cost increases")
st.caption("HCPCS codes whose cost per beneficiary jumped the most in the latest year, "
           "filtered to codes with a meaningful beneficiary base.")

c1, c2 = st.columns(2)
with c1:
    i4_state_label = st.selectbox("Geography", hcpcs_state_options, index=0, key="i4_state")
with c2:
    i4_minben = st.select_slider(
        "Minimum beneficiaries", options=[100, 500, 1000, 5000, 10000],
        value=1000, key="i4_minben",
    )

i4_state = hcpcs_state_value(i4_state_label)
latest_year = int(max(years))

df = run_query(
    f"""
    SELECT hcpcs_code, cost_per_beneficiary, prior_year_cpb,
           yoy_pct_change, total_beneficiaries
    FROM {SCHEMA}.mart_cost_per_bene_hcpcs_yoy
    WHERE practice_state = %s AND claim_year = %s
      AND yoy_pct_change IS NOT NULL AND total_beneficiaries >= %s
    ORDER BY yoy_pct_change DESC
    LIMIT 15
    """,
    (i4_state, latest_year, int(i4_minben)),
)

if df.empty:
    st.info("No qualifying codes for that selection.")
else:
    df["yoy_label"] = df["yoy_pct_change"] * 100
    fig = px.bar(
        df.sort_values("yoy_pct_change"),
        x="yoy_label", y="hcpcs_code", orientation="h",
        color="yoy_label", color_continuous_scale=PALETTES["yoy_increase"],
        labels={"yoy_label": "YOY change (%)", "hcpcs_code": "HCPCS"},
    )
    fig.update_layout(
        height=480, coloraxis_showscale=False,
        margin=dict(l=10, r=10, t=20, b=10),
        plot_bgcolor="rgba(0,0,0,0)", font=dict(size=14),
        yaxis=dict(type="category"),
    )
    fig.update_traces(hovertemplate="%{y}<br>+%{x:.1f}%<extra></extra>")
    st.plotly_chart(fig, use_container_width=True)

st.divider()

# --- Chart C: Top 10 HCPCS by geography + year ------------------------------
st.subheader("Top HCPCS codes by total spend")
st.caption("The ten highest-spend procedure codes for the selected geography and year.")

c1, c2 = st.columns(2)
with c1:
    c4_state_label = st.selectbox("Geography", hcpcs_state_options, index=0, key="c4_state")
with c2:
    c4_year = st.selectbox("Year", years, index=0, key="c4_year")

c4_state = hcpcs_state_value(c4_state_label)

df_c4 = run_query(
    f"""
    SELECT hcpcs_code, total_paid, total_claims, total_beneficiaries
    FROM {SCHEMA}.mart_top_hcpcs_by_year
    WHERE practice_state = %s
      AND claim_year = %s
      AND rank_by_spend <= 10
    ORDER BY rank_by_spend
    """,
    (c4_state, int(c4_year)),
)

if df_c4.empty:
    st.info("No data for that selection.")
else:
    df_c4["total_paid"] = df_c4["total_paid"].astype(float)
    fig_c4 = px.bar(
        df_c4.sort_values("total_paid"),
        x="total_paid", y="hcpcs_code", orientation="h",
        labels={"total_paid": "Total Paid", "hcpcs_code": "HCPCS"},
    )
    fig_c4.update_traces(
        marker_color="#5a189a",  # purple — new color for visual distinction
        text=df_c4.sort_values("total_paid")["total_paid"].map(fmt_millions),
        textposition="outside", cliponaxis=False,
        textfont=dict(size=13),
        hovertemplate="%{y}<br>%{text}<extra></extra>",
    )
    fig_c4.update_layout(
        height=460, margin=dict(l=10, r=120, t=20, b=10),
        plot_bgcolor="rgba(0,0,0,0)", font=dict(size=14),
        xaxis=dict(tickprefix="$", tickformat=".2s"),
        yaxis=dict(type="category"),
    )
    st.plotly_chart(fig_c4, use_container_width=True)

st.divider()

# --- Chart D: Top cost per beneficiary, with range filter -------------------
st.subheader("Top HCPCS codes by cost per beneficiary")
st.caption("Procedure codes ordered by per-beneficiary cost, filtered to a selected range. "
           "Use this to find codes within a specific cost band rather than the overall most "
           "expensive.")

c1, c2 = st.columns(2)
with c1:
    d5_state_label = st.selectbox("Geography", hcpcs_state_options, index=0, key="d5_state")
with c2:
    d5_year = st.selectbox("Year", years, index=0, key="d5_year")

d5_state = hcpcs_state_value(d5_state_label)

# Pull the candidate set for the selected geography + year so we can show
# reference averages and bound the slider sensibly.
d5_all = run_query(
    f"""
    SELECT hcpcs_code, cost_per_beneficiary, total_beneficiaries, total_paid
    FROM {SCHEMA}.mart_cost_per_bene_hcpcs_yoy
    WHERE practice_state = %s
      AND claim_year = %s
      AND cost_per_beneficiary IS NOT NULL
      AND cost_per_beneficiary > 0
    """,
    (d5_state, int(d5_year)),
)

if d5_all.empty:
    st.info("No data for that geography/year.")
else:
    d5_all["cost_per_beneficiary"] = d5_all["cost_per_beneficiary"].astype(float)
    d5_all["total_paid"] = d5_all["total_paid"].astype(float)

    # Compute the weighted average cost per beneficiary for the selected geography/year.
    # SUM(total_paid)/SUM(total_beneficiaries) is the right aggregation, not a mean
    # of the rates.
    avg_cpb = (
        d5_all["total_paid"].sum()
        / max(1, d5_all["total_beneficiaries"].sum())
    )
    st.caption(
        f"Average cost per beneficiary in this geography/year: **${avg_cpb:,.0f}** "
        f"(across {len(d5_all):,} HCPCS codes). "
        "Slider bounded at the 1st–99th percentile to avoid extreme outliers."
    )

    # Percentile-bounded slider so outliers don't compress the useful range.
    lower = float(d5_all["cost_per_beneficiary"].quantile(0.01))
    upper = float(d5_all["cost_per_beneficiary"].quantile(0.99))
    # Round bounds to friendly numbers
    lower_r = max(0.0, round(lower, 2))
    upper_r = round(upper, 2)
    if upper_r <= lower_r:
        upper_r = lower_r + 1.0  # safety for degenerate cases

    d5_range = st.slider(
        "Cost-per-beneficiary range ($)",
        min_value=lower_r, max_value=upper_r,
        value=(lower_r, upper_r), step=max(1.0, (upper_r - lower_r) / 200),
        key="d5_range",
    )

    filtered = d5_all[
        (d5_all["cost_per_beneficiary"] >= d5_range[0])
        & (d5_all["cost_per_beneficiary"] <= d5_range[1])
    ].copy()

    if filtered.empty:
        st.info("No HCPCS codes fall within that range.")
    else:
        top = filtered.nlargest(15, "cost_per_beneficiary").sort_values(
            "cost_per_beneficiary"
        )
        fig_d5 = px.bar(
            top, x="cost_per_beneficiary", y="hcpcs_code", orientation="h",
            labels={
                "cost_per_beneficiary": "Cost per Beneficiary ($)",
                "hcpcs_code": "HCPCS",
            },
        )
        fig_d5.update_traces(
            marker_color="#0a9396",  # teal
            text=top["cost_per_beneficiary"].map(lambda v: f"${v:,.0f}"),
            textposition="outside", cliponaxis=False,
            textfont=dict(size=13),
            hovertemplate="%{y}<br>$%{x:,.0f} per beneficiary<extra></extra>",
        )
        fig_d5.update_layout(
            height=520, margin=dict(l=10, r=120, t=20, b=10),
            plot_bgcolor="rgba(0,0,0,0)", font=dict(size=14),
            xaxis=dict(tickprefix="$", tickformat=","),
            yaxis=dict(type="category"),
        )
        st.plotly_chart(fig_d5, use_container_width=True)
        st.caption(
            f"Showing top 15 of {len(filtered):,} codes in the selected range."
        )


# ===========================================================================
# SECTION 3 — TREND ANALYTICS
# ===========================================================================
st.header("Trend analytics")

st.subheader("Cumulative change vs. annualized growth (CAGR)")
st.caption("Each point is one HCPCS code. Cumulative % captures total movement; CAGR "
           "annualizes it. Codes far apart on the two measures grew unevenly — most of "
           "their change happened in a single year. Sized by years of data observed.")

# Default this dropdown to GA.
_s6_options = hcpcs_state_options
_s6_default = _s6_options.index("GA") if "GA" in _s6_options else 0
s6_state_label = st.selectbox("Geography", _s6_options, index=_s6_default, key="s6_state")
s6_state = hcpcs_state_value(s6_state_label)

df = run_query(
    f"""
    SELECT hcpcs_code, cumulative_pct_change, cagr_to_date,
           years_observed, total_beneficiaries
    FROM {SCHEMA}.mart_cost_per_bene_hcpcs_yoy
    WHERE practice_state = %s AND claim_year = %s
      AND first_year <= 2019 AND years_observed >= 5
      AND cumulative_pct_change IS NOT NULL AND cagr_to_date IS NOT NULL
      AND total_beneficiaries >= 5000
    """,
    (s6_state, latest_year),
)

if df.empty:
    st.info("No qualifying codes for that selection. Try lowering the geography to "
            "National, which has the most data.")
else:
    df["cum_pct"] = df["cumulative_pct_change"] * 100
    df["cagr_pct"] = df["cagr_to_date"] * 100
    fig = px.scatter(
        df, x="cagr_pct", y="cum_pct",
        size="total_beneficiaries", color="years_observed",
        hover_name="hcpcs_code", color_continuous_scale=PALETTES["scatter"],
        labels={
            "cagr_pct": "CAGR (%/yr)", "cum_pct": "Cumulative change (%)",
            "years_observed": "Years observed", "total_beneficiaries": "Beneficiaries",
        },
        size_max=40,
    )
    fig.update_traces(marker=dict(line=dict(width=1, color="#444")))
    fig.update_layout(
        height=560, margin=dict(l=10, r=10, t=20, b=10),
        plot_bgcolor="rgba(0,0,0,0)", font=dict(size=14),
        xaxis=dict(
            title_font=dict(size=18), tickfont=dict(size=15),
            dtick=20, gridcolor="#e2e8f0", gridwidth=1,
        ),
        yaxis=dict(
            title_font=dict(size=18), tickfont=dict(size=15),
            dtick=200, gridcolor="#e2e8f0", gridwidth=1,
        ),
    )
    st.plotly_chart(fig, use_container_width=True)
    st.caption(f"Showing {len(df)} codes meeting the maturity and volume filters.")

# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------
st.divider()
st.markdown(
    "Built with dbt Core + PostgreSQL (Neon) + Streamlit. "
    "Source: [HHS Medicaid Provider Spending](https://opendata.hhs.gov/datasets/medicaid-provider-spending) · "
    "Provider attribution via [NPPES](https://download.cms.gov/nppes/NPI_Files.html)."
)
