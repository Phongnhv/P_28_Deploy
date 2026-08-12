#!/bin/bash
set -e

echo "🚀 Bắt đầu cài đặt môi trường Local Development (Docker Compose)..."

# Bước 1: Khởi động Database & Storage
echo "📦 Đang khởi chạy PostgreSQL và MinIO containers..."
docker-compose up -d db minio

echo "⏳ Đang đợi Database khởi động (10 giây)..."
sleep 10

# Bước 2: Tạo Database và Setup Roles
echo "🔧 Đang khởi tạo Database 'ridepulse' và phân quyền (Roles)..."
docker-compose exec -T db psql -U postgres -d postgres -c "CREATE DATABASE ridepulse;" || echo "Database 'ridepulse' đã tồn tại."

# Chạy schema
docker-compose exec -T db psql -U postgres -d ridepulse -f /scripts/migrations/001_schema.sql
# Chạy roles
docker-compose exec -T db psql -U postgres -d ridepulse -f /scripts/migrations/002_roles.sql

# Bước 3: Build và khởi động API & Worker
echo "🐳 Đang build và khởi chạy FastAPI & Worker containers..."
docker-compose up -d api worker --build

echo "✅ Hoàn tất! Hệ thống Local đã sẵn sàng."
echo "API Endpoint: http://localhost:8000"
echo "MinIO Console: http://localhost:9001 (minioadmin / miniopassword)"
