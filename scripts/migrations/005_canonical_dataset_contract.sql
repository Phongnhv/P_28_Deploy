-- Canonical dataset contract for Supabase/PostgreSQL.
--
-- trips_raw remains the immutable ingestion boundary:
--   (source_row_id varchar, dataset_id varchar, values json)
-- Consumers must read trips_canonical instead of assuming typed columns exist on
-- trips_raw. Invalid scalar representations become NULL rather than aborting a
-- profile or DQ run.

CREATE INDEX IF NOT EXISTS ix_trips_raw_dataset_id
    ON public.trips_raw (dataset_id);

CREATE OR REPLACE VIEW public.trips_canonical AS
SELECT
    source_row_id,
    dataset_id,
    NULLIF(values ->> 'vendor_id', '') AS vendor_id,
    CASE
        WHEN pg_input_is_valid(NULLIF(values ->> 'pickup_at', ''), 'timestamp without time zone')
        THEN (values ->> 'pickup_at')::timestamp without time zone
    END AS pickup_at,
    CASE
        WHEN pg_input_is_valid(NULLIF(values ->> 'dropoff_at', ''), 'timestamp without time zone')
        THEN (values ->> 'dropoff_at')::timestamp without time zone
    END AS dropoff_at,
    CASE
        WHEN pg_input_is_valid(NULLIF(values ->> 'passenger_count', ''), 'double precision')
        THEN (values ->> 'passenger_count')::double precision
    END AS passenger_count,
    CASE
        WHEN pg_input_is_valid(NULLIF(values ->> 'trip_distance', ''), 'double precision')
        THEN (values ->> 'trip_distance')::double precision
    END AS trip_distance,
    NULLIF(values ->> 'rate_code_id', '') AS rate_code_id,
    NULLIF(values ->> 'store_and_fwd_flag', '') AS store_and_fwd_flag,
    NULLIF(values ->> 'pickup_location_id', '') AS pickup_location_id,
    NULLIF(values ->> 'dropoff_location_id', '') AS dropoff_location_id,
    NULLIF(values ->> 'payment_type', '') AS payment_type,
    CASE WHEN pg_input_is_valid(NULLIF(values ->> 'fare_amount', ''), 'double precision')
        THEN (values ->> 'fare_amount')::double precision END AS fare_amount,
    CASE WHEN pg_input_is_valid(NULLIF(values ->> 'extra', ''), 'double precision')
        THEN (values ->> 'extra')::double precision END AS extra,
    CASE WHEN pg_input_is_valid(NULLIF(values ->> 'mta_tax', ''), 'double precision')
        THEN (values ->> 'mta_tax')::double precision END AS mta_tax,
    CASE WHEN pg_input_is_valid(NULLIF(values ->> 'tip_amount', ''), 'double precision')
        THEN (values ->> 'tip_amount')::double precision END AS tip_amount,
    CASE WHEN pg_input_is_valid(NULLIF(values ->> 'tolls_amount', ''), 'double precision')
        THEN (values ->> 'tolls_amount')::double precision END AS tolls_amount,
    CASE WHEN pg_input_is_valid(NULLIF(values ->> 'improvement_surcharge', ''), 'double precision')
        THEN (values ->> 'improvement_surcharge')::double precision END AS improvement_surcharge,
    CASE WHEN pg_input_is_valid(NULLIF(values ->> 'total_amount', ''), 'double precision')
        THEN (values ->> 'total_amount')::double precision END AS total_amount,
    CASE WHEN pg_input_is_valid(NULLIF(values ->> 'congestion_surcharge', ''), 'double precision')
        THEN (values ->> 'congestion_surcharge')::double precision END AS congestion_surcharge,
    CASE WHEN pg_input_is_valid(NULLIF(values ->> 'airport_fee', ''), 'double precision')
        THEN (values ->> 'airport_fee')::double precision END AS airport_fee,
    CASE WHEN pg_input_is_valid(NULLIF(values ->> 'cbd_congestion_fee', ''), 'double precision')
        THEN (values ->> 'cbd_congestion_fee')::double precision END AS cbd_congestion_fee
FROM public.trips_raw;

COMMENT ON VIEW public.trips_canonical IS
    'Typed, null-safe projection of immutable trips_raw JSON used by dbt, profiling, and DQ rules.';

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
    ON public.dataset_profiles (generated_at DESC);
CREATE INDEX IF NOT EXISTS ix_dq_rules_dataset_id
    ON public.dq_rules (dataset_id);
CREATE INDEX IF NOT EXISTS ix_dq_runs_dataset_created_at
    ON public.dq_runs (dataset_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_dq_results_run_id
    ON public.dq_results (run_id);
