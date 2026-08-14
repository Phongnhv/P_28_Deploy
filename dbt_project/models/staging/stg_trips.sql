{{ config(materialized='view') }}

with source_data as (

    select
        cast(source_row_id as text) as source_row_id,
        cast(dataset_id as text) as dataset_id,
        cast(vendor_id as text) as vendor_id,
        pickup_at,
        dropoff_at,
        passenger_count,
        trip_distance,
        cast(rate_code_id as text) as rate_code_id,
        cast(store_and_fwd_flag as text) as store_and_fwd_flag,
        cast(pickup_location_id as text) as pickup_location_id,
        cast(dropoff_location_id as text) as dropoff_location_id,
        cast(payment_type as text) as payment_type,
        fare_amount,
        extra,
        mta_tax,
        tip_amount,
        tolls_amount,
        improvement_surcharge,
        total_amount,
        congestion_surcharge,
        airport_fee,
        cbd_congestion_fee
    from {{ source('public', 'trips_canonical') }}

)

select * from source_data
