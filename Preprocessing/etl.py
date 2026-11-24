#!/usr/bin/env python3
"""
MOVER preprocessing pipeline on PostgreSQL.

  python etl.py init                                         schemas, helper functions, typed raw tables
  python etl.py load-csv --file data/patient_information.csv  MOVER delivery (CSV) -> raw, with type gate
  python etl.py load-ehr --table patient_history --query-file queries/patient_history.sql
                                                             EHR database -> raw, no CSV in between
  python etl.py transform                                    raw -> clean -> mart (sql/02..04)
  python etl.py checks                                       quality tests (exit code 1 on FAIL)

Connection settings come from the environment / .env (PGHOST, PGPORT, PGDATABASE, PGUSER, PGPASSWORD).
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import os
import sys
from pathlib import Path

#Postgre SQL adapter 
import psycopg
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
SQL_DIR = ROOT / "sql"
load_dotenv(ROOT / ".env")

# raw column type -> safe-cast function used by the load gate
CAST_FUNC = {
    "numeric": "etl.try_numeric",
    "integer": "etl.try_integer",
    "date": "etl.try_date",
    "timestamp without time zone": "etl.try_timestamp",
}

# --------------------------------------------------------------------------- helpers

# helper function to connect to the database using psycopg (the adapter for PostgreSQL)
def connect() -> psycopg.Connection:
    return psycopg.connect(host="localhost",
    port=5432,
    dbname="PLACEHOLDEr",
    user="PLACEHOLDER",
    password="PLACEHOLDER")  # libpq reads PGHOST, PGPORT, PGDATABASE, PGUSER, PGPASSWORD, PGSSLMODE

# helper function to run the sql files
def run_sql_file(conn: psycopg.Connection, name: str) -> None:
    conn.execute((SQL_DIR / name).read_text(encoding="utf-8"))
    conn.commit()
    print(f"  ran {name}")

# helper function to get the raw table columns and their types from the database
def raw_columns(conn: psycopg.Connection, table: str) -> list[tuple[str, str]]:
    rows = conn.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_schema = 'raw' AND table_name = %s ORDER BY ordinal_position",
        (table,),
    ).fetchall()
    cols = [(n, t) for n, t in rows if not n.startswith("_")]  # skip lineage columns
    if not cols:
        sys.exit(f"Unknown raw table '{table}'. Run 'python etl.py init' first.")
    return cols

# helper function to compute the SHA256 hash of a file, used to check if a CSV file has already been loaded
def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


# --------------------------------------------------------------------------- init
# create the schemas, helper functions and raw tables
def cmd_init(args) -> None:
    with connect() as conn:
        print("Creating schemas, helper functions and raw tables")
        run_sql_file(conn, "00_schemas.sql")
        run_sql_file(conn, "01_raw_tables.sql")


# --------------------------------------------------------------------------- CSV -> raw
# load a CSV file into a raw table, with type checking and lineage logging
def cmd_load_csv(args) -> None:
    path = Path(args.file)
    table = args.table or path.stem.lower()
    if not path.exists():
        sys.exit(f"File not found: {path}")

    with connect() as conn:
        cols = raw_columns(conn, table)
        names = [n for n, _ in cols]

        # 1) header must match the raw table (order may differ)
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            header = [h.strip().lower() for h in next(csv.reader(f))]
        if set(header) != set(names) or len(header) != len(names):
            sys.exit(
                f"Header does not match raw.{table}.\n"
                f"  missing in file: {sorted(set(names) - set(header))}\n"
                f"  unexpected in file: {sorted(set(header) - set(names))}"
            )

        # 2) skip a file that was already loaded (same content), unless --force
        sha = sha256_of(path)
        if not args.force:
            done = conn.execute(
                "SELECT load_id FROM etl.load_log WHERE table_name = %s AND file_sha256 = %s AND status = 'ok'",
                (table, sha),
            ).fetchone()
            if done:
                print(f"{path.name}: already loaded as load_id {done[0]} (use --force to load again)")
                return

        load_id = conn.execute(
            "INSERT INTO etl.load_log (table_name, source_file, file_sha256) VALUES (%s, %s, %s) RETURNING load_id",
            (table, path.name, sha),
        ).fetchone()[0]

        # 3) COPY into a text staging table (never fails on content, only on structure)
        stage_cols = ", ".join(f'"{h}" text' for h in header)
        conn.execute(f"CREATE TEMP TABLE _stage ({stage_cols}) ON COMMIT DROP")
        copy_cols = ", ".join(f'"{h}"' for h in header)
        with conn.cursor() as cur:
            with cur.copy(
                f"COPY _stage ({copy_cols}) FROM STDIN WITH (FORMAT csv, HEADER true, ENCODING 'UTF8')"
            ) as copy:
                with open(path, "rb") as f:
                    while chunk := f.read(1 << 20):
                        copy.write(chunk)
        n_rows = conn.execute("SELECT count(*) FROM _stage").fetchone()[0]

        # 4) type gate: every value must be castable to the type of its raw column
        problems = []
        for name, dtype in cols:
            fn = CAST_FUNC.get(dtype)
            if fn is None:
                continue
            n_bad, examples = conn.execute(
                f'SELECT count(*), (array_agg("{name}"))[1:5] FROM _stage '
                f"WHERE \"{name}\" IS NOT NULL AND btrim(\"{name}\") <> '' AND {fn}(\"{name}\") IS NULL"
            ).fetchone()
            if n_bad:
                problems.append((name, dtype, n_bad, examples))
        if problems:
            conn.rollback()
            print(f"LOAD REJECTED for {path.name}: values that do not fit the column type")
            for name, dtype, n_bad, examples in problems:
                print(f"  {name} ({dtype}): {n_bad} rows, e.g. {examples}")
            print("Fix the raw table type, the DateStyle, or ask the data provider. Nothing was loaded.")
            sys.exit(1)

        # 5) typed insert + lineage columns
        select_list = ", ".join(
            f'"{n}"' if t == "text" else f"NULLIF(btrim(\"{n}\"), '')::{t}" for n, t in cols
        )
        conn.execute(
            f"INSERT INTO raw.{table} ({', '.join(names)}, _load_id, _source_file) "
            f"SELECT {select_list}, %s, %s FROM _stage",
            (load_id, path.name),
        )
        conn.execute("UPDATE etl.load_log SET rows_loaded = %s WHERE load_id = %s", (n_rows, load_id))
        conn.commit()
        print(f"{path.name}: {n_rows:,} rows loaded into raw.{table} (load_id {load_id})")


# --------------------------------------------------------------------------- EHR -> raw (no CSV)
# Stream the result of a SQL query on the EHR database straight into the raw table. Therefore no types are lost due to CSV conversion 
def cmd_load_ehr(args) -> None:
    from sqlalchemy import create_engine

    url = os.environ.get("EHR_DATABASE_URL")
    if not url:
        sys.exit("Set EHR_DATABASE_URL in .env (read-only account on the EHR database).")
    query_path = Path(args.query_file)
    query = query_path.read_text(encoding="utf-8")
    table = args.table

    with connect() as conn:
        names = [n for n, _ in raw_columns(conn, table)]
        load_id = conn.execute(
            "INSERT INTO etl.load_log (table_name, source_file, file_sha256) VALUES (%s, %s, %s) RETURNING load_id",
            (table, f"ehr:{query_path.name}", hashlib.sha256(query.encode()).hexdigest()),
        ).fetchone()[0]

        src = create_engine(url)
        n_rows = 0
        with src.connect() as sc:
            result = sc.execution_options(stream_results=True).exec_driver_sql(query)
            keys = [k.lower() for k in result.keys()]
            if set(keys) != set(names) or len(keys) != len(names):
                conn.rollback()
                sys.exit(
                    f"Query columns do not match raw.{table}.\n"
                    f"  missing: {sorted(set(names) - set(keys))}  unexpected: {sorted(set(keys) - set(names))}"
                )
            order = [keys.index(n) for n in names]
            with conn.cursor() as cur:
                with cur.copy(
                    f"COPY raw.{table} ({', '.join(names)}, _load_id, _source_file) FROM STDIN"
                ) as copy:
                    for row in result:
                        copy.write_row([row[i] for i in order] + [load_id, f"ehr:{query_path.name}"])
                        n_rows += 1
        conn.execute("UPDATE etl.load_log SET rows_loaded = %s WHERE load_id = %s", (n_rows, load_id))
        conn.commit()
        print(f"EHR query {query_path.name}: {n_rows:,} rows loaded into raw.{table} (load_id {load_id})")


# --------------------------------------------------------------------------- transform
#The ETL rules are run as defined in the sql files. First, the surgery table and history table are cleaned and then put into the target layers 
def cmd_transform(args) -> None:
    with connect() as conn:
        for t in ("patient_information", "patient_history"):
            n = conn.execute(f"SELECT count(*) FROM raw.{t}").fetchone()[0]
            if n == 0:
                sys.exit(f"raw.{t} is empty. Load the data first.")
        conn.execute("TRUNCATE etl.rule_log")
        conn.commit()
        print("Running ETL rules")
        for f in ("02_clean_surgery.sql", "03_clean_history.sql", "04_mart.sql"):
            run_sql_file(conn, f)
        print("\nRule log (rows affected per rule):")
        for rule_id, desc, n in conn.execute(
            "SELECT rule_id, description, rows_affected FROM etl.rule_log ORDER BY logged_at, rule_id"
        ):
            print(f"  {rule_id:<5} {n:>10,}  {desc}")


# --------------------------------------------------------------------------- quality tests
#define forbidden columns to avoid data leakage 
FORBIDDEN_IN_MART = (
    "disch_disp", "disch_disp_c", "los", "hosp_disch_time", "out_or_dttm",
    "an_stop_datetime", "in_or_dttm", "an_start_datetime", "icu_admin_flag", "icu_admission",
)

# (id, description, sql returning one number, predicate on that number, severity if the predicate fails)
CHECKS = [
    ("Q01", "raw tables are not empty (rows, smaller table)",
     "SELECT least((SELECT count(*) FROM raw.patient_information), (SELECT count(*) FROM raw.patient_history))",
     lambda v: v > 0, "FAIL"),
    ("Q02", "clean.surgery: duplicate log_id (must be 0)",
     "SELECT count(*) - count(DISTINCT log_id) FROM clean.surgery",
     lambda v: v == 0, "FAIL"),
    ("Q03", "reconciliation raw -> clean: distinct log_ids - kept - excluded (must be 0)",
     "SELECT (SELECT count(DISTINCT etl.clean_text(log_id)) FROM raw.patient_information "
     "        WHERE _load_id = (SELECT max(_load_id) FROM raw.patient_information)) "
     "- (SELECT count(*) FROM clean.surgery) "
     "- (SELECT rows_affected FROM etl.rule_log WHERE rule_id = 'S03')",
     lambda v: v == 0, "FAIL"),
    ("Q04", "values outside plausible ranges in clean.surgery (must be 0)",
     "SELECT count(*) FROM clean.surgery WHERE height_cm NOT BETWEEN 120 AND 230 "
     "OR weight_kg NOT BETWEEN 20 AND 350 OR bmi NOT BETWEEN 10 AND 80 OR age_years NOT BETWEEN 0 AND 120",
     lambda v: v == 0, "FAIL"),
    ("Q05", "mart.model_dataset: one row per surgery, no join fan-out (extra rows, must be 0)",
     "SELECT count(*) - count(DISTINCT log_id) FROM mart.model_dataset",
     lambda v: v == 0, "FAIL"),
    ("Q06", "mart rows - labelled clean rows (must be 0)",
     "SELECT (SELECT count(*) FROM mart.model_dataset) "
     "- (SELECT count(*) FROM clean.surgery WHERE icu_admission IS NOT NULL)",
     lambda v: v == 0, "FAIL"),
    ("Q07", "no post-surgery column in mart.model_dataset (must be 0)",
     "SELECT count(*) FROM information_schema.columns WHERE table_schema = 'mart' "
     "AND table_name = 'model_dataset' AND column_name IN (" + ", ".join(f"'{c}'" for c in FORBIDDEN_IN_MART) + ")",
     lambda v: v == 0, "FAIL"),
    ("Q08", "ICU rate in mart (%) within the expected 30-60 range (WP1 reference ~44-45)",
     "SELECT round(100.0 * avg(icu), 1) FROM mart.model_dataset",
     lambda v: 30 <= v <= 60, "WARN"),
    ("Q09", "share of surgeries whose patient has a history (%), expected > 90",
     "SELECT round(100.0 * avg(has_history), 1) FROM mart.model_dataset",
     lambda v: v > 90, "WARN"),
    ("Q10", "surgeries without a valid ICU label (S08, expected 0)",
     "SELECT rows_affected FROM etl.rule_log WHERE rule_id = 'S08'",
     lambda v: v == 0, "WARN"),
    ("Q11", "timestamp-order issues in clean.surgery (%), expected < 1",
     "SELECT round(100.0 * avg(ts_order_issue), 2) FROM clean.surgery",
     lambda v: v < 1, "WARN"),
    ("Q12", "history rows without a diagnosis code (%), expected < 40",
     "SELECT round(100.0 * avg((dx_code IS NULL)::int), 1) FROM clean.history",
     lambda v: v < 40, "WARN"),
]

#funtion to run checks as defined before 
def cmd_checks(args) -> None:
    failed = False
    with connect() as conn:
        for qid, desc, sql, ok, severity in CHECKS:
            try:
                value = conn.execute(sql).fetchone()[0]
                status = "PASS" if (value is not None and ok(value)) else severity
            except psycopg.Error as exc:
                conn.rollback()
                value, status = f"error: {str(exc).splitlines()[0]}", "FAIL"
            failed |= status == "FAIL"
            print(f"[{status}] {qid} {desc} -> {value}")
    if failed:
        sys.exit(1)


# --------------------------------------------------------------------------- CLI
def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("init").set_defaults(func=cmd_init)

    s = sub.add_parser("load-csv")
    s.add_argument("--file", required=True)
    s.add_argument("--table", help="raw table name (default: file name without extension)")
    s.add_argument("--force", action="store_true", help="load again even if the same file was already loaded")
    s.set_defaults(func=cmd_load_csv)

    s = sub.add_parser("load-ehr")
    s.add_argument("--table", required=True)
    s.add_argument("--query-file", required=True)
    s.set_defaults(func=cmd_load_ehr)

    sub.add_parser("transform").set_defaults(func=cmd_transform)
    sub.add_parser("checks").set_defaults(func=cmd_checks)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
