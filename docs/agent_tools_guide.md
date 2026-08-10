# Hướng dẫn Phát triển Tools cho các Agent (Agent Tools Specification)

Tài liệu này định nghĩa chi tiết các công cụ (Tools) cần phát triển dưới dạng Python `@tool` trong thư mục [src/agents/tools/](file:///d:/ai_thuc_chien/P-028/src/agents/tools) tương ứng với từng bước trong luồng xử lý Agent đơn giản hóa (v2).

---

## Bảng Tổng hợp các Tools cần phát triển

| Tên Agent | Tên Tool | File đích | Chức năng chính |
| :--- | :--- | :--- | :--- |
| **1. Profiler Agent** | `profile_database` | `db_profiler_tool.py` | Quét cấu trúc bảng và tính toán các thống kê mô tả (Null rate, Min, Max, Average, Distinct count) trên target database. *(Đã có code mẫu)* |
| **2. LLM Rule Proposer** | `query_historical_rules` | `chroma_rag_tool.py` | Truy vấn các quy tắc DQ tương tự đã lưu trong Vector DB (ChromaDB) để phục vụ cơ chế RAG. |
| | `save_proposed_rules` | `db_profiler_tool.py` *(hoặc database tool)* | Lưu trữ các rule do LLM đề xuất vào database PostgreSQL của ứng dụng để Data Steward có thể xem qua giao diện Admin. |
| **3. Test Generator Agent** | `generate_dbt_test_code` | `dbt_generator_tool.py` | Tạo file cấu hình YAML/SQL dbt test tương ứng với các rules đã được phê duyệt. |
| | `validate_dbt_syntax` | `dbt_generator_tool.py` | Dry-run / kiểm tra tính đúng đắn của cú pháp file dbt vừa sinh trước khi chạy chính thức. |
| **4. Test Runner & Anomaly** | `run_dbt_test_pipeline` | `dbt_generator_tool.py` | Gọi lệnh thực thi `dbt test` hoặc trigger Dagster job để chạy bài test trên Data Warehouse. |
| | `detect_anomalies_ml` | `ml_anomaly_tool.py` | Sử dụng `scikit-learn` (Isolation Forest hoặc Z-Score) trên chuỗi dữ liệu lịch sử để phát hiện các chỉ số DQ bất thường. |
| **5. Diagnostic & Alert** | `send_slack_alert` | `alert_tool.py` | Gửi cảnh báo lỗi và kết quả chẩn đoán lỗi kèm mã SQL gợi ý khắc phục qua webhook Slack / Email. |

---

## Chi tiết đặc tả & Gợi ý Code cho từng Tool

### 1. Profiler Agent Tools

#### 📌 Tool: `profile_database`
* **File:** [db_profiler_tool.py](file:///d:/ai_thuc_chien/P-028/src/agents/tools/db_profiler_tool.py) *(Đã được xây dựng sẵn)*
* **Mô tả:** Đọc thông tin các cột từ database và thực thi một câu lệnh truy vấn SQL tổng hợp để thống kê số lượng dòng, tỉ lệ Null, Min/Max/Mean của các cột số mà không tải dữ liệu nhạy cảm lên LLM.
* **Đầu vào:**
  * `connection_string` (str): Chuỗi kết nối DB (PostgreSQL hoặc SQLite).
  * `table_name` (str): Tên bảng cần thống kê.
  * `sampling_rate` (float): Tỷ lệ lấy mẫu (từ `0.0` đến `1.0`).
* **Đầu ra:** Chuỗi JSON chứa toàn bộ schema và kết quả profiling.

---

### 2. LLM Rule Proposer Tools

#### 📌 Tool: `query_historical_rules`
* **File:** [chroma_rag_tool.py](file:///d:/ai_thuc_chien/P-028/src/agents/tools/chroma_rag_tool.py)
* **Mô tả:** Tìm kiếm ngữ nghĩa trong ChromaDB các quy tắc chất lượng dữ liệu tương tự đã từng được duyệt trong quá khứ đối với bảng hoặc cột có tên tương tự.
* **Đầu vào:**
  * `table_name` (str): Tên bảng hiện tại.
  * `column_name` (str): Tên cột cần tham khảo.
  * `top_k` (int): Số lượng rule lịch sử cần lấy ra.
* **Đầu ra:** Danh sách các rule phù hợp dưới dạng text/JSON để chèn vào prompt LLM.
* **Gợi ý Mock Code:**
```python
from langchain_core.tools import tool
import chromadb

@tool
def query_historical_rules(table_name: str, column_name: str, top_k: int = 3) -> str:
    """Truy vấn các quy tắc DQ đã được duyệt trong quá khứ từ ChromaDB để làm tài liệu tham khảo (RAG)."""
    try:
        # Mock / Hoặc kết nối ChromaDB thực tế
        # client = chromadb.PersistentClient(path="./data/chroma")
        # collection = client.get_or_create_collection("dq_rules_history")
        # query = f"table: {table_name}, column: {column_name}"
        # results = collection.query(query_texts=[query], n_results=top_k)
        
        # Mẫu dữ liệu trả về cho LLM tham khảo
        mock_results = [
            {"column": "fare_amount", "rule_type": "RANGE_CHECK", "min": 0, "max": 2000000},
            {"column": "driver_id", "rule_type": "NOT_NULL"}
        ]
        import json
        return json.dumps(mock_results, ensure_ascii=False)
    except Exception as e:
        return f"Lỗi khi query ChromaDB: {str(e)}"
```

---

### 3. Test Generator Agent Tools

#### 📌 Tool: `generate_dbt_test_code`
* **File:** [dbt_generator_tool.py](file:///d:/ai_thuc_chien/P-028/src/agents/tools/dbt_generator_tool.py)
* **Mô tả:** Render file `schema.yml` cấu hình dbt test từ danh sách các rule đã duyệt.
* **Đầu vào:**
  * `rules_json` (str): Chuỗi JSON danh sách rules đã được Data Steward phê duyệt.
  * `output_path` (str): Đường dẫn lưu trữ file YAML cấu hình dbt test.
* **Đầu ra:** Đường dẫn file YAML đã sinh thành công hoặc thông báo lỗi.
* **Gợi ý Mock Code:**
```python
import yaml
import json
from langchain_core.tools import tool

@tool
def generate_dbt_test_code(rules_json: str, output_path: str = "./dbt_project/models/schema.yml") -> str:
    """Tạo cấu hình dbt tests (YAML) dựa trên danh sách quy tắc đã được phê duyệt."""
    try:
        rules = json.loads(rules_json)
        
        # Cấu trúc cơ bản của dbt schema.yml
        dbt_config = {
            "version": 2,
            "models": [{
                "name": "dich_vu_xe_trips", # Cần lấy động tên bảng
                "columns": []
            }]
        }
        
        # Duyệt qua các rules để map sang dbt tests chuẩn
        columns_map = {}
        for r in rules:
            col = r["column"]
            rule_type = r["rule_type"]
            
            if col not in columns_map:
                columns_map[col] = {"name": col, "tests": []}
                
            if rule_type == "NOT_NULL":
                columns_map[col]["tests"].append("not_null")
            elif rule_type == "UNIQUE":
                columns_map[col]["tests"].append("unique")
            elif rule_type == "RANGE_CHECK":
                min_val = r["parameters"].get("min")
                max_val = r["parameters"].get("max")
                columns_map[col]["tests"].append({
                    "dbt_utils.accepted_range": {
                        "min_value": min_val,
                        "max_value": max_val
                    }
                })
        
        dbt_config["models"][0]["columns"] = list(columns_map.values())
        
        # Ghi file YAML
        with open(output_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(dbt_config, f, default_flow_style=False, sort_keys=False)
            
        return f"Đã sinh file cấu hình dbt tests tại: {output_path}"
    except Exception as e:
        return f"Lỗi sinh dbt code: {str(e)}"
```

#### 📌 Tool: `validate_dbt_syntax`
* **File:** [dbt_generator_tool.py](file:///d:/ai_thuc_chien/P-028/src/agents/tools/dbt_generator_tool.py)
* **Mô tả:** Chạy cú pháp lệnh parse hoặc dry-run dbt để đảm bảo cấu trúc YAML vừa ghi không bị lỗi cú pháp thụt lề hay sai tên cột.
* **Đầu vào:**
  * `project_dir` (str): Đường dẫn đến dbt project.
* **Đầu ra:** Kết quả xác thực (Thành công / Lỗi cú pháp kèm stacktrace chi tiết).

---

### 4. Test Runner & Anomaly Detection Tools

#### 📌 Tool: `run_dbt_test_pipeline`
* **File:** [dbt_generator_tool.py](file:///d:/ai_thuc_chien/P-028/src/agents/tools/dbt_generator_tool.py)
* **Mô tả:** Thực thi lệnh `dbt test` thông qua command line hoặc trigger API để bắt đầu kiểm tra dữ liệu thực tế.
* **Đầu vào:**
  * `select_model` (str): Tên model/bảng cụ thể muốn chạy kiểm tra.
* **Đầu ra:** JSON log chứa kết quả chi tiết từng testcase (Pass, Fail, Error, Warn).

#### 📌 Tool: `detect_anomalies_ml`
* **File:** [ml_anomaly_tool.py](file:///d:/ai_thuc_chien/P-028/src/agents/tools/ml_anomaly_tool.py)
* **Mô tả:** Chạy thuật toán Z-Score hoặc Isolation Forest trên tập dữ liệu chuỗi thời gian (ví dụ: tỉ lệ Null của cột `fare_amount` trong 30 ngày gần đây) để xác định xem ngày hôm nay có phải là điểm bất thường không.
* **Đầu vào:**
  * `historical_metrics_json` (str): Chuỗi JSON chứa dữ liệu lịch sử các ngày.
  * `threshold` (float): Ngưỡng độ lệch chuẩn (cho Z-score) hoặc contamination (cho Isolation Forest).
* **Đầu ra:** JSON chỉ rõ điểm bất thường (nếu có).
* **Gợi ý Mock Code:**
```python
import numpy as np
import json
from langchain_core.tools import tool

@tool
def detect_anomalies_ml(historical_metrics_json: str, threshold: float = 3.0) -> str:
    """Sử dụng thuật toán thống kê Z-Score phát hiện giá trị DQ bất thường từ chuỗi thời gian lịch sử."""
    try:
        data = json.loads(historical_metrics_json)
        values = [d["value"] for d in data]
        dates = [d["date"] for d in data]
        
        if len(values) < 5:
            return json.dumps({"warning": "Không đủ dữ liệu lịch sử (cần tối thiểu 5 điểm).", "anomaly_detected": False})
            
        mean = np.mean(values)
        std = np.std(values)
        
        if std == 0:
            std = 1e-9 # Tránh chia cho 0
            
        latest_val = values[-1]
        z_score = abs((latest_val - mean) / std)
        
        anomaly_detected = z_score > threshold
        
        result = {
            "latest_value": latest_val,
            "mean": round(float(mean), 4),
            "std": round(float(std), 4),
            "z_score": round(float(z_score), 2),
            "anomaly_detected": bool(anomaly_detected),
            "reason": f"Giá trị mới nhất {latest_val} lệch {z_score:.2f} lần độ lệch chuẩn (Ngưỡng: {threshold})" if anomaly_detected else "Bình thường"
        }
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"Lỗi chạy ML Anomaly: {str(e)}"})
```

---

### 5. Diagnostic & Alerting Tools

#### 📌 Tool: `send_slack_alert`
* **File:** [alert_tool.py](file:///d:/ai_thuc_chien/P-028/src/agents/tools/alert_tool.py)
* **Mô tả:** Định dạng thông điệp cảnh báo bất thường dưới dạng block kit và đẩy qua Webhook tới Slack/Email.
* **Đầu vào:**
  * `webhook_url` (str): Địa chỉ webhook của kênh Slack nhận thông báo.
  * `severity` (str): Mức độ khẩn cấp (CRITICAL, WARNING, INFO).
  * `message` (str): Nội dung cảnh báo.
  * `diagnosis` (str): Lý giải nguyên nhân gốc rễ và đề xuất cách sửa (SQL script).
* **Đầu ra:** Trạng thái gửi thành công hoặc lỗi.
