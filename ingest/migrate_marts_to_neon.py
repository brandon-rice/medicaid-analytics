import os
import psycopg2
from io import StringIO
from dotenv import load_dotenv

load_dotenv()

# Source: local
SRC = dict(
    host="localhost", port=5432, dbname="brdb",
    user=os.getenv("PG_USER"), password=os.getenv("PG_PASSWORD"),
)

# Target: Neon (set these in .env)
DST = dict(
    host=os.getenv("NEON_HOST"),
    port=5432,
    dbname=os.getenv("NEON_DB"),
    user=os.getenv("NEON_USER"),
    password=os.getenv("NEON_PASSWORD"),
    sslmode="require",  # Neon requires SSL
)

MARTS = [
    "mart_spend_by_state_month",
    "mart_top_providers_yoy",
    "mart_top_hcpcs_by_year",
    "mart_cost_per_bene_hcpcs_yoy",
]

SCHEMA = "medicaid_analytics"
SOURCE_SCHEMA = "medicaid_marts"

def get_table_ddl(src_conn, source_schema, dest_schema, table):
    """Generate CREATE TABLE DDL by introspecting columns from source,
    but with destination schema in the output."""
    sql = """
        SELECT 
            column_name, 
            data_type, 
            character_maximum_length,
            numeric_precision,
            numeric_scale,
            is_nullable
        FROM information_schema.columns
        WHERE table_schema = %s AND table_name = %s
        ORDER BY ordinal_position;
    """
    with src_conn.cursor() as cur:
        cur.execute(sql, (source_schema, table))
        rows = cur.fetchall()

    if not rows:
        raise ValueError(
            f"No columns found for {source_schema}.{table} on source — "
            f"does this table exist?"
        )

    cols = []
    for name, dtype, char_len, num_prec, num_scale, nullable in rows:
        if dtype == 'character varying' and char_len:
            type_str = f"varchar({char_len})"
        elif dtype == 'numeric' and num_prec:
            type_str = f"numeric({num_prec},{num_scale or 0})"
        elif dtype == 'double precision':
            type_str = 'double precision'
        elif dtype == 'timestamp without time zone':
            type_str = 'timestamp'
        else:
            type_str = dtype
        null_str = "" if nullable == "YES" else " NOT NULL"
        cols.append(f"    {name} {type_str}{null_str}")

    cols_sql = ",\n".join(cols)
    return f"CREATE TABLE {dest_schema}.{table} (\n{cols_sql}\n);"


def migrate_table(src_conn, dst_conn, source_schema, dest_schema, table):
    print(f"\n=== {source_schema}.{table} -> {dest_schema}.{table} ===")

    ddl = get_table_ddl(src_conn, source_schema, dest_schema, table)

    with dst_conn.cursor() as cur:
        cur.execute(f"CREATE SCHEMA IF NOT EXISTS {dest_schema};")
        cur.execute(f"DROP TABLE IF EXISTS {dest_schema}.{table};")
        cur.execute(ddl)
    dst_conn.commit()
    print(f"  Created table on Neon.")

    buf = StringIO()
    with src_conn.cursor() as src_cur:
        src_cur.copy_expert(
            f"COPY {source_schema}.{table} TO STDOUT WITH (FORMAT text)",
            buf
        )

    buf.seek(0)
    with dst_conn.cursor() as dst_cur:
        dst_cur.copy_expert(
            f"COPY {dest_schema}.{table} FROM STDIN WITH (FORMAT text)",
            buf
        )
    dst_conn.commit()

    with dst_conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM {dest_schema}.{table};")
        row_count = cur.fetchone()[0]
    print(f"  Loaded {row_count:,} rows.")


def main():
    print("Connecting to local Postgres...")
    src = psycopg2.connect(**SRC)
    print("Connecting to Neon...")
    dst = psycopg2.connect(**DST)

    for table in MARTS:
        migrate_table(src, dst, SOURCE_SCHEMA, SCHEMA, table)

    # Add helpful indexes on Neon for dashboard queries
    print("\nBuilding dashboard indexes on Neon...")
    with dst.cursor() as cur:
        cur.execute(f"""
        CREATE INDEX IF NOT EXISTS idx_spend_state_month 
            ON {SCHEMA}.mart_spend_by_state_month (practice_state, claim_month);
        
        CREATE INDEX IF NOT EXISTS idx_providers_state_year 
            ON {SCHEMA}.mart_top_providers_yoy (practice_state, claim_year);
        
        CREATE INDEX IF NOT EXISTS idx_hcpcs_state_year 
            ON {SCHEMA}.mart_top_hcpcs_by_year (practice_state, claim_year);
        
        CREATE INDEX IF NOT EXISTS idx_cpb_state_year 
            ON {SCHEMA}.mart_cost_per_bene_hcpcs_yoy (practice_state, claim_year);
        
        ANALYZE {SCHEMA}.mart_spend_by_state_month;
        ANALYZE {SCHEMA}.mart_top_providers_yoy;
        ANALYZE {SCHEMA}.mart_top_hcpcs_by_year;
        ANALYZE {SCHEMA}.mart_cost_per_bene_hcpcs_yoy;
        """)
    dst.commit()
    print("Indexes built.")

    src.close()
    dst.close()
    print("\nDone.")


if __name__ == "__main__":
    main()