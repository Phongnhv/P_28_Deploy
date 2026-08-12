#!/bin/bash

# Smoke Test Script for Gate 2 Local Host - Job Lifecycle Verification
# Make sure docker-compose is running before executing this script.

API_URL="http://localhost:8000"

echo "🧪 [1/4] Checking /health endpoint..."
HEALTH=$(curl -s "$API_URL/health")
echo "   -> $HEALTH"
if [[ "$HEALTH" != *"ok"* ]]; then
    echo "❌ API is not running!"
    exit 1
fi

echo -e "\n🧪 [2/4] Checking /ready endpoint..."
READY=$(curl -s "$API_URL/ready")
echo "   -> $READY"
if [[ "$READY" != *"connected"* ]]; then
    echo "❌ Database connection failed!"
    exit 1
fi

echo -e "\n🧪 [3/4] Testing Job Dispatcher & Idempotency..."
IDEMPOTENCY_KEY="local-smoke-$(date +%s)"

echo "   -> Dispatching INGEST_PROFILE Job (expecting 202):"
JOB_RES_1=$(curl -s -X POST "$API_URL/api/v1/jobs" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: $IDEMPOTENCY_KEY" \
  -d '{"type":"INGEST_PROFILE","linked_entity":"yellow_tripdata"}')
echo "      $JOB_RES_1"

JOB_ID=$(echo "$JOB_RES_1" | grep -o '"job_id":"[^"]*' | grep -o '[^"]*$')
if [ -z "$JOB_ID" ]; then
    echo "❌ Failed to parse job_id from response!"
    exit 1
fi
echo "      Parsed Job ID: $JOB_ID"

echo "   -> Dispatching Job again with same Idempotency-Key (expecting 409):"
JOB_RES_2=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$API_URL/api/v1/jobs" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: $IDEMPOTENCY_KEY" \
  -d '{"type":"INGEST_PROFILE","linked_entity":"yellow_tripdata"}')
echo "      HTTP Status: $JOB_RES_2"

if [ "$JOB_RES_2" != "409" ]; then
    echo "❌ Idempotency check failed (expected 409, got $JOB_RES_2)!"
    exit 1
fi

echo -e "\n🧪 [4/4] Polling Job Status (GET /api/v1/jobs/{job_id})..."
MAX_RETRIES=10
RETRY_COUNT=0
STATUS="PENDING"

while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
    echo "   -> Polling status (Attempt $((RETRY_COUNT + 1))/$MAX_RETRIES)..."
    POLL_RES=$(curl -s "$API_URL/api/v1/jobs/$JOB_ID")
    echo "      Response: $POLL_RES"
    
    STATUS=$(echo "$POLL_RES" | grep -o '"status":"[^"]*' | grep -o '[^"]*$')
    if [ "$STATUS" == "SUCCEEDED" ] || [ "$STATUS" == "COMPLETED" ]; then
        echo "   -> Job succeeded!"
        break
    elif [ "$STATUS" == "FAILED" ]; then
        echo "❌ Job execution failed!"
        exit 1
    fi
    
    RETRY_COUNT=$((RETRY_COUNT + 1))
    sleep 2
done

if [ "$STATUS" != "SUCCEEDED" ] && [ "$STATUS" != "COMPLETED" ]; then
    echo "❌ Job timed out or failed to reach success status!"
    exit 1
fi

echo -e "\n✅ ALL SMOKE TESTS PASSED SUCCESSFULLY!"
exit 0
