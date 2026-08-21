import subprocess
import time
import sys

def run_cmd(cmd, check=True):
    print(f"Executing: {cmd}")
    res = subprocess.run(cmd, shell=True)
    if check and res.returncode != 0:
        print(f"❌ Command failed: {cmd}")
        sys.exit(res.returncode)

def main():
    print("🚀 Bắt đầu quá trình Reset Database RidePulse...")

    # Bước 1: Clear database & storage volumes
    print("\n--- BƯỚC 1: Xóa DB & Volumes cũ ---")
    run_cmd("docker compose down -v")
    run_cmd("docker compose up -d db minio")
    print("⏳ Chờ PostgreSQL khởi động (8 giây)...")
    time.sleep(8)

    # Bước 2: Chạy các migration gốc
    print("\n--- BƯỚC 2: Chạy Migration gốc 001 và 002 ---")
    run_cmd("docker compose exec -T db psql -U postgres -d ridepulse -f /scripts/migrations/001_schema.sql")
    run_cmd("docker compose exec -T db psql -U postgres -d ridepulse -f /scripts/migrations/002_roles.sql")

    # Bước 3: Khởi chạy API & Worker để sinh bảng ORM chuẩn
    print("\n--- BƯỚC 3: Khởi chạy API & Worker để tạo cấu trúc ORM ---")
    run_cmd("docker compose up -d api worker")
    print("⏳ Chờ API khởi động và tạo bảng (8 giây)...")
    time.sleep(8)

    # Bước 4: Chạy các migration bổ trợ từ 003 đến 007
    print("\n--- BƯỚC 4: Chạy các migration bổ trợ từ 003 đến 007 ---")
    migrations = [
        "003_gate2_schema.sql",
        "004_fix_audit_schema.sql",
        "005_canonical_dataset_contract.sql",
        "006_rule_proposal_core_evidence.sql",
        "007_graph2_3_models.sql"
    ]
    for m in migrations:
        run_cmd(f"docker compose exec -T db psql -U postgres -d ridepulse -f /scripts/migrations/{m}")

    # Bước 5: Chạy migration phân tách schema 008
    print("\n--- BƯỚC 5: Chạy migration phân tách Schema 008 ---")
    run_cmd("docker compose exec -T db psql -U postgres -d ridepulse -f /scripts/migrations/008_split_schemas.sql")

    print("\n🎉 RESET DATABASE VÀ ĐỒNG BỘ CẤU TRÚC THÀNH CÔNG!")

if __name__ == "__main__":
    main()
