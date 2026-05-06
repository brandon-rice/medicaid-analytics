import os
import pandas as pd
import psycopg2
from io import StringIO
from dotenv import load_dotenv

load_dotenv()

# Update this path to wherever you unzipped the NPPES file
NPPES_PATH = os.getenv("NPPES_FILE_PATH")
TABLE = "medicaid_raw.nppes_raw"

# NPPES has 330+ columns; we only need a handful.
# Column names come from the NPPES Data Dissemination File header.
COLS_TO_KEEP = {
    "NPI":                                              "npi",
    "Entity Type Code":                                 "entity_type_code",
    "Provider Organization Name (Legal Business Name)": "organization_name",
    "Provider Last Name (Legal Name)":                  "last_name",
    "Provider First Name":                              "first_name",
    "Provider First Line Business Practice Location Address": "practice_address_1",
    "Provider Business Practice Location Address City Name":  "practice_city",
    "Provider Business Practice Location Address State Name": "practice_state",
    "Provider Business Practice Location Address Postal Code": "practice_zip",
    "Healthcare Provider Taxonomy Code_1":              "primary_taxonomy",
    "Provider Enumeration Date":                        "enumeration_date",
    "NPI Deactivation Date":                            "deactivation_date",
}

DDL = """
CREATE SCHEMA IF NOT EXISTS medicaid_raw;
DROP TABLE IF EXISTS medicaid_raw.nppes_raw;
CREATE TABLE medicaid_raw.nppes_raw (
    npi                text PRIMARY KEY,
    entity_type_code   text,
    organization_name  text,
    last_name          text,
    first_name         text,
    practice_address_1 text,
    practice_city      text,
    practice_state     text,
    practice_zip       text,
    primary_taxonomy   text,
    enumeration_date   text,
    deactivation_date  text
);
"""

def main():
    # Step 1: validate the column names BEFORE doing anything expensive
    print("Validating column names against the source file...")
    actual_headers = pd.read_csv(NPPES_PATH, nrows=0).columns.tolist()
    missing = [c for c in COLS_TO_KEEP.keys() if c not in actual_headers]
    if missing:
        print(f"\n  ERROR: These expected columns were not found in the file:")
        for m in missing:
            print(f"    {m!r}")
        print(f"\n  Columns in source file that contain similar text:")
        for m in missing:
            keyword = m.split()[0].lower()
            matches = [h for h in actual_headers if keyword in h.lower()][:3]
            print(f"    For {m!r}: {matches}")
        sys.exit(1)
    print(f"  All {len(COLS_TO_KEEP)} expected columns found.")

    # Step 2: connect and load
    conn = psycopg2.connect(
        host="localhost", port=5432, dbname="brdb",
        user=os.getenv("PG_USER"), password=os.getenv("PG_PASSWORD"),
    )
    cur = conn.cursor()
    cur.execute(DDL)
    conn.commit()

    chunk_size = 250_000
    total_rows = 0
    skipped_deactivated = 0

    reader = pd.read_csv(
        NPPES_PATH,
        usecols=list(COLS_TO_KEEP.keys()),
        dtype=str,
        chunksize=chunk_size,
        encoding="utf-8",
        on_bad_lines="warn",
    )

    for i, df in enumerate(reader):
        df = df.rename(columns=COLS_TO_KEEP)

        # Reorder columns to match table DDL exactly — defends against
        # pandas reading them in source-file order
        df = df[list(COLS_TO_KEEP.values())]

        before = len(df)
        df = df[df["deactivation_date"].isna()]
        skipped_deactivated += (before - len(df))

        df = df.dropna(subset=["npi"])
        df["practice_state"] = df["practice_state"].str.upper().str[:2]

        buf = StringIO()
        df.to_csv(buf, index=False, header=False, sep="\t", na_rep="\\N")
        buf.seek(0)

        cur.copy_expert(
            f"COPY {TABLE} FROM STDIN WITH (FORMAT text, DELIMITER E'\\t', NULL '\\N')",
            buf
        )
        total_rows += len(df)
        print(f"Chunk {i+1}: {total_rows:,} active rows loaded")

    conn.commit()

    print("Building index on practice_state...")
    cur.execute("CREATE INDEX idx_nppes_state ON medicaid_raw.nppes_raw (practice_state);")
    cur.execute("ANALYZE medicaid_raw.nppes_raw;")
    conn.commit()

    cur.close()
    conn.close()

    print(f"\nDone.")
    print(f"  Active providers loaded: {total_rows:,}")
    print(f"  Deactivated providers skipped: {skipped_deactivated:,}")

if __name__ == "__main__":
    main()