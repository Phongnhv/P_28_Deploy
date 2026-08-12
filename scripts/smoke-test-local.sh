#!/bin/bash

API_URL="http://localhost:8000"

echo "🧪 [1/3] Kiểm tra /health endpoint..."
HEALTH=$(curl -s "$API_URL/health")
echo "   -> $HEALTH"
if [[ "$HEALTH" != *"ok"* ]]; then
    echo "❌ API chưa hoạt động!"
    exit 1
fi

echo -e "\n🧪 [2/3] Kiểm tra /ready endpoint..."
READY=$(curl -s "$API_URL/ready")
echo "   -> $READY"
if [[ "$READY" != *"connected"* ]]; then
    echo "❌ Database kết nối thất bại!"
    exit 1
fi

echo -e "\n🧪 [3/3] Kiểm tra Job Dispatcher & Idempotency..."
IDEMPOTENCY_KEY="local-smoke-$(date +%s)"

echo "   -> Trigger Job lần 1 (Cần trả về 202):"
JOB_RES_1=$(curl -s -X POST "$API_URL/api/v1/jobs" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: $IDEMPOTENCY_KEY" \
  -d '{"type":"TEST_JOB","linked_entity":"smoke-test"}')
echo "      $JOB_RES_1"

echo "   -> Trigger Job lần 2 với cùng Key (Cần trả về 409 Conflict):"
JOB_RES_2=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$API_URL/api/v1/jobs" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: $IDEMPOTENCY_KEY" \
  -d '{"type":"TEST_JOB","linked_entity":"smoke-test"}')
echo "      HTTP Status: $JOB_RES_2"

if [ "$JOB_RES_2" == "409" ]; then
    echo -e "\n✅ TOÀN BỘ SMOKE TEST THÀNH CÔNG!"
else
    echo -e "\n❌ Smoke Test thất bại. (Mong đợi 409 nhưng nhận $JOB_RES_2)"
    exit 1
fi
