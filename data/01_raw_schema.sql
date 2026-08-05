
DROP TABLE IF EXISTS payments;
DROP TABLE IF EXISTS dich_vu_xe_trips;
DROP TABLE IF EXISTS drivers;
DROP TABLE IF EXISTS customers;

CREATE TABLE customers (
    customer_id VARCHAR(20),
    full_name VARCHAR(150),
    email VARCHAR(255),
    phone VARCHAR(50),
    city VARCHAR(100),
    customer_status VARCHAR(30),
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE drivers (
    driver_id VARCHAR(20),
    full_name VARCHAR(150),
    phone VARCHAR(50),
    license_number VARCHAR(50),
    vehicle_plate VARCHAR(50),
    cab_type VARCHAR(30),
    rating NUMERIC(4,2),
    driver_status VARCHAR(30),
    joined_at TEXT,
    updated_at TEXT
);

CREATE TABLE dich_vu_xe_trips (
    trip_id VARCHAR(50),
    source_record_id VARCHAR(50),
    customer_id VARCHAR(20),
    driver_id VARCHAR(20),
    cab_type VARCHAR(30),
    product_id VARCHAR(100),
    service_name VARCHAR(100),
    pickup_location VARCHAR(200),
    dropoff_location VARCHAR(200),
    requested_at TEXT,
    timezone VARCHAR(100),
    distance_miles NUMERIC(12,4),
    surge_multiplier NUMERIC(8,4),
    fare_amount NUMERIC(12,2),
    trip_status VARCHAR(30),
    pickup_latitude NUMERIC(12,8),
    pickup_longitude NUMERIC(12,8),
    temperature_f NUMERIC(8,2),
    weather_summary VARCHAR(200),
    weather_icon VARCHAR(100),
    ingested_at TEXT,
    freshness_lag_hours NUMERIC(12,4)
);

CREATE TABLE payments (
    payment_id VARCHAR(30),
    trip_id VARCHAR(50),
    customer_id VARCHAR(20),
    amount NUMERIC(12,2),
    currency VARCHAR(10),
    payment_method VARCHAR(30),
    payment_status VARCHAR(30),
    paid_at TEXT,
    transaction_ref VARCHAR(100),
    created_at TEXT,
    updated_at TEXT
);
