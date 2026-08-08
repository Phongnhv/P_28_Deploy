import hashlib
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd


def _get_project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def generate_semantic_data():
    project_root = _get_project_root()
    raw_data_path = project_root / "data" / "yellow_tripdata_2025" / "raw" / "yellow_tripdata_2025-01.parquet"
    lookup_csv_path = project_root / "data" / "yellow_tripdata_2025" / "matchinfor" / "taxi_zone_lookup.csv"

    if not raw_data_path.exists():
        raise FileNotFoundError(f"Source Parquet file not found at '{raw_data_path}'")
    if not lookup_csv_path.exists():
        raise FileNotFoundError(f"Taxi Zone Lookup CSV not found at '{lookup_csv_path}'")

    print(f"Reading raw data from {raw_data_path}...")
    df_raw = pd.read_parquet(raw_data_path)
    print(f"Raw data loaded: {len(df_raw)} rows.")

    # 1. Deterministic Sampling: Extract 50,000 clean rows
    sample_seed = 42
    df_sampled = df_raw.sample(n=50000, random_state=sample_seed).copy().reset_index(drop=True)

    # 2. Mutate 1,250 defect rows at fixed indices
    mutation_seed = 1337
    np.random.seed(mutation_seed)
    mutation_indices = np.random.choice(50000, size=1250, replace=False)

    g1_idx = mutation_indices[0:250]     # negative_fare_amount
    g2_idx = mutation_indices[250:500]   # negative_trip_distance
    g3_idx = mutation_indices[500:750]   # null_vendor_id
    g4_idx = mutation_indices[750:1000]  # invalid_payment_type
    g5_idx = mutation_indices[1000:1250] # duplicate_fingerprint

    # Apply synthetic defect mutations
    df_sampled.loc[g1_idx, "fare_amount"] = -15.0
    df_sampled.loc[g1_idx, "total_amount"] = -18.5
    df_sampled.loc[g2_idx, "trip_distance"] = -2.5
    df_sampled.loc[g3_idx, "VendorID"] = np.nan
    df_sampled.loc[g4_idx, "payment_type"] = 99

    # Duplicate fingerprint with row 0
    source_clean_idx = 0
    clean_vendor = df_sampled.loc[source_clean_idx, "VendorID"]
    clean_pickup = df_sampled.loc[source_clean_idx, "tpep_pickup_datetime"]
    clean_dropoff = df_sampled.loc[source_clean_idx, "tpep_dropoff_datetime"]
    clean_pu_loc = df_sampled.loc[source_clean_idx, "PULocationID"]
    clean_do_loc = df_sampled.loc[source_clean_idx, "DOLocationID"]
    clean_dist = df_sampled.loc[source_clean_idx, "trip_distance"]

    for idx in g5_idx:
        df_sampled.loc[idx, "VendorID"] = clean_vendor
        df_sampled.loc[idx, "tpep_pickup_datetime"] = clean_pickup
        df_sampled.loc[idx, "tpep_dropoff_datetime"] = clean_dropoff
        df_sampled.loc[idx, "PULocationID"] = clean_pu_loc
        df_sampled.loc[idx, "DOLocationID"] = clean_do_loc
        df_sampled.loc[idx, "trip_distance"] = clean_dist

    # 3. Read Taxi Zone Lookup CSV
    df_lookup = pd.read_csv(lookup_csv_path)
    zone_dict = df_lookup.set_index("LocationID").to_dict("index")

    # Helper function to get zone text label: "Borough (Zone)"
    def format_zone_label(loc_id):
        if pd.isna(loc_id) or int(loc_id) not in zone_dict:
            return "Unknown Location"
        info = zone_dict[int(loc_id)]
        return f"{info['Borough']} ({info['Zone']})"

    # 4. Direct Value Replacement In-Place (Strictly 21 original columns, NO extra columns added)
    # VendorID mapping
    vendor_map = {
        1: "Creative Mobile Technologies, LLC",
        2: "Curb Mobility, LLC",
        6: "Myle Technologies Inc",
        7: "Helix",
    }
    df_sampled["VendorID"] = df_sampled["VendorID"].map(lambda x: vendor_map.get(x, "Unknown Vendor") if pd.notna(x) else "Unknown Vendor")

    # RatecodeID mapping
    ratecode_map = {
        1: "Standard rate",
        2: "JFK",
        3: "Newark",
        4: "Nassau or Westchester",
        5: "Negotiated fare",
        6: "Group ride",
        99: "Null/Unknown",
    }
    df_sampled["RatecodeID"] = df_sampled["RatecodeID"].map(lambda x: ratecode_map.get(x, "Unknown Ratecode") if pd.notna(x) else "Unknown Ratecode")

    # payment_type mapping
    payment_map = {
        0: "Flex Fare trip",
        1: "Credit card",
        2: "Cash",
        3: "No charge",
        4: "Dispute",
        5: "Unknown",
        6: "Voided trip",
        99: "Invalid Payment (Dispute/Test)",
    }
    df_sampled["payment_type"] = df_sampled["payment_type"].map(lambda x: payment_map.get(x, "Unknown Payment") if pd.notna(x) else "Unknown Payment")

    # PULocationID & DOLocationID replacement in-place
    df_sampled["PULocationID"] = df_sampled["PULocationID"].apply(format_zone_label)
    df_sampled["DOLocationID"] = df_sampled["DOLocationID"].apply(format_zone_label)

    # Standardize Column Names to snake_case
    column_mapping = {
        "VendorID": "vendor_id",
        "tpep_pickup_datetime": "pickup_at",
        "tpep_dropoff_datetime": "dropoff_at",
        "passenger_count": "passenger_count",
        "trip_distance": "trip_distance",
        "RatecodeID": "rate_code_id",
        "store_and_fwd_flag": "store_and_fwd_flag",
        "PULocationID": "pickup_location_id",
        "DOLocationID": "dropoff_location_id",
        "payment_type": "payment_type",
        "fare_amount": "fare_amount",
        "extra": "extra",
        "mta_tax": "mta_tax",
        "tip_amount": "tip_amount",
        "tolls_amount": "tolls_amount",
        "improvement_surcharge": "improvement_surcharge",
        "total_amount": "total_amount",
        "congestion_surcharge": "congestion_surcharge",
        "Airport_fee": "airport_fee",
        "cbd_congestion_fee": "cbd_congestion_fee",
    }
    df_sampled.rename(columns=column_mapping, inplace=True)

    # Standardize pickup_at & dropoff_at to ISO string format (YYYY-MM-DDTHH:MM:SSZ)
    pickup_dt = pd.to_datetime(df_sampled["pickup_at"], utc=True)
    dropoff_dt = pd.to_datetime(df_sampled["dropoff_at"], utc=True)
    df_sampled["pickup_at"] = pickup_dt.dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    df_sampled["dropoff_at"] = dropoff_dt.dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    # Add deterministic unique source_row_id (row-00001 to row-50000)
    row_ids = [f"row-{i:05d}" for i in range(1, 50001)]
    df_sampled.insert(0, "source_row_id", row_ids)

    # Keep strictly the 21 columns
    expected_21_cols = [
        "source_row_id", "vendor_id", "pickup_at", "dropoff_at", "passenger_count",
        "trip_distance", "rate_code_id", "store_and_fwd_flag", "pickup_location_id",
        "dropoff_location_id", "payment_type", "fare_amount", "extra", "mta_tax",
        "tip_amount", "tolls_amount", "improvement_surcharge", "total_amount",
        "congestion_surcharge", "airport_fee", "cbd_congestion_fee"
    ]
    df_sampled = df_sampled[expected_21_cols]

    # Save Output Files to data/yellow_tripdata_2025/semantic_data/
    out_semantic_dir = project_root / "data" / "yellow_tripdata_2025" / "semantic_data"
    out_resources_dir = project_root / "src" / "resources"

    os.makedirs(out_semantic_dir, exist_ok=True)
    os.makedirs(out_resources_dir, exist_ok=True)

    semantic_parquet_path = out_semantic_dir / "yellow_tripdata_2025_semantic_50k.parquet"
    semantic_csv_path = out_semantic_dir / "yellow_tripdata_2025_semantic_50k.csv"
    res_parquet_path = out_resources_dir / "nyc_yellow_50k.parquet"

    df_sampled.to_parquet(semantic_parquet_path, index=False)
    df_sampled.to_csv(semantic_csv_path, index=False)
    df_sampled.to_parquet(res_parquet_path, index=False)

    print(f"Saved 21-column semantic parquet to {semantic_parquet_path}")
    print(f"Saved 21-column semantic CSV sample to {semantic_csv_path}")


    # Compute SHA-256 Checksum
    sha256 = hashlib.sha256()
    with open(semantic_parquet_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha256.update(chunk)
    file_hash = sha256.hexdigest()
    print(f"Calculated SHA-256 Hash: {file_hash}")

    # Build Manifest Metadata
    columns_schema = []
    for col in df_sampled.columns:
        if col == "source_row_id":
            col_type = "string"
            nullable = False
        elif "at" in col or col in ["store_and_fwd_flag", "vendor_id", "rate_code_id", "payment_type", "pickup_location_id", "dropoff_location_id"]:
            col_type = "string"
            nullable = True
        elif col == "passenger_count":
            col_type = "integer"
            nullable = True
        else:
            col_type = "float"
            nullable = True

        columns_schema.append({
            "name": col,
            "type": col_type,
            "nullable": nullable
        })

    manifest = {
        "manifest_name": "nyc-yellow-50k-v1",
        "dataset_name": "NYC Yellow Taxi Trip Records (Semantic Enriched 50k Fixture - 21 Columns)",
        "source_type": "nyc_yellow_50k_semantic_parquet",
        "local_path": "data/yellow_tripdata_2025/semantic_data/yellow_tripdata_2025_semantic_50k.parquet",
        "file_sha256": file_hash,
        "total_rows": 50000,
        "clean_rows": 48750,
        "defect_rows": 1250,
        "sample_seed": sample_seed,
        "mutation_seed": mutation_seed,
        "schema_version": "1.0",
        "columns": columns_schema,
        "expected_aggregate_defect_counts": {
            "negative_fare_amount": 250,
            "negative_trip_distance": 250,
            "null_vendor_id": 250,
            "invalid_payment_type": 250,
            "duplicate_fingerprint": 250
        }
    }

    manifest_semantic_path = out_semantic_dir / "manifest.json"
    manifest_res_path = out_resources_dir / "manifest.json"
    manifest_50k_path = out_resources_dir / "manifest_50k.json"

    with open(manifest_semantic_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    with open(manifest_res_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    with open(manifest_50k_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    print(f"Generated manifest at {manifest_semantic_path} and {manifest_res_path}")


if __name__ == "__main__":
    generate_semantic_data()
