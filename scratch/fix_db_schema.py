import sqlite3

db_path = "c:/DATA/P-028/steward_local.db"
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# Get table info
cursor.execute("PRAGMA table_info(jobs)")
columns = [row[1] for row in cursor.fetchall()]
print("Current columns in 'jobs' table:", columns)

if "lease_expires_at" not in columns:
    print("Adding 'lease_expires_at' column to 'jobs' table...")
    try:
        cursor.execute("ALTER TABLE jobs ADD COLUMN lease_expires_at DATETIME;")
        conn.commit()
        print("Column 'lease_expires_at' added successfully.")
    except Exception as e:
        print("Failed to add column:", e)
else:
    print("'lease_expires_at' already exists.")

conn.close()
