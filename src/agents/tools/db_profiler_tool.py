import json
import logging
from datetime import UTC, date, datetime

from langchain_core.tools import tool
from sqlalchemy import create_engine, inspect, text, types

logger = logging.getLogger(__name__)


def _parse_and_calculate_freshness(max_time_val):
    """Chuẩn hóa giá trị thời gian (str/datetime/date) về ISO string và tính khoảng cách
    (giây) tới thời điểm hiện tại để phục vụ đánh giá 'freshness' của dữ liệu.
    """
    if max_time_val is None:
        return None, None

    if isinstance(max_time_val, str):
        try:
            parsed_dt = datetime.fromisoformat(max_time_val.replace("Z", "+00:00"))
        except ValueError:
            for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                try:
                    parsed_dt = datetime.strptime(max_time_val, fmt)
                    break
                except ValueError:
                    continue
            else:
                return str(max_time_val), None
    elif isinstance(max_time_val, datetime):
        parsed_dt = max_time_val
    elif isinstance(max_time_val, date):
        parsed_dt = datetime(max_time_val.year, max_time_val.month, max_time_val.day)
    else:
        return str(max_time_val), None

    now = datetime.now(UTC) if parsed_dt.tzinfo else datetime.now()
    gap_seconds = (now - parsed_dt).total_seconds()
    return parsed_dt.isoformat(), round(gap_seconds, 2)


def _classify_columns(columns):
    """Phân loại từng cột theo type để quyết định các phép aggregate phù hợp."""
    col_meta = []
    for col in columns:
        col_name = col["name"]
        col_type = col["type"]
        type_name = str(col_type).upper()

        is_numeric = isinstance(col_type, (types.Integer, types.Numeric, types.Float, types.DECIMAL))
        is_text = isinstance(col_type, (types.String, types.Text)) or any(
            kw in type_name for kw in ["TEXT", "CHAR", "VARCHAR", "STRING"]
        )
        is_datetime = isinstance(col_type, (types.DateTime, types.Date, types.TIMESTAMP, types.Time)) or any(
            kw in type_name for kw in ["DATE", "TIME", "TIMESTAMP", "DATETIME"]
        )
        is_complex = any(kw in type_name for kw in ["JSON", "BLOB", "BYTEA", "ARRAY", "STRUCT"])

        col_meta.append(
            {
                "name": col_name,
                "type": str(col_type),
                "is_numeric": is_numeric,
                "is_text": is_text,
                "is_datetime": is_datetime,
                "is_complex": is_complex,
            }
        )
    return col_meta


def _get_schema_constraints(inspector, table_name: str) -> dict:
    """Lấy PK / FK / Unique constraints từ inspector.
    Trả về dict an toàn kể cả khi DB không hỗ trợ (SQLite, permission thiếu...).
    """
    result = {"primary_key": [], "foreign_keys": [], "unique_constraints": []}

    try:
        pk = inspector.get_pk_constraint(table_name)
        result["primary_key"] = pk.get("constrained_columns", []) if pk else []
    except Exception as e:
        logger.debug("Không lấy được PK constraint của bảng '%s': %s", table_name, e)

    try:
        fks = inspector.get_foreign_keys(table_name)
        result["foreign_keys"] = [
            {
                "constrained_columns": fk.get("constrained_columns", []),
                "referred_table": fk.get("referred_table"),
                "referred_columns": fk.get("referred_columns", []),
            }
            for fk in (fks or [])
        ]
    except Exception as e:
        logger.debug("Không lấy được FK constraints của bảng '%s': %s", table_name, e)

    try:
        ucs = inspector.get_unique_constraints(table_name)
        result["unique_constraints"] = [
            {"name": uc.get("name"), "columns": uc.get("column_names", [])} for uc in (ucs or [])
        ]
    except Exception as e:
        logger.debug("Không lấy được unique constraints của bảng '%s': %s", table_name, e)

    return result


def _is_key_candidate(col_name: str, col_meta: dict, distinct_in_sample: int, sampled_rows: int) -> bool:
    """Xác định xem cột có phải ứng viên khóa cần full-table distinct hay không.

    Điều kiện:
    - Tên kết thúc bằng '_id' hoặc đúng là 'id', HOẶC
    - distinct_in_sample >= 95% sampled_rows (mẫu gần như toàn là unique)

    Không áp dụng cho cột datetime hoặc complex.
    """
    if col_meta.get("is_datetime") or col_meta.get("is_complex"):
        return False
    name_lower = col_name.lower()
    if name_lower == "id" or name_lower.endswith("_id"):
        return True
    if sampled_rows > 0 and distinct_in_sample >= sampled_rows * 0.95:
        return True
    return False


def _compute_sqlite_percentiles_sql(col_name: str, subquery: str, sampled_rows: int) -> dict | None:
    """Tính approximate percentiles trên SQLite bằng cách ORDER BY + OFFSET.
    Trả về dict {p1, p5, p25, p50, p75, p95, p99} hoặc None nếu không đủ dữ liệu.
    Caller phải execute từng query riêng do SQLite không hỗ trợ native percentile_cont.
    """
    if sampled_rows <= 0:
        return None
    # Trả về dict {percentile_label: offset_index} để caller execute
    n = sampled_rows
    return {
        "p1": max(0, int(n * 0.01) - 1),
        "p5": max(0, int(n * 0.05) - 1),
        "p25": max(0, int(n * 0.25) - 1),
        "p50": max(0, int(n * 0.50) - 1),
        "p75": max(0, int(n * 0.75) - 1),
        "p95": max(0, int(n * 0.95) - 1),
        "p99": max(0, int(n * 0.99) - 1),
    }


def _detect_datetime_pairs(datetime_cols: list[str]) -> list[tuple[str, str]]:
    """Phát hiện heuristic các cặp cột datetime có quan hệ thứ tự (start < end).

    Chỉ xét bảng có <= 6 cột datetime để tránh tổ hợp bùng nổ.
    Heuristic: tìm cặp có prefix giống nhau hoặc suffix khớp pattern known.
    """
    if len(datetime_cols) > 6:
        logger.debug("Bỏ qua cross-column datetime check: có %d > 6 cột datetime", len(datetime_cols))
        return []

    # Các cặp suffix ngầm hiểu start < end
    ordered_pairs = [
        ("pickup", "dropoff"),
        ("start", "end"),
        ("created", "updated"),
        ("begin", "end"),
        ("open", "close"),
        ("departure", "arrival"),
        ("tpep_pickup", "tpep_dropoff"),
        ("lpep_pickup", "lpep_dropoff"),
    ]

    pairs: list[tuple[str, str]] = []
    cols_lower = {c.lower(): c for c in datetime_cols}

    # Exact-suffix matching
    for prefix_a, prefix_b in ordered_pairs:
        matched_a = [orig for low, orig in cols_lower.items() if prefix_a in low]
        matched_b = [orig for low, orig in cols_lower.items() if prefix_b in low]
        for a in matched_a:
            for b in matched_b:
                if a != b and (a, b) not in pairs:
                    pairs.append((a, b))

    # Fallback: nếu không tìm được cặp nào và có 2 cột datetime → check O(n²) thông thường
    if not pairs and len(datetime_cols) == 2:
        pairs.append((datetime_cols[0], datetime_cols[1]))

    return pairs


@tool
def profile_database(
    connection_string: str,
    table_name: str,
    sampling_rate: float = 1.0,
    compute_percentiles: bool = True,
) -> str:
    """Quét metadata và tính toán thống kê (row count, null rate, min, max, distinct, quantiles,
    categories, freshness, schema constraints, percentiles, negative/zero pct, length stats,
    cross-column hints) của một bảng.

    Chiến lược 2 pha:
    - Pha 1 (LUÔN chạy trên full table, không materialize): null_count, min/max/avg cho numeric,
      negative_count/zero_count cho numeric, length stats cho text, min/max/freshness cho datetime.
      Đây là các phép SUM/MIN/MAX/AVG rẻ, gộp trong 1 câu SQL duy nhất.
    - Pha 2 (chỉ sample khi sampling_rate < 1.0): distinct count và top_categories, vì đây là
      phép tốn kém hơn (COUNT DISTINCT, GROUP BY) và chấp nhận ước lượng gần đúng được.
      Khi sampling_rate = 1.0, KHÔNG tạo temp table, query thẳng trên bảng gốc.
    - Pha 3 (targeted, sau Pha 2): full-table COUNT(DISTINCT) cho key candidate columns;
      percentiles cho numeric; cross-column datetime order check.

    Args:
        connection_string: Chuỗi kết nối database (PostgreSQL hoặc SQLite)
        table_name: Tên bảng cần profile
        sampling_rate: Tỷ lệ lấy mẫu (từ 0.0 đến 1.0), chỉ áp dụng cho distinct/categorical stats
        compute_percentiles: Bật/tắt tính percentile cho cột numeric (mặc định True)

    Returns:
        Chuỗi JSON chứa thông tin schema và các chỉ số thống kê của bảng, bao gồm
        schema_constraints, cross_column_hints và các field mới theo plan cải tiến.
    """
    if table_name.lower().split(".")[-1].strip('"') == "source_rows":
        return json.dumps({"error": "SOURCE_SCOPE_REQUIRED: Shared source_rows requires dataset/version isolation; use the verified versioned profiler"})
    engine = None
    temp_table_created = False
    temp_table = f"_sample_{table_name}"

    try:
        is_postgres = "postgresql" in connection_string
        is_sqlite = "sqlite" in connection_string

        if connection_string.startswith("postgresql://"):
            connection_string = connection_string.replace("postgresql://", "postgresql+psycopg2://", 1)

        engine = create_engine(connection_string)
        with engine.connect() as conn:
            # Bật FK cho SQLite (an toàn, không ảnh hưởng nếu không có FK)
            if is_sqlite:
                try:
                    conn.execute(text("PRAGMA foreign_keys=ON"))
                except Exception:
                    pass

            inspector = inspect(engine)

            if not inspector.has_table(table_name):
                return json.dumps({"error": f"Bảng '{table_name}' không tồn tại trong database."}, ensure_ascii=False)

            columns = inspector.get_columns(table_name)
            col_meta = _classify_columns(columns)

            # ===================== P0.1: Schema constraints =====================
            schema_constraints = _get_schema_constraints(inspector, table_name)

            count_res = conn.execute(text(f'SELECT COUNT(*) FROM "{table_name}"')).scalar()
            total_rows = count_res if count_res is not None else 0

            if total_rows == 0:
                return json.dumps(
                    {
                        "table_metadata": {
                            "table_name": table_name,
                            "total_rows": 0,
                            "sampling_rate": sampling_rate,
                            "sampled_rows": 0,
                            "is_sampled": False,
                            "timestamp": datetime.now(UTC).isoformat(),
                        },
                        "schema_constraints": schema_constraints,
                        "columns": {},
                        "cross_column_hints": [],
                        "warning": "Bảng không có dữ liệu.",
                    },
                    ensure_ascii=False,
                )

            try:
                # ===================== PHA 1: full-table, không materialize =====================
                # Tất cả aggregate: null_count, min/max/avg (numeric), negative/zero (numeric),
                # length (text), min/max (datetime) — gộp 1 câu SQL duy nhất.
                full_select = []
                for col in col_meta:
                    col_name = col["name"]
                    # Null count — tất cả cột
                    full_select.append(f'SUM(CASE WHEN "{col_name}" IS NULL THEN 1 ELSE 0 END) AS "{col_name}_nulls"')
                    if col["is_numeric"]:
                        full_select.append(f'MIN("{col_name}") AS "{col_name}_min"')
                        full_select.append(f'MAX("{col_name}") AS "{col_name}_max"')
                        full_select.append(f'AVG("{col_name}") AS "{col_name}_avg"')
                        # P1.2: negative / zero counts — gộp cùng full_sql
                        full_select.append(
                            f'SUM(CASE WHEN "{col_name}" < 0 THEN 1 ELSE 0 END) AS "{col_name}_negative_count"'
                        )
                        full_select.append(
                            f'SUM(CASE WHEN "{col_name}" = 0 THEN 1 ELSE 0 END) AS "{col_name}_zero_count"'
                        )
                    if col["is_datetime"]:
                        full_select.append(f'MIN("{col_name}") AS "{col_name}_min"')
                        full_select.append(f'MAX("{col_name}") AS "{col_name}_max"')
                    # P1.3: length stats cho text — gộp cùng full_sql
                    if col["is_text"]:
                        full_select.append(f'MIN(LENGTH("{col_name}")) AS "{col_name}_len_min"')
                        full_select.append(f'MAX(LENGTH("{col_name}")) AS "{col_name}_len_max"')
                        full_select.append(f'AVG(LENGTH("{col_name}")) AS "{col_name}_len_avg"')

                full_sql = f'SELECT {", ".join(full_select)} FROM "{table_name}"'
                full_res = conn.execute(text(full_sql)).mappings().first()

                # ===================== PHA 2: distinct/categorical, sample nếu cần =====================
                is_sampled = sampling_rate < 1.0
                current_sampling_rate = sampling_rate
                subquery = f'"{table_name}"'
                sampled_rows = total_rows

                if is_sampled:
                    if is_postgres:
                        from_clause = f'"{table_name}" TABLESAMPLE SYSTEM ({current_sampling_rate * 100})'
                    elif is_sqlite:
                        from_clause = f'"{table_name}" WHERE abs(random() % 100) < {current_sampling_rate * 100}'
                    else:
                        from_clause = f'"{table_name}" WHERE random() < {current_sampling_rate}'

                    conn.execute(text(f'DROP TABLE IF EXISTS "{temp_table}"'))
                    conn.execute(text(f'CREATE TEMP TABLE "{temp_table}" AS SELECT * FROM {from_clause}'))
                    temp_table_created = True

                    sampled_rows = conn.execute(text(f'SELECT COUNT(*) FROM "{temp_table}"')).scalar() or 0

                    if sampled_rows < min(total_rows, 10):
                        # Fallback: mẫu quá nhỏ → dùng full table
                        conn.execute(text(f'DROP TABLE IF EXISTS "{temp_table}"'))
                        temp_table_created = False
                        is_sampled = False
                        current_sampling_rate = 1.0
                        sampled_rows = total_rows
                        subquery = f'"{table_name}"'
                    else:
                        subquery = f'"{temp_table}"'

                distinct_select = [
                    f'COUNT(DISTINCT "{col["name"]}") AS "{col["name"]}_distinct"'
                    for col in col_meta
                    if not col["is_complex"]
                ]
                distinct_res = None
                if distinct_select:
                    distinct_sql = f"SELECT {', '.join(distinct_select)} FROM {subquery}"
                    distinct_res = conn.execute(text(distinct_sql)).mappings().first()

                # ===================== PHA 3: Full-table distinct cho key candidates =====================
                # Xác định key candidates sau khi đã có distinct_in_sample từ Pha 2
                key_candidate_cols = []
                for col in col_meta:
                    col_name = col["name"]
                    if col["is_complex"]:
                        continue
                    dist_sample = int(distinct_res.get(f"{col_name}_distinct", 0)) if distinct_res else 0
                    if _is_key_candidate(col_name, col, dist_sample, sampled_rows):
                        key_candidate_cols.append(col_name)

                logger.debug(
                    "Key candidate columns cho full-distinct: %s / %d tổng cột",
                    key_candidate_cols,
                    len(col_meta),
                )

                full_distinct_res: dict[str, int] = {}
                if key_candidate_cols:
                    fd_select = [f'COUNT(DISTINCT "{c}") AS "{c}_full_distinct"' for c in key_candidate_cols]
                    fd_sql = f'SELECT {", ".join(fd_select)} FROM "{table_name}"'
                    fd_row = conn.execute(text(fd_sql)).mappings().first()
                    if fd_row:
                        for c in key_candidate_cols:
                            v = fd_row.get(f"{c}_full_distinct")
                            full_distinct_res[c] = int(v) if v is not None else 0

                # ===================== P1.1: Percentile cho numeric =====================
                percentile_res: dict[str, dict] = {}
                if compute_percentiles:
                    numeric_cols = [col["name"] for col in col_meta if col["is_numeric"]]

                    if is_postgres and numeric_cols:
                        # Postgres: percentile_cont native, chạy trên full table
                        pct_parts = []
                        for col_name in numeric_cols:
                            pct_parts.append(
                                f"percentile_cont(ARRAY[0.01,0.05,0.25,0.5,0.75,0.95,0.99]) "
                                f'WITHIN GROUP (ORDER BY "{col_name}") AS "{col_name}_pcts"'
                            )
                        pct_sql = f'SELECT {", ".join(pct_parts)} FROM "{table_name}"'
                        try:
                            pct_row = conn.execute(text(pct_sql)).mappings().first()
                            if pct_row:
                                for col_name in numeric_cols:
                                    arr = pct_row.get(f"{col_name}_pcts")
                                    if arr is not None:
                                        labels = ["p1", "p5", "p25", "p50", "p75", "p95", "p99"]
                                        percentile_res[col_name] = {
                                            "values": {
                                                lbl: float(v) if v is not None else None for lbl, v in zip(labels, arr)
                                            },
                                            "is_estimate": False,
                                        }
                        except Exception as e:
                            logger.warning("Không tính được percentile Postgres: %s", e)

                    elif is_sqlite and numeric_cols and sampled_rows > 0:
                        # SQLite: approximate percentile trên sample, từng cột
                        labels = ["p1", "p5", "p25", "p50", "p75", "p95", "p99"]
                        offsets_map = _compute_sqlite_percentiles_sql("", subquery, sampled_rows)
                        if offsets_map:
                            for col_name in numeric_cols:
                                pct_values: dict[str, float | None] = {}
                                try:
                                    for lbl, offset in offsets_map.items():
                                        row = conn.execute(
                                            text(
                                                f'SELECT "{col_name}" FROM {subquery} '
                                                f'WHERE "{col_name}" IS NOT NULL '
                                                f'ORDER BY "{col_name}" '
                                                f"LIMIT 1 OFFSET {offset}"
                                            )
                                        ).scalar()
                                        pct_values[lbl] = float(row) if row is not None else None
                                    percentile_res[col_name] = {
                                        "values": pct_values,
                                        "is_estimate": True,
                                    }
                                except Exception as e:
                                    logger.debug("Không tính được percentile SQLite cho cột '%s': %s", col_name, e)

                # ===================== P2.1: Cross-column datetime hints =====================
                datetime_cols = [col["name"] for col in col_meta if col["is_datetime"]]
                cross_column_hints: list[dict] = []
                if len(datetime_cols) >= 2:
                    dt_pairs = _detect_datetime_pairs(datetime_cols)
                    for col_a, col_b in dt_pairs:
                        try:
                            violation_sql = (
                                f'SELECT SUM(CASE WHEN "{col_a}" > "{col_b}" THEN 1 ELSE 0 END) AS violations, '
                                f"COUNT(*) AS total "
                                f'FROM "{table_name}" '
                                f'WHERE "{col_a}" IS NOT NULL AND "{col_b}" IS NOT NULL'
                            )
                            vrow = conn.execute(text(violation_sql)).mappings().first()
                            if vrow:
                                violations = int(vrow["violations"] or 0)
                                total_checked = int(vrow["total"] or 0)
                                violation_pct = round(violations / total_checked, 6) if total_checked > 0 else 0.0
                                cross_column_hints.append(
                                    {
                                        "type": "datetime_order",
                                        "columns": [col_a, col_b],
                                        "description": f'Kỳ vọng "{col_a}" <= "{col_b}"',
                                        "violation_count": violations,
                                        "total_checked": total_checked,
                                        "violation_pct": violation_pct,
                                    }
                                )
                        except Exception as e:
                            logger.debug("Không tính được cross-column hint (%s, %s): %s", col_a, col_b, e)

                # ===================== Tổng hợp kết quả =====================
                result_columns = {}
                for col in col_meta:
                    col_name = col["name"]

                    # null_count/null_pct luôn từ full table (pha 1)
                    null_count = full_res.get(f"{col_name}_nulls") if full_res else None
                    null_count = int(null_count) if null_count is not None else 0
                    null_pct = null_count / total_rows if total_rows > 0 else 0.0

                    distinct_in_sample = (
                        int(distinct_res.get(f"{col_name}_distinct", 0))
                        if distinct_res and not col["is_complex"]
                        else 0
                    )

                    # Phân tích Categorical (text <= 50, numeric <= 10)
                    is_categorical = False
                    if not col["is_complex"] and not col["is_datetime"]:
                        if col["is_text"] and distinct_in_sample <= 50:
                            is_categorical = True
                        elif col["is_numeric"] and distinct_in_sample <= 10:
                            is_categorical = True

                    col_stats: dict = {
                        "type": col["type"],
                        "null_count": null_count,
                        "null_pct": round(null_pct, 4),
                        "distinct_in_sample": distinct_in_sample,
                        "distinct_is_estimate": is_sampled,
                        "is_categorical": is_categorical,
                    }

                    # P0.3: Full-distinct / unique flag cho key candidates
                    if col_name in full_distinct_res:
                        fd_count = full_distinct_res[col_name]
                        col_stats["distinct_full_table"] = fd_count
                        col_stats["is_unique_full_table"] = fd_count == total_rows and total_rows > 0

                    # min/max/mean cho numeric: từ full table (pha 1)
                    if col["is_numeric"] and full_res:
                        min_val = full_res.get(f"{col_name}_min")
                        max_val = full_res.get(f"{col_name}_max")
                        avg_val = full_res.get(f"{col_name}_avg")
                        col_stats["min"] = float(min_val) if min_val is not None else None
                        col_stats["max"] = float(max_val) if max_val is not None else None
                        col_stats["mean"] = float(avg_val) if avg_val is not None else None

                        # P1.2: negative / zero pct — từ full table (pha 1)
                        neg_count = full_res.get(f"{col_name}_negative_count")
                        zero_count = full_res.get(f"{col_name}_zero_count")
                        neg_count = int(neg_count) if neg_count is not None else 0
                        zero_count = int(zero_count) if zero_count is not None else 0
                        col_stats["negative_count"] = neg_count
                        col_stats["negative_pct"] = round(neg_count / total_rows, 6) if total_rows > 0 else 0.0
                        col_stats["zero_count"] = zero_count
                        col_stats["zero_pct"] = round(zero_count / total_rows, 6) if total_rows > 0 else 0.0

                        # P1.1: Percentile
                        if col_name in percentile_res:
                            pct_data = percentile_res[col_name]
                            col_stats["percentiles"] = pct_data["values"]
                            col_stats["percentiles_is_estimate"] = pct_data["is_estimate"]

                    # min/max/freshness cho datetime: từ full table (pha 1)
                    if col["is_datetime"] and full_res:
                        min_val = full_res.get(f"{col_name}_min")
                        max_val = full_res.get(f"{col_name}_max")

                        min_iso, _ = _parse_and_calculate_freshness(min_val)
                        max_iso, gap_seconds = _parse_and_calculate_freshness(max_val)

                        col_stats["min"] = min_iso
                        col_stats["max"] = max_iso
                        col_stats["freshness_gap_seconds"] = gap_seconds

                    # P1.3: Length stats cho text — từ full table (pha 1)
                    if col["is_text"] and full_res:
                        len_min = full_res.get(f"{col_name}_len_min")
                        len_max = full_res.get(f"{col_name}_len_max")
                        len_avg = full_res.get(f"{col_name}_len_avg")
                        col_stats["length_stats"] = {
                            "min": int(len_min) if len_min is not None else None,
                            "max": int(len_max) if len_max is not None else None,
                            "avg": round(float(len_avg), 2) if len_avg is not None else None,
                        }

                    # P2.2: Dynamic top_categories limit (20 nếu cardinality thấp, 5 nếu cao)
                    if is_categorical and sampled_rows > 0:
                        cat_limit = 20 if distinct_in_sample <= 20 else 5
                        cat_query = (
                            f'SELECT "{col_name}" AS val, COUNT(*) AS cnt FROM {subquery} '
                            f'WHERE "{col_name}" IS NOT NULL GROUP BY "{col_name}" ORDER BY cnt DESC LIMIT {cat_limit}'
                        )
                        cat_rows = conn.execute(text(cat_query)).mappings().all()
                        top_categories = []
                        for row in cat_rows:
                            val = row["val"]
                            cnt = int(row["cnt"])
                            rate = round(cnt / sampled_rows, 4)
                            top_categories.append(
                                {"value": str(val) if val is not None else None, "count": cnt, "rate": rate}
                            )
                        col_stats["top_categories"] = top_categories

                    result_columns[col_name] = col_stats

                profile = {
                    "table_metadata": {
                        "table_name": table_name,
                        "total_rows": total_rows,
                        "sampling_rate": current_sampling_rate,
                        "sampled_rows": sampled_rows,
                        "is_sampled": is_sampled,
                        "timestamp": datetime.now(UTC).isoformat(),
                    },
                    # P0.1: schema constraints ở cấp bảng
                    "schema_constraints": schema_constraints,
                    "columns": result_columns,
                    # P2.1: cross-column hints ở cấp bảng
                    "cross_column_hints": cross_column_hints,
                }
                return json.dumps(profile, ensure_ascii=False, indent=2)
            finally:
                if temp_table_created:
                    try:
                        conn.execute(text(f'DROP TABLE IF EXISTS "{temp_table}"'))
                    except Exception:
                        pass

    except Exception as e:
        logger.error(f"Lỗi khi profile bảng {table_name}: {str(e)}", exc_info=True)
        return json.dumps({"error": f"Lỗi thực thi profiling: {str(e)}"}, ensure_ascii=False)
    finally:
        if engine is not None:
            engine.dispose()
