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
    "scatter": "Viridis",               # plotly sequential
}

DEFAULT_NPI = "1689744450"
DEFAULT_HCPCS = "T1019"

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
    top15 = df.nsmallest(15, "rank").sort_values("total_paid")
    bar = px.bar(
        top15, x="total_paid", y="provider_name", orientation="h",
        labels={"total_paid": "Total Paid ($)", "provider_name": ""},
    )
    bar.update_traces(marker_color=PALETTES["provider_bar"],
                      hovertemplate="%{y}<br>$%{x:,.0f}<extra></extra>")
    bar.update_layout(
        height=520, margin=dict(l=10, r=10, t=20, b=10),
        plot_bgcolor="rgba(0,0,0,0)", font=dict(size=14),
    )
    st.plotly_chart(bar, use_container_width=True)

    # Ranked table: Provider Name, NPI, [State], Taxonomy, Beneficiaries, Total Paid
    display = df.copy()
    # Keep numeric beneficiaries so the column stays sortable; format paid as the
    # last column. (Total Paid is a string for $ formatting; beneficiaries stays int.)
    display["total_paid"] = display["total_paid"].map(lambda v: f"${v:,.0f}")
    rename_map = {
        "rank": "Rank",
        "provider_name": "Provider Name",
        "npi": "NPI",
        "state": "State",
        "taxonomy": "Taxonomy",
        "total_beneficiaries": "Beneficiaries",
        "total_paid": "Total Paid",
    }
    # Column order: Rank, Provider Name, NPI, (State), Taxonomy, Beneficiaries, Total Paid
    if "state" in display.columns:
        order = ["rank", "provider_name", "npi", "state", "taxonomy",
                 "total_beneficiaries", "total_paid"]
    else:
        order = ["rank", "provider_name", "npi", "taxonomy",
                 "total_beneficiaries", "total_paid"]
    display = display[order].rename(columns=rename_map)
    st.dataframe(
        display, use_container_width=True, hide_index=True,
        column_config={
            "Beneficiaries": st.column_config.NumberColumn(
                "Beneficiaries", format="%d"
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
            text_labels[-1] = f"${last_val:,.0f}"

        fig = go.Figure()
        fig.add_trace(
            go.Scatter(
                x=trend["claim_year"], y=trend["total_paid"],
                mode="lines+markers+text",
                line=dict(color=PALETTES["provider_trend"], width=3, shape="spline"),
                marker=dict(size=10, color=PALETTES["provider_trend"]),
                text=text_labels, textposition="top center",
                textfont=dict(size=14, color=PALETTES["provider_trend"]),
                hovertemplate="Year %{x}<br>Paid $%{y:,.0f}<extra></extra>",
                name="Total paid",
            )
        )
        fig.update_layout(
            yaxis_title="Total Paid ($)", xaxis_title="",
            xaxis=dict(dtick=1), height=400,
            margin=dict(l=10, r=10, t=40, b=10),
            plot_bgcolor="rgba(0,0,0,0)", font=dict(size=14),
        )
        st.plotly_chart(fig, use_container_width=True)

        yoy = trend[["claim_year", "total_paid", "yoy_pct"]].copy()
        yoy["total_paid"] = yoy["total_paid"].map(lambda v: f"${v:,.0f}")
        yoy["yoy_pct"] = yoy["yoy_pct"].map(
            lambda v: "—" if v != v else f"{v:+.1f}%"
        )
        yoy = yoy.rename(columns={
            "claim_year": "Year", "total_paid": "Total Paid", "yoy_pct": "YOY Change"
        })
        st.dataframe(yoy, use_container_width=True, hide_index=True)


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
    tbl["total_paid"] = tbl["total_paid"].map(lambda v: f"${v:,.0f}")
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
            "Beneficiaries": st.column_config.NumberColumn("Beneficiaries", format="%d"),
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
    )
    fig.update_traces(hovertemplate="%{y}<br>+%{x:.1f}%<extra></extra>")
    st.plotly_chart(fig, use_container_width=True)


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
    fig.update_layout(
        height=560, margin=dict(l=10, r=10, t=20, b=10),
        plot_bgcolor="rgba(0,0,0,0)", font=dict(size=14),
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
