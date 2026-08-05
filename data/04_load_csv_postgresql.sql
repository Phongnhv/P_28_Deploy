
-- Chạy bằng psql sau khi tạo bảng từ 01_raw_schema.sql.
-- Điều chỉnh đường dẫn CSV theo máy của bạn.

\copy customers
FROM 'customers.csv'
WITH (
    FORMAT CSV,
    HEADER TRUE,
    ENCODING 'UTF8'
);

\copy drivers
FROM 'drivers.csv'
WITH (
    FORMAT CSV,
    HEADER TRUE,
    ENCODING 'UTF8'
);

\copy dich_vu_xe_trips
FROM 'dich_vu_xe_trips.csv'
WITH (
    FORMAT CSV,
    HEADER TRUE,
    ENCODING 'UTF8'
);

\copy payments
FROM 'payments.csv'
WITH (
    FORMAT CSV,
    HEADER TRUE,
    ENCODING 'UTF8'
);
