"""
Database connection and cached query helpers for the Medicaid analytics dashboard.

Connects to the Neon Postgres `medicaid_analytics` schema. Reads credentials from
Streamlit secrets when deployed (Community Cloud), and falls back to a local .env
file for local development.
"""
import os
import streamlit as st
import psycopg2
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

SCHEMA = "medicaid_analytics"


def _get_credentials() -> dict:
    """Pull Neon credentials from Streamlit secrets first, then .env."""
    # Streamlit Community Cloud: credentials live in st.secrets
    try:
        if "neon" in st.secrets:
            s = st.secrets["neon"]
            return dict(
                host=s["host"],
                dbname=s["dbname"],
                user=s["user"],
                password=s["password"],
                port=s.get("port", 5432),
                sslmode="require",
            )
    except Exception:
        # st.secrets raises if no secrets file exists locally; ignore and use .env
        pass

    # Local development: read from .env
    return dict(
        host=os.getenv("NEON_HOST"),
        dbname=os.getenv("NEON_DB"),
        user=os.getenv("NEON_USER"),
        password=os.getenv("NEON_PASSWORD"),
        port=int(os.getenv("NEON_PORT", 5432)),
        sslmode="require",
    )


@st.cache_resource
def get_connection():
    """Single cached Postgres connection, reused across reruns."""
    creds = _get_credentials()
    return psycopg2.connect(**creds)


@st.cache_data(ttl=3600, show_spinner=False)
def run_query(sql: str, params: tuple | None = None) -> pd.DataFrame:
    """Run a query and return a DataFrame. Results cached for one hour."""
    conn = get_connection()
    try:
        return pd.read_sql(sql, conn, params=params)
    except Exception:
        # If the cached connection went stale (Neon scaled to zero / dropped),
        # clear it and retry once with a fresh connection.
        get_connection.clear()
        conn = get_connection()
        return pd.read_sql(sql, conn, params=params)
