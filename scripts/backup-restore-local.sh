#!/bin/bash
set -e

ACTION=$1

if [ "$ACTION" == "backup" ]; then
    echo "💾 Đang sao lưu Database 'ridepulse'..."
    docker-compose exec -T db pg_dump -U postgres -d ridepulse > backup_local.sql
    echo "✅ Đã lưu backup vào file: backup_local.sql"
    
elif [ "$ACTION" == "restore" ]; then
    if [ ! -f "backup_local.sql" ]; then
        echo "❌ Lỗi: Không tìm thấy file backup_local.sql"
        exit 1
    fi
    echo "♻️ Đang phục hồi Database 'ridepulse' từ backup_local.sql..."
    # Drop and recreate DB to ensure clean state
    docker-compose exec -T db psql -U postgres -d postgres -c "DROP DATABASE IF EXISTS ridepulse;"
    docker-compose exec -T db psql -U postgres -d postgres -c "CREATE DATABASE ridepulse;"
    # Restore
    cat backup_local.sql | docker-compose exec -T db psql -U postgres -d ridepulse
    echo "✅ Phục hồi hoàn tất!"
else
    echo "Sử dụng: ./scripts/backup-restore-local.sh [backup|restore]"
    exit 1
fi
