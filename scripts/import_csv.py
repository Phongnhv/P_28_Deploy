#!/usr/bin/env python3
import argparse
import csv
import os
import sqlite3
import sys
import time

from dotenv import load_dotenv

# Load env variables
load_dotenv()

# Predefined columns and target types
# Cập nhật cho CSV semantic (yellow_tripdata_2025_semantic_50k.csv):
# - vendor_id: TEXT (tên công ty, vd "Curb Mobility, LLC")
# - pickup_at / dropoff_at: TIMESTAMP (đã đổi tên từ tpep_*)
# - rate_code_id: TEXT (vd "Standard rate", "JFK")
# - pickup_location_id / dropoff_location_id: TEXT (vd "Manhattan (Midtown)")
# - payment_type: TEXT (vd "Credit card", "Cash")
SCHEMA = {
    "vendor_id": "TEXT",
    "pickup_at": "TIMESTAMP",
    "dropoff_at": "TIMESTAMP",
    "passenger_count": "REAL",
    "trip_distance": "REAL",
    "rate_code_id": "TEXT",
    "store_and_fwd_flag": "TEXT",
    "pickup_location_id": "TEXT",
    "dropoff_location_id": "TEXT",
    "payment_type": "TEXT",
    "fare_amount": "REAL",
    "extra": "REAL",
    "mta_tax": "REAL",
    "tip_amount": "REAL",
    "tolls_amount": "REAL",
    "improvement_surcharge": "REAL",
    "total_amount": "REAL",
    "congestion_surcharge": "REAL",
    "airport_fee": "REAL",
    "cbd_congestion_fee": "REAL",
}

# Mapping raw headers (lowercase, stripped) to schema keys
# Hỗ trợ cả tên cũ (tpep_pickup_datetime, vendorid...) và tên mới (pickup_at, vendor_id...)
HEADER_MAP = {
    # tên mới trong CSV semantic
    "vendor_id": "vendor_id",
    "pickup_at": "pickup_at",
    "dropoff_at": "dropoff_at",
    "rate_code_id": "rate_code_id",
    "pickup_location_id": "pickup_location_id",
    "dropoff_location_id": "dropoff_location_id",
    # tên cũ (giữ lại để tương thích nếu dùng file gốc TLC)
    "vendorid": "vendor_id",
    "tpep_pickup_datetime": "pickup_at",
    "tpep_dropoff_datetime": "dropoff_at",
    "ratecodeid": "rate_code_id",
    "pulocationid": "pickup_location_id",
    "dolocationid": "dropoff_location_id",
    # cột chung (tên không đổi)
    "passenger_count": "passenger_count",
    "trip_distance": "trip_distance",
    "store_and_fwd_flag": "store_and_fwd_flag",
    "payment_type": "payment_type",
    "fare_amount": "fare_amount",
    "extra": "extra",
    "mta_tax": "mta_tax",
    "tip_amount": "tip_amount",
    "tolls_amount": "tolls_amount",
    "improvement_surcharge": "improvement_surcharge",
    "total_amount": "total_amount",
    "congestion_surcharge": "congestion_surcharge",
    "airport_fee": "airport_fee",
    "cbd_congestion_fee": "cbd_congestion_fee",
}


def clean_header(h):
    return h.replace("\ufeff", "").strip().lower()


def safe_int(val):
    if not val or val.strip() == "":
        return None
    try:
        return int(float(val))
    except ValueError:
        return None


def safe_float(val):
    if not val or val.strip() == "":
        return None
    try:
        return float(val)
    except ValueError:
        return None


def safe_str(val):
    if val is None:
        return None
    s = val.strip()
    return s if s != "" else None


def parse_row(row, headers):
    parsed = {}
    for raw_h, val in zip(headers, row):
        clean_h = clean_header(raw_h)
        col_name = HEADER_MAP.get(clean_h)
        if not col_name:
            continue

        col_type = SCHEMA.get(col_name)
        if col_type == "INTEGER":
            parsed[col_name] = safe_int(val)
        elif col_type == "REAL":
            parsed[col_name] = safe_float(val)
        else:
            parsed[col_name] = safe_str(val)

    # fill missing columns with None
    for col_name in SCHEMA.keys():
        if col_name not in parsed:
            parsed[col_name] = None

    return [parsed[col] for col in SCHEMA.keys()]


def import_to_sqlite(csv_path, db_path, table_name="yellow_tripdata", batch_size=20000):
    print(f"-> Importing CSV into SQLite database: {db_path} (Table: {table_name})")

    # Ensure directory exists
    db_dir = os.path.dirname(db_path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Create Table
    cols_def = ", ".join([f"{col} {t}" for col, t in SCHEMA.items()])
    create_sql = f"CREATE TABLE IF NOT EXISTS {table_name} ({cols_def});"
    cursor.execute(create_sql)

    # Clear old data if exists
    cursor.execute(f"DELETE FROM {table_name};")
    conn.commit()

    # Insert data
    placeholders = ", ".join(["?"] * len(SCHEMA))
    insert_sql = f"INSERT INTO {table_name} ({', '.join(SCHEMA.keys())}) VALUES ({placeholders})"

    start_time = time.time()
    count = 0
    batch = []

    with open(csv_path, encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        headers = next(reader)

        for row in reader:
            if not row:
                continue
            parsed_row = parse_row(row, headers)
            batch.append(parsed_row)
            count += 1

            if len(batch) >= batch_size:
                cursor.executemany(insert_sql, batch)
                conn.commit()
                print(f"   Processed {count} rows...")
                batch = []

        if batch:
            cursor.executemany(insert_sql, batch)
            conn.commit()
            print(f"   Processed {count} rows...")

    conn.close()
    elapsed = time.time() - start_time
    print(f"Success! Imported {count} rows in {elapsed:.2f} seconds.")


def import_to_postgres(csv_path, conn_string, table_name="yellow_tripdata", batch_size=20000):
    print(f"-> Importing CSV into PostgreSQL database (Table: {table_name})")

    try:
        import psycopg2
        from psycopg2.extras import execute_values
    except ImportError:
        print("Error: psycopg2 is not installed. Please run: pip install psycopg2-binary")
        sys.exit(1)

    # Clean connection string prefix for psycopg2 if it starts with postgresql+psycopg2://
    if conn_string.startswith("postgresql+psycopg2://"):
        conn_string = conn_string.replace("postgresql+psycopg2://", "postgresql://", 1)

    try:
        conn = psycopg2.connect(conn_string)
        cursor = conn.cursor()
    except Exception as e:
        print(f"Error connecting to PostgreSQL: {e}")
        print("Please check your DATABASE_URL in .env or the postgres server status.")
        sys.exit(1)

    # Create Table
    cols_def = ", ".join([f"{col} {t}" for col, t in SCHEMA.items()])
    create_sql = f"CREATE TABLE IF NOT EXISTS {table_name} ({cols_def});"
    cursor.execute(create_sql)

    # Clear old data if exists
    cursor.execute(f"TRUNCATE TABLE {table_name};")
    conn.commit()

    # Insert data
    insert_sql = f"INSERT INTO {table_name} ({', '.join(SCHEMA.keys())}) VALUES %s"

    start_time = time.time()
    count = 0
    batch = []

    with open(csv_path, encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        headers = next(reader)

        for row in reader:
            if not row:
                continue
            parsed_row = parse_row(row, headers)
            batch.append(parsed_row)
            count += 1

            if len(batch) >= batch_size:
                execute_values(cursor, insert_sql, batch)
                conn.commit()
                print(f"   Processed {count} rows...")
                batch = []

        if batch:
            execute_values(cursor, insert_sql, batch)
            conn.commit()
            print(f"   Processed {count} rows...")

    cursor.close()
    conn.close()
    elapsed = time.time() - start_time
    print(f"Success! Imported {count} rows in {elapsed:.2f} seconds.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Import NYC Taxi CSV into SQLite or PostgreSQL")
    parser.add_argument("--csv", default="./data/yellow_tripdata_2025-01.csv", help="Path to CSV file")
    parser.add_argument(
        "--type",
        choices=["sqlite", "postgres"],
        default="sqlite",
        help="Database type to import: 'sqlite' (default, generates .db file) or 'postgres'",
    )
    parser.add_argument(
        "--db-path", default="./data/yellow_tripdata.db", help="For SQLite: output path for the .db file"
    )
    parser.add_argument("--table", default="yellow_tripdata", help="Target table name")
    parser.add_argument("--batch", type=int, default=20000, help="Batch size for insertions")

    args = parser.parse_args()

    # Resolve absolute paths
    csv_abs = os.path.abspath(args.csv)
    if not os.path.exists(csv_abs):
        print(f"Error: CSV file not found at {csv_abs}")
        sys.exit(1)

    if args.type == "sqlite":
        db_abs = os.path.abspath(args.db_path)
        import_to_sqlite(csv_abs, db_abs, args.table, args.batch)
    elif args.type == "postgres":
        conn_string = os.getenv("DATABASE_URL")
        if not conn_string:
            print("Error: DATABASE_URL environment variable is not defined in .env")
            sys.exit(1)
        import_to_postgres(csv_abs, conn_string, args.table, args.batch)

    # Syntax: python -m scripts.import_csv --type postgres --csv ./data/yellow_tripdata_2025_semantic_50k.csv
