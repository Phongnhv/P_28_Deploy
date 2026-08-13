{{ config(materialized='view') }}

select
    source_row_id,
    vendor_id,
    pickup_at,
    dropoff_at,
    passenger_count,
    trip_distance,
    rate_code_id,
    payment_type,
    fare_amount,
    total_amount,
    pickup_location_id,
    dropoff_location_id
from {{ ref('stg_trips') }}
