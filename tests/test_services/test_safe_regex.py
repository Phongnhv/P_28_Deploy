"""Chốt chặn ReDoS: giới hạn theo lần gọi, theo biên dịch, và theo truy vấn."""

from __future__ import annotations

import time

import pytest

from src.services import safe_regex
from src.services.safe_regex import (
    MAX_PATTERN_LENGTH,
    MAX_REPEAT_EXPANSION,
    RegexBudget,
    SafeRegexError,
    regex_budget,
    repeat_expansion,
    safe_search,
    validate_regex,
)


def test_valid_pattern_passes():
    assert validate_regex(r"^\d{3}-\d{4}$") == r"^\d{3}-\d{4}$"
    assert safe_search(r"^\d+$", "12345") is True
    assert safe_search(r"^\d+$", "abc") is False


def test_pattern_length_is_capped():
    with pytest.raises(SafeRegexError, match="exceeds"):
        validate_regex("a" * (MAX_PATTERN_LENGTH + 1))


def test_invalid_pattern_is_rejected():
    with pytest.raises(SafeRegexError, match="invalid"):
        validate_regex("(unclosed")


def test_rejected_pattern_is_remembered():
    """`lru_cache` không nhớ exception.

    Không có bộ nhớ riêng cho pattern hỏng thì gửi lặp một pattern hỏng sẽ biên
    dịch lại mỗi lần — biến chính cơ chế cache thành đường tấn công.
    """
    pattern = "(cung-mot-pattern-hong-" + "x" * 20
    with pytest.raises(SafeRegexError):
        validate_regex(pattern)
    assert pattern in safe_regex._rejected
    with pytest.raises(SafeRegexError):
        validate_regex(pattern)


def test_oversized_input_is_rejected():
    with pytest.raises(SafeRegexError, match="input exceeds"):
        safe_search(r"^a+$", "a" * (safe_regex.MAX_VALUE_LENGTH + 1))


# ---------------------------------------------------------------------------
# Ngân sách theo truy vấn — chỗ hở chính khi regex chạy theo dòng
# ---------------------------------------------------------------------------


def test_budget_accumulates_and_raises():
    budget = RegexBudget(total_seconds=0.05)
    budget.spend(0.03)
    assert budget.remaining == pytest.approx(0.02)
    with pytest.raises(SafeRegexError, match="budget"):
        budget.spend(0.03)


def test_budget_exhausts_across_many_cheap_calls():
    """Đây chính là lỗ hổng thật.

    Từng lần gọi đều nằm dưới ngưỡng per-call, nên không lần nào báo động —
    nhưng tổng công việc thì không giới hạn. 50 000 dòng × 24 ms ≈ 20 phút CPU.
    """
    with pytest.raises(SafeRegexError, match="budget"):
        with regex_budget(total_seconds=0.02):
            deadline = time.perf_counter() + 5.0
            while time.perf_counter() < deadline:
                safe_search(r"^[a-z]+$", "abcdefghijklmnop")
            pytest.fail("Ngân sách không bao giờ cạn — giới hạn theo truy vấn không hoạt động")


def test_no_budget_means_no_accumulation():
    """Không có ngân sách thì hành vi giữ nguyên như cũ — chỉ chặn theo lần gọi."""
    for _ in range(200):
        assert safe_search(r"^[a-z]+$", "abcdef") is True


def test_budget_is_reset_between_scopes():
    with regex_budget(total_seconds=1.0):
        safe_search(r"^[a-z]+$", "abc")
    with regex_budget(total_seconds=1.0):
        assert safe_search(r"^[a-z]+$", "abc") is True


# ---------------------------------------------------------------------------
# Bom biên dịch — giới hạn độ dài KHÔNG đủ để chặn
# ---------------------------------------------------------------------------

COMPILE_BOMB = "(((((a{50}){50}){50}){50}){50})"


def test_compile_bomb_is_short_enough_to_pass_the_length_cap():
    """Chứng minh vì sao cần phép chặn riêng: bom chỉ dài 31 ký tự.

    Đo thực tế trên `regex.compile`: hơn 120 giây rồi kết thúc bằng MemoryError.
    """
    assert len(COMPILE_BOMB) < MAX_PATTERN_LENGTH


def test_compile_bomb_is_rejected_quickly():
    started = time.perf_counter()
    with pytest.raises(SafeRegexError, match="expands to"):
        validate_regex(COMPILE_BOMB)
    elapsed = time.perf_counter() - started
    assert elapsed < 0.5, "Bom phải bị chặn TRƯỚC khi biên dịch, không phải bằng deadline"


@pytest.mark.parametrize(
    "pattern",
    [r"^\d{3}-\d{4}$", r"^[a-z]+$", r"a{1000}", r"^\w{2,64}$", r"(\d{2}){3}"],
)
def test_realistic_patterns_are_not_caught_by_the_expansion_limit(pattern):
    """Phép chặn phải nhắm đúng bom, không được bắt nhầm rule thật."""
    assert repeat_expansion(pattern) <= MAX_REPEAT_EXPANSION
    assert validate_regex(pattern) == pattern


def test_hung_compiles_do_not_poison_later_valid_patterns():
    """Hồi quy cho một lỗi từng được đưa vào rồi gỡ bỏ.

    Bản đầu dùng ThreadPoolExecutor 2 worker. Hai pattern treo chiếm hết worker
    vĩnh viễn và MỌI pattern hợp lệ sau đó đều bị từ chối — biến một vấn đề hiệu
    năng thành mất hẳn tính năng, không tự phục hồi.
    """
    import regex as real_regex

    original_compile = real_regex.compile

    def maybe_hang(pattern, *args, **kwargs):
        if pattern.startswith("HANG"):
            time.sleep(60)
        return original_compile(pattern, *args, **kwargs)

    safe_regex.regex.compile = maybe_hang
    try:
        for index in range(5):
            with pytest.raises(SafeRegexError):
                safe_regex._compile_with_deadline(f"HANG{index}")
        assert safe_regex._compile_with_deadline(r"^\d{3}$") is not None
    finally:
        safe_regex.regex.compile = original_compile
