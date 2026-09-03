import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def generate_profile_digest(dataset_profile: dict) -> dict:
    """Biến đổi profile thô của database thành dạng tóm tắt digest rút gọn để hướng-rule DQ.

    Các field mới được thêm theo plan cải tiến (không xóa/đổi tên field cũ):
    - P0.1: `schema_constraints` ở cấp bảng; signal `has_pk_constraint`, `has_unique_constraint`;
            field `references` cho cột FK.
    - P0.2: `unique_in_sample` chỉ gắn khi is_sampled=False; khi is_sampled=True đổi thành
            `unique_in_sample_only` (cảnh báo không đủ bằng chứng unique).
    - P0.3: signal `unique_full_table` khi `is_unique_full_table=True`.
    - P1.1: field `percentiles` và `typical_range` ([p5, p95]); signal `has_extreme_outliers`.
    - P1.2: field `negative_pct`, `zero_pct` khi > 0; signal `has_negative_values`,
            `has_zero_values` (khi zero_pct > 5%).
    - P1.3: field `length_stats` cho freetext/categorical text; signal `fixed_length`.
    - P2.1: `cross_column_hints` ở cấp bảng (truyền thẳng từ raw profile).
    - P2.2: `values` tự động reflect dynamic limit từ raw profile (không cần sửa logic).

    Args:
        dataset_profile: Dictionary chứa profile thô của tất cả các bảng.

    Returns:
        Dictionary chứa digest của tất cả các bảng.
    """
    if dataset_profile is None:
        return {}

    # Tự động giải bọc nếu đầu vào chứa key "dataset_profile" ở mức cao nhất
    if "dataset_profile" in dataset_profile:
        dataset_profile = dataset_profile["dataset_profile"]

    # Tự động bọc thành dictionary nếu đầu vào là profile của 1 bảng duy nhất
    if "table_metadata" in dataset_profile and "columns" in dataset_profile:
        t_name = dataset_profile.get("table_metadata", {}).get("table_name", "unknown_table")
        dataset_profile = {t_name: dataset_profile}

    digest = {}
    for table_name, table_data in dataset_profile.items():
        if "error" in table_data:
            digest[table_name] = {
                "table": table_name,
                "error": table_data["error"],
            }
            continue

        meta = table_data.get("table_metadata", {})
        total_rows = meta.get("total_rows", 0)
        sampled_rows = meta.get("sampled_rows", 0)
        sampling_rate = meta.get("sampling_rate", 1.0)
        is_sampled = meta.get("is_sampled", False)

        sample_info = {"rate": sampling_rate, "n": sampled_rows}
        if is_sampled:
            sample_info["caveat"] = (
                "distinct_in_sample và top_categories là ước lượng từ mẫu; "
                "null_count/min/max/freshness là số liệu full-table"
            )

        # ===================== P0.1: Schema constraints ở cấp bảng =====================
        raw_constraints = table_data.get("schema_constraints", {})
        pk_columns: set[str] = set(raw_constraints.get("primary_key", []))

        # Build set unique-constraint columns
        unique_constraint_columns: set[str] = set()
        for uc in raw_constraints.get("unique_constraints", []):
            for col in uc.get("columns", []):
                unique_constraint_columns.add(col)

        # Build FK lookup: {constrained_col: {table, column}}
        fk_lookup: dict[str, dict] = {}
        for fk in raw_constraints.get("foreign_keys", []):
            constrained = fk.get("constrained_columns", [])
            referred_table = fk.get("referred_table")
            referred_columns = fk.get("referred_columns", [])
            for i, col in enumerate(constrained):
                ref_col = referred_columns[i] if i < len(referred_columns) else None
                fk_lookup[col] = {"table": referred_table, "column": ref_col}

        # Digest-level schema_constraints (giữ đủ để LLM đọc)
        schema_constraints_digest = {
            "primary_key": list(pk_columns),
            "unique_constraint_columns": list(unique_constraint_columns),
            "foreign_keys": raw_constraints.get("foreign_keys", []),
        }

        # ===================== P2.1: Cross-column hints ở cấp bảng =====================
        cross_column_hints = table_data.get("cross_column_hints", [])

        # ===================== Per-column digest =====================
        columns_digest = []
        raw_columns = table_data.get("columns", {})

        for col_name, col_data in raw_columns.items():
            type_str = str(col_data.get("type", "UNKNOWN"))
            distinct_in_sample = col_data.get("distinct_in_sample", 0)

            has_min_max = col_data.get("min") is not None or col_data.get("max") is not None

            type_upper = type_str.upper()
            is_numeric_type = (
                any(kw in type_upper for kw in ["INT", "REAL", "FLOAT", "DOUBLE", "DECIMAL", "NUMERIC", "NUMBER"])
                or has_min_max
            )
            is_text_type = any(kw in type_upper for kw in ["TEXT", "CHAR", "VARCHAR", "STRING"])

            # Infer role
            # Thứ tự ưu tiên:
            # 1. datetime → luôn đúng theo type
            # 2. is_categorical (từ raw profile) → profiler đã đo đạc, đáng tin hơn heuristic tên
            # 3. _id name heuristic → chỉ áp dụng khi KHÔNG categorical (cardinality cao / unique-like)
            # 4. numeric / freetext / generic
            if any(kw in type_upper for kw in ["DATE", "TIME", "TIMESTAMP", "DATETIME"]):
                role = "datetime"
            elif col_data.get("is_categorical", False):
                # Profiler đã xác định cardinality thấp → categorical, kể cả khi tên kết thúc _id
                # (vd vendor_id có 3 giá trị, rate_code_id có ~5 giá trị)
                role = "categorical"
            elif (
                col_name.lower() == "id"
                or col_name.lower().endswith("_id")
                or (
                    distinct_in_sample == sampled_rows
                    and sampled_rows > 0
                    and not any(kw in type_upper for kw in ["REAL", "FLOAT", "DOUBLE", "DECIMAL"])
                )
            ):
                role = "id"
            elif is_numeric_type:
                role = "numeric"
            elif is_text_type:
                role = "freetext"
            else:
                role = "generic"

            null_rate = col_data.get("null_pct", col_data.get("null_pct_sampled"))
            null_count = col_data.get("null_count")
            # This is model evidence, not a display label. Rounding rare nulls
            # to zero made the model infer mandatory fields from false evidence.
            null_pct = null_rate * 100 if null_rate is not None else None

            col_digest: dict = {
                "name": col_name,
                "type": type_str,
                "role": role,
                "null_pct": null_pct,
            }

            # P0.1: FK reference
            if col_name in fk_lookup:
                col_digest["references"] = fk_lookup[col_name]

            # Add values for categorical columns (P2.2: dynamic limit đã được thực hiện ở raw profile)
            if role == "categorical":
                col_digest["values"] = [cat.get("value") for cat in col_data.get("top_categories", [])]

            # Add range for numeric and datetime columns
            if role in ["numeric", "datetime"] and has_min_max:
                col_digest["range"] = [col_data.get("min"), col_data.get("max")]

            # P1.1: Percentile → typical_range và percentiles trong digest
            if role == "numeric":
                percentiles = col_data.get("percentiles")
                if percentiles:
                    pct_is_estimate = col_data.get("percentiles_is_estimate", False)
                    col_digest["percentiles"] = percentiles
                    if pct_is_estimate:
                        col_digest["percentiles_is_estimate"] = True
                    p5 = percentiles.get("p5")
                    p95 = percentiles.get("p95")
                    if p5 is not None and p95 is not None:
                        col_digest["typical_range"] = [p5, p95]

            # P1.2: negative_pct / zero_pct — chỉ đưa vào khi > 0
            if role == "numeric":
                neg_pct = col_data.get("negative_pct", 0.0)
                zero_pct = col_data.get("zero_pct", 0.0)
                if neg_pct and neg_pct > 0:
                    col_digest["negative_pct"] = round(neg_pct * 100, 4)
                if zero_pct and zero_pct > 0:
                    col_digest["zero_pct"] = round(zero_pct * 100, 4)

            # P1.3: length_stats cho freetext và categorical text
            if role in ["freetext", "categorical"] and is_text_type:
                length_stats = col_data.get("length_stats")
                if length_stats:
                    col_digest["length_stats"] = length_stats

            # ===================== DQ Signals =====================
            signals: list[str] = []

            # Null signals
            if (null_count == 0 or null_rate == 0) and not (
                (null_count is not None and null_count > 0)
                or (null_rate is not None and null_rate > 0)
            ):
                signals.append("no_nulls")
            elif null_pct is not None and null_pct > 80.0:
                signals.append("mostly_null")

            # Cardinality signal
            if distinct_in_sample <= 10 and distinct_in_sample > 0:
                signals.append("low_cardinality")

            # P0.3: Unique full-table (đáng tin 100%)
            if col_data.get("is_unique_full_table"):
                signals.append("unique_full_table")
            # P0.2: unique_in_sample chỉ đáng tin khi KHÔNG sample
            elif distinct_in_sample == sampled_rows and sampled_rows > 0:
                if not is_sampled:
                    signals.append("unique_in_sample")
                else:
                    # Khi sample: chỉ là gợi ý, KHÔNG đủ bằng chứng cho rule uniqueness cứng
                    signals.append("unique_in_sample_only")

            # P0.1: PK / unique constraint signals
            if col_name in pk_columns:
                signals.append("has_pk_constraint")
            if col_name in unique_constraint_columns:
                signals.append("has_unique_constraint")

            # P1.1: Extreme outlier signal
            if role == "numeric":
                percentiles = col_data.get("percentiles")
                col_min = col_data.get("min")
                col_max = col_data.get("max")
                if percentiles and col_min is not None and col_max is not None:
                    p1 = percentiles.get("p1")
                    p99 = percentiles.get("p99")
                    # Outlier: min << p1 (dưới ngưỡng 1%) hoặc max >> p99 (trên ngưỡng 99%)
                    # Dùng so sánh tuyệt đối và tương đối: chênh lệch > 50% giá trị p1/p99
                    has_outlier = False
                    if p1 is not None and p1 != 0:
                        # min xa hơn 3x khoảng cách p1 tính từ 0
                        if col_min < p1 - 2 * abs(p1):
                            has_outlier = True
                    elif p1 is not None and p1 == 0 and col_min < 0:
                        has_outlier = True
                    if p99 is not None and p99 != 0:
                        if col_max > p99 + 2 * abs(p99):
                            has_outlier = True
                    elif p99 is not None and p99 == 0 and col_max > 0:
                        has_outlier = True
                    if has_outlier:
                        signals.append("has_extreme_outliers")

            # P1.2: Negative / zero value signals
            if role == "numeric":
                neg_pct = col_data.get("negative_pct", 0.0)
                zero_pct = col_data.get("zero_pct", 0.0)
                if neg_pct and neg_pct > 0:
                    signals.append("has_negative_values")
                # Cảnh báo khi > 5% giá trị là 0 (đáng để LLM chú ý)
                if zero_pct and zero_pct > 0.05:
                    signals.append("has_zero_values")

            # P1.3: Fixed-length signal cho text
            if role in ["freetext", "categorical"] and is_text_type:
                length_stats = col_data.get("length_stats")
                if length_stats:
                    len_min = length_stats.get("min")
                    len_max = length_stats.get("max")
                    if len_min is not None and len_max is not None and len_min == len_max:
                        signals.append("fixed_length")

            # BOM Detection in names or values (giữ nguyên logic cũ)
            has_bom = "\ufeff" in col_name or (
                col_data.get("top_categories")
                and any("\ufeff" in str(cat.get("value", "")) for cat in col_data["top_categories"])
            )
            if has_bom:
                signals.append("BOM_detected")

            if signals:
                col_digest["signals"] = signals

            columns_digest.append(col_digest)

        table_digest = {
            "table": table_name,
            "rows": total_rows,
            # P2.1: ưu tiên cross-column hints ở đầu digest để LLM chú ý
            "cross_column_hints": cross_column_hints,
            "sample": sample_info,
            # P0.1: schema constraints ở cấp bảng
            "schema_constraints": schema_constraints_digest,
            "columns": columns_digest,
        }

        digest[table_name] = table_digest

    return digest


# ---------------------------------------------------------------------------
# Digest splitter — dùng bởi rule_proposer_node
# ---------------------------------------------------------------------------


def split_digest_by_table(digest: dict) -> dict[str, dict]:
    """Tách digest tổng thành dict {table_name: table_digest}.

    Tự động bỏ bọc nếu digest có key 'dataset_profile_digest' ở mức cao nhất
    (file debug được bọc lại, state field thì không).
    Bảng có key 'error' sẽ bị bỏ qua và in cảnh báo.

    Returns:
        dict[str, dict] — chỉ chứa các bảng hợp lệ.
    """
    if not digest:
        return {}

    # Bỏ bọc nếu file debug bọc thêm key ngoài
    if "dataset_profile_digest" in digest:
        digest = digest["dataset_profile_digest"]

    per_table: dict[str, dict] = {}
    for table_name, table_digest in digest.items():
        if isinstance(table_digest, dict) and "error" in table_digest:
            logger.warning(
                "Bỏ qua bảng '%s' vì chứa lỗi profile: %s",
                table_name,
                table_digest["error"],
            )
            continue
        per_table[table_name] = table_digest

    return per_table


def dump_table_digests(per_table: dict[str, dict], out_dir: str | Path) -> list[Path]:
    """Ghi mỗi bảng ra 1 file JSON để debug/inspect.

    Chỉ gọi khi bật cờ debug (settings.debug_dump_table_digests).

    Returns:
        Danh sách các Path đã ghi.
    """
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    for table_name, table_digest in per_table.items():
        file_path = out_path / f"{table_name}.json"
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(table_digest, f, ensure_ascii=False, indent=2)
        written.append(file_path)
        logger.debug("Đã ghi digest bảng '%s' ra %s", table_name, file_path)

    return written


if __name__ == "__main__":
    # with open("data/results/debug_profile_20260806_110510.json", "r") as f:
    #     raw_profile = json.load(f)
    # digest = generate_profile_digest(raw_profile)
    # with open("data/results/profile_digest.json", "w") as f:
    #     json.dump(digest, f, indent=2)

    # YOU SHOULD CREATE TEST BY YOURSELF
    pass
