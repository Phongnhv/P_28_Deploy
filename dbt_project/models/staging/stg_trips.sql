{{ config(materialized='view') }}

with source_data as (

    select
        cast(source_row_id as text) as source_row_id,
        cast(vendor_id as integer) as vendor_id,
        cast(pickup_at as timestamp) as pickup_at,
        cast(dropoff_at as timestamp) as dropoff_at,
        cast(passenger_count as integer) as passenger_count,
        cast(trip_distance as double precision) as trip_distance,
        cast(rate_code_id as integer) as rate_code_id,
        cast(store_and_fwd_flag as text) as store_and_fwd_flag,
        cast(pickup_location_id as integer) as pickup_location_id,
        cast(dropoff_location_id as integer) as dropoff_location_id,
        cast(payment_type as integer) as payment_type,
        cast(fare_amount as double precision) as fare_amount,
        cast(extra as double precision) as extra,
        cast(mta_tax as double precision) as mta_tax,
        cast(tip_amount as double precision) as tip_amount,
        cast(tolls_amount as double precision) as tolls_amount,
        cast(improvement_surcharge as double precision) as improvement_surcharge,
        cast(total_amount as double precision) as total_amount,
        cast(congestion_surcharge as double precision) as congestion_surcharge,
        cast(airport_fee as double precision) as airport_fee,
        cast(cbd_congestion_fee as double precision) as cbd_congestion_fee
    from {{ source('public', 'trips_raw') }}

)

select * from source_data
