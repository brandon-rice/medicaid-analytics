import os
import pyarrow.parquet as pq
import psycopg2
from io import StringIO
from dotenv import load_dotenv

load_dotenv()

PARQUET_FILE_PATH = os.getenv("PARQUET_FILE_PATH")
TABLE = "medicaid_raw.medicaid_claims_raw"

DDL = """
DROP TABLE IF EXISTS medicaid_raw.medicaid_claims_raw;
CREATE TABLE medicaid_raw.medicaid_claims_raw (
    billing_provider_npi   text,
    servicing_provider_npi text,
    hcpcs_code             text,
    claim_from_month       text,
    unique_beneficiaries   bigint,
    total_claims           bigint,
    total_paid             numeric(18,2)
);
"""


def main():
    conn = psycopg2.connect(
        host="localhost",
        port=5432,
        dbname="brdb",
        user=os.getenv("PG_USER"),
        password=os.getenv("PG_PASSWORD"),
    )
    cur = conn.cursor()
    cur.execute(DDL)
    conn.commit()

    pf = pq.ParquetFile(PARQUET_FILE_PATH)
    total_rows = 0

    for i, batch in enumerate(pf.iter_batches(batch_size=500_000)):
        df = batch.to_pandas()
        buf = StringIO()
        df.to_csv(buf, index=False, header=False, sep="\t", na_rep="\\N")
        buf.seek(0)

        cur.copy_expert(
            f"COPY {TABLE} FROM STDIN WITH (FORMAT text, DELIMITER E'\\t', NULL '\\N')",
            buf
        )
        total_rows += len(df)
        print(f"Batch {i+1}: {total_rows:,} rows loaded")

    conn.commit()
    cur.close()
    conn.close()
    print(f"Done. {total_rows:,} total rows.")

if __name__ == "__main__":
    main()