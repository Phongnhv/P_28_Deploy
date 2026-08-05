
-- ==========================================================
-- 1. NULL PRIMARY KEY
-- ==========================================================

SELECT COUNT(*) AS null_trip_pk
FROM dich_vu_xe_trips
WHERE trip_id IS NULL OR TRIM(trip_id) = '';

SELECT COUNT(*) AS null_driver_pk
FROM drivers
WHERE driver_id IS NULL OR TRIM(driver_id) = '';

SELECT COUNT(*) AS null_customer_pk
FROM customers
WHERE customer_id IS NULL OR TRIM(customer_id) = '';

SELECT COUNT(*) AS null_payment_pk
FROM payments
WHERE payment_id IS NULL OR TRIM(payment_id) = '';


-- ==========================================================
-- 2. PRIMARY KEY TRÙNG
-- ==========================================================

SELECT trip_id, COUNT(*)
FROM dich_vu_xe_trips
WHERE trip_id IS NOT NULL
GROUP BY trip_id
HAVING COUNT(*) > 1;

SELECT driver_id, COUNT(*)
FROM drivers
WHERE driver_id IS NOT NULL
GROUP BY driver_id
HAVING COUNT(*) > 1;

SELECT customer_id, COUNT(*)
FROM customers
WHERE customer_id IS NOT NULL
GROUP BY customer_id
HAVING COUNT(*) > 1;

SELECT payment_id, COUNT(*)
FROM payments
WHERE payment_id IS NOT NULL
GROUP BY payment_id
HAVING COUNT(*) > 1;


-- ==========================================================
-- 3. CƯỚC ÂM
-- ==========================================================

SELECT *
FROM dich_vu_xe_trips
WHERE fare_amount < 0;

SELECT *
FROM payments
WHERE amount < 0;


-- ==========================================================
-- 4. EMAIL SAI FORMAT
-- ==========================================================

SELECT *
FROM customers
WHERE email IS NULL
   OR email !~ '^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$';


-- ==========================================================
-- 5. PHONE SAI FORMAT
-- ==========================================================

SELECT *
FROM customers
WHERE phone IS NULL
   OR phone !~ '^\+1-[0-9]{3}-[0-9]{3}-[0-9]{4}$';

SELECT *
FROM drivers
WHERE phone IS NULL
   OR phone !~ '^\+1-[0-9]{3}-[0-9]{3}-[0-9]{4}$';


-- ==========================================================
-- 6. LICENSE SAI FORMAT
-- ==========================================================

SELECT *
FROM drivers
WHERE license_number IS NULL
   OR license_number !~ '^MA-[0-9]{7}$';


-- ==========================================================
-- 7. TRANSACTION REF SAI FORMAT
-- ==========================================================

SELECT *
FROM payments
WHERE transaction_ref IS NULL
   OR transaction_ref !~ '^TXN-[0-9]{12}$';


-- ==========================================================
-- 8. FRESHNESS LAG
-- ==========================================================

SELECT *
FROM dich_vu_xe_trips
WHERE freshness_lag_hours > 24;


-- ==========================================================
-- 9. ORPHAN FOREIGN KEYS
-- ==========================================================

SELECT t.*
FROM dich_vu_xe_trips t
LEFT JOIN customers c
    ON t.customer_id = c.customer_id
WHERE c.customer_id IS NULL;

SELECT t.*
FROM dich_vu_xe_trips t
LEFT JOIN drivers d
    ON t.driver_id = d.driver_id
WHERE d.driver_id IS NULL;

SELECT p.*
FROM payments p
LEFT JOIN dich_vu_xe_trips t
    ON p.trip_id = t.trip_id
WHERE t.trip_id IS NULL;

SELECT p.*
FROM payments p
LEFT JOIN customers c
    ON p.customer_id = c.customer_id
WHERE c.customer_id IS NULL;
