#!/usr/bin/env bash
# ==============================================================================
# Script to run LangGraph Pipelines in src/agents/graph.py
#
# Usage:
#   bash scripts/run_graph.sh          (Interactive menu)
#   bash scripts/run_graph.sh 1        (Run Propose Graph)
#   bash scripts/run_graph.sh 2        (Run Execution Graph)
#   bash scripts/run_graph.sh 3        (Run Full Pipeline: Propose -> Approve -> Execute)
# ==============================================================================

set -e

# Chuyển về thư mục root của project
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$REPO_ROOT"
export PYTHONPATH="$REPO_ROOT:${PYTHONPATH:-}"

# Tìm Python executable phù hợp
find_python() {
  if [ -n "${VIRTUAL_ENV:-}" ]; then
    if [ -x "$VIRTUAL_ENV/Scripts/python.exe" ]; then
      echo "$VIRTUAL_ENV/Scripts/python.exe"
      return 0
    elif [ -x "$VIRTUAL_ENV/bin/python" ]; then
      echo "$VIRTUAL_ENV/bin/python"
      return 0
    fi
  fi

  if [ -x "$REPO_ROOT/.venv/Scripts/python.exe" ]; then
    echo "$REPO_ROOT/.venv/Scripts/python.exe"
    return 0
  elif [ -x "$REPO_ROOT/.venv/bin/python" ]; then
    echo "$REPO_ROOT/.venv/bin/python"
    return 0
  elif [ -x "$REPO_ROOT/venv/Scripts/python.exe" ]; then
    echo "$REPO_ROOT/venv/Scripts/python.exe"
    return 0
  elif [ -x "$REPO_ROOT/venv/bin/python" ]; then
    echo "$REPO_ROOT/venv/bin/python"
    return 0
  fi

  if command -v python3 >/dev/null 2>&1; then
    echo "python3"
    return 0
  elif command -v python >/dev/null 2>&1; then
    echo "python"
    return 0
  elif command -v py >/dev/null 2>&1; then
    echo "py -3"
    return 0
  fi

  echo ""
}

PYTHON_CMD=$(find_python)

if [ -z "$PYTHON_CMD" ]; then
  echo "❌ Không tìm thấy Python! Vui lòng kích hoạt virtualenv hoặc cài đặt Python."
  exit 1
fi

# Hàm thực thi graph
run_graph() {
  local mode="$1"
  echo "================================================================="
  case "$mode" in
    1|proposal|propose)
      echo "🚀 Đang khởi chạy: [RUN 1] PROPOSAL GRAPH"
      echo "   (raw_profiler -> profiler_digest -> rule_proposer -> hitl_gate)"
      echo "================================================================="
      $PYTHON_CMD -m src.agents.graph 1
      ;;
    2|execution|execute)
      echo "🚀 Đang khởi chạy: [RUN 2] EXECUTION GRAPH"
      echo "   (test_generator -> validate -> repair -> runner -> anomaly -> report)"
      echo "================================================================="
      $PYTHON_CMD -m src.agents.graph 2
      ;;
    3|all|full)
      echo "🚀 Đang khởi chạy: [FULL PIPELINE] RUN 1 ➔ APPROVE ➔ RUN 2"
      echo "================================================================="
      $PYTHON_CMD -m src.agents.graph all
      ;;
    *)
      echo "❌ Lựa chọn không hợp lệ: $mode"
      exit 1
      ;;
  esac
}

# Nếu có truyền tham số dòng lệnh (vd: ./run_graph.sh 1 hoặc ./run_graph.sh 2)
if [ -n "${1:-}" ]; then
  run_graph "$1"
  exit 0
fi

# Menu tương tác nếu không truyền tham số
while true; do
  echo ""
  echo "========================================================"
  echo "         🤖 CHỌN PIPELINE ĐỂ CHẠY GRAPH.PY             "
  echo "========================================================"
  echo " [1] Run Proposal Graph  (Đề xuất Rules từ Profiling)"
  echo " [2] Run Execution Graph (Thực thi Test trên Active Rules)"
  echo " [3] Run Full Pipeline   (Proposal -> Approve -> Execution)"
  echo " [0/q] Thoát"
  echo "========================================================"
  read -r -p "👉 Nhập lựa chọn của bạn [1/2/3/q]: " choice

  case "$choice" in
    1)
      run_graph 1
      break
      ;;
    2)
      run_graph 2
      break
      ;;
    3)
      run_graph 3
      break
      ;;
    0|q|Q)
      echo "👋 Đã thoát."
      exit 0
      ;;
    *)
      echo "⚠️ Lựa chọn không hợp lệ, vui lòng chọn lại (1, 2, 3 hoặc q)."
      ;;
  esac
done
