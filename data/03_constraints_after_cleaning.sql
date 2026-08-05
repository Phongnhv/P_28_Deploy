
-- Chỉ chạy sau khi đã làm sạch dữ liệu.

ALTER TABLE customers
ALTER COLUMN customer_id SET NOT NULL;

ALTER TABLE drivers
ALTER COLUMN driver_id SET NOT NULL;

ALTER TABLE dich_vu_xe_trips
ALTER COLUMN trip_id SET NOT NULL;

ALTER TABLE payments
ALTER COLUMN payment_id SET NOT NULL;


ALTER TABLE customers
ADD CONSTRAINT pk_customers
PRIMARY KEY (customer_id);

ALTER TABLE drivers
ADD CONSTRAINT pk_drivers
PRIMARY KEY (driver_id);

ALTER TABLE dich_vu_xe_trips
ADD CONSTRAINT pk_dich_vu_xe_trips
PRIMARY KEY (trip_id);

ALTER TABLE payments
ADD CONSTRAINT pk_payments
PRIMARY KEY (payment_id);


ALTER TABLE dich_vu_xe_trips
ADD CONSTRAINT fk_trips_customers
FOREIGN KEY (customer_id)
REFERENCES customers(customer_id);

ALTER TABLE dich_vu_xe_trips
ADD CONSTRAINT fk_trips_drivers
FOREIGN KEY (driver_id)
REFERENCES drivers(driver_id);

ALTER TABLE payments
ADD CONSTRAINT fk_payments_trips
FOREIGN KEY (trip_id)
REFERENCES dich_vu_xe_trips(trip_id);

ALTER TABLE payments
ADD CONSTRAINT fk_payments_customers
FOREIGN KEY (customer_id)
REFERENCES customers(customer_id);


ALTER TABLE dich_vu_xe_trips
ADD CONSTRAINT chk_trip_fare_non_negative
CHECK (fare_amount IS NULL OR fare_amount >= 0);

ALTER TABLE payments
ADD CONSTRAINT chk_payment_amount_non_negative
CHECK (amount IS NULL OR amount >= 0);
