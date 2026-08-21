-- Canonical dataset contract for Supabase/PostgreSQL.
--
-- In local environment, we map trips_canonical view to public.source_rows
-- instead of public.trips_raw, because source_rows has the typed flat columns.

CREATE TABLE IF NOT EXISTS public.source_rows (
    source_row_id VARCHAR(256) PRIMARY KEY,
    dataset_id VARCHAR(256) NOT NULL,
    vendor_id VARCHAR(64),
    pickup_at VARCHAR(64),
    dropoff_at VARCHAR(64),
    passenger_count INT,
    trip_distance FLOAT,
    rate_code_id VARCHAR(64),
    store_and_fwd_flag VARCHAR(64),
    pickup_location_id VARCHAR(64),
    dropoff_location_id VARCHAR(64),
    payment_type VARCHAR(64),
    fare_amount FLOAT,
    extra FLOAT,
    mta_tax FLOAT,
    tip_amount FLOAT,
    tolls_amount FLOAT,
    improvement_surcharge FLOAT,
    total_amount FLOAT,
    congestion_surcharge FLOAT,
    airport_fee FLOAT,
    cbd_congestion_fee FLOAT
);

CREATE INDEX IF NOT EXISTS ix_source_rows_dataset_id
    ON public.source_rows (dataset_id);

CREATE OR REPLACE VIEW public.trips_canonical AS
SELECT
    source_row_id,
    dataset_id,
    vendor_id,
    CASE 
        WHEN NULLIF(pickup_at, '') IS NOT NULL THEN pickup_at::timestamp without time zone 
    END AS pickup_at,
    CASE 
        WHEN NULLIF(dropoff_at, '') IS NOT NULL THEN dropoff_at::timestamp without time zone 
    END AS dropoff_at,
    passenger_count::double precision AS passenger_count,
    trip_distance::double precision AS trip_distance,
    rate_code_id,
    store_and_fwd_flag,
    pickup_location_id,
    dropoff_location_id,
    payment_type,
    fare_amount::double precision AS fare_amount,
    extra::double precision AS extra,
    mta_tax::double precision AS mta_tax,
    tip_amount::double precision AS tip_amount,
    tolls_amount::double precision AS tolls_amount,
    improvement_surcharge::double precision AS improvement_surcharge,
    total_amount::double precision AS total_amount,
    congestion_surcharge::double precision AS congestion_surcharge,
    airport_fee::double precision AS airport_fee,
    cbd_congestion_fee::double precision AS cbd_congestion_fee
FROM public.source_rows;

COMMENT ON VIEW public.trips_canonical IS
    'Typed, null-safe projection of immutable source_rows used by dbt, profiling, and DQ rules.';

DO $$
DECLARE
    role_name text;
BEGIN
    FOREACH role_name IN ARRAY ARRAY['ridepulse_app', 'ridepulse_runner', 'ridepulse_dbt']
    LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = role_name) THEN
            EXECUTE format('GRANT SELECT ON public.trips_canonical TO %I', role_name);
        END IF;
    END LOOP;
END
$$;

CREATE INDEX IF NOT EXISTS ix_dataset_profiles_generated_at
    ON public.dataset_profiles (created_at DESC); -- Postgres 15 generated_at fix
CREATE INDEX IF NOT EXISTS ix_dq_rules_dataset_id
    ON public.dq_rules (dataset_id);
CREATE INDEX IF NOT EXISTS ix_dq_runs_dataset_created_at
    ON public.dq_runs (dataset_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_dq_results_run_id
    ON public.dq_results (run_id);
