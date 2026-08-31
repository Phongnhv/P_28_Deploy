from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache

import regex

MAX_PATTERN_LENGTH = 256
MAX_VALUE_LENGTH = 4096
MATCH_TIMEOUT_SECONDS = 0.025

#: Trần thời gian biên dịch một pattern. `regex.compile` KHÔNG nhận tham số
#: `timeout`, nên chốt chặn duy nhất là chạy nó trong thread có deadline.
COMPILE_TIMEOUT_SECONDS = 0.25

#: Tổng thời gian regex cho MỘT lần chạy rule. `MATCH_TIMEOUT_SECONDS` chỉ chặn
#: từng lần gọi; hàm REGEXP của SQLite chạy một lần mỗi dòng, nên 50 000 dòng ×
#: 24 ms (vừa dưới ngưỡng) vẫn đốt khoảng 20 phút CPU mà không lần nào báo động.
DEFAULT_BUDGET_SECONDS = 5.0

#: Trần cho TÍCH các số lặp có cận `{n}` / `{n,m}` trong một pattern.
#: `(((((a{50}){50}){50}){50}){50})` chỉ dài 31 ký tự nhưng khai triển thành
#: 50^5 ≈ 312 triệu nhánh — đo thực tế: hơn 120 giây rồi kết thúc bằng
#: MemoryError. Giới hạn độ dài không chặn được nó; tích số lặp thì có.
MAX_REPEAT_EXPANSION = 100_000

#: Bắt `{3}`, `{2,5}`, `{4,}`. Pattern cố định, an toàn để biên dịch lúc import.
_REPEAT_QUANTIFIER = regex.compile(r"\{\s*(\d+)\s*(?:,\s*(\d*)\s*)?\}")


class SafeRegexError(ValueError):
    pass


class RegexBudget:
    """Ngân sách thời gian regex tích luỹ cho một lần chạy rule."""

    __slots__ = ("remaining", "total")

    def __init__(self, total_seconds: float = DEFAULT_BUDGET_SECONDS) -> None:
        self.total = total_seconds
        self.remaining = total_seconds

    def spend(self, elapsed: float) -> None:
        self.remaining -= elapsed
        if self.remaining <= 0:
            raise SafeRegexError(
                f"Regex budget of {self.total:g}s exhausted for this rule execution"
            )


_current_budget: ContextVar[RegexBudget | None] = ContextVar("regex_budget", default=None)


def start_regex_budget(total_seconds: float = DEFAULT_BUDGET_SECONDS) -> None:
    """Bắt đầu một ngân sách mới cho phạm vi hiện tại.

    Dùng cho các hook không có cấu trúc lồng nhau (ví dụ sự kiện
    `before_cursor_execute` của SQLAlchemy), nơi mỗi câu lệnh đơn giản thay thế
    ngân sách của câu lệnh trước.
    """
    _current_budget.set(RegexBudget(total_seconds))


@contextmanager
def regex_budget(total_seconds: float = DEFAULT_BUDGET_SECONDS):
    """Giới hạn TỔNG thời gian regex trong phạm vi khối lệnh.

    Bọc quanh mỗi lần thực thi rule. Không có nó, mỗi lần gọi vẫn bị chặn riêng
    nhưng tổng công việc thì không — đó là chỗ hở thật sự khi regex chạy theo dòng.
    """
    token = _current_budget.set(RegexBudget(total_seconds))
    try:
        yield
    finally:
        _current_budget.reset(token)


#: Pattern đã bị từ chối. `lru_cache` không nhớ exception, nên nếu không có
#: bộ nhớ này thì gửi lặp một pattern hỏng sẽ biên dịch lại mỗi lần — biến
#: chính cơ chế cache thành đường tấn công.
_rejected: dict[str, str] = {}


def repeat_expansion(pattern: str) -> int:
    """Tích các số lặp có cận trong pattern — thước đo chi phí khai triển.

    Dừng sớm khi vượt trần để bản thân phép đo không trở thành điểm nghẽn.
    """
    product = 1
    for match in _REPEAT_QUANTIFIER.finditer(pattern):
        lower, upper = match.group(1), match.group(2)
        count = int(upper) if upper else int(lower)
        product *= max(1, count)
        if product > MAX_REPEAT_EXPANSION:
            return product
    return product


def _compile_with_deadline(pattern: str) -> regex.Pattern:
    """Biên dịch trong thread RIÊNG, có deadline.

    Dùng thread mới cho mỗi lần thay vì một pool cố định là có chủ đích: với
    pool, hai pattern treo chiếm hết worker vĩnh viễn và mọi pattern HỢP LỆ sau
    đó đều bị từ chối — biến một vấn đề hiệu năng thành mất hẳn tính năng.

    Đây chỉ là lưới an toàn. Tuyến chính là `repeat_expansion`, chặn bom trước
    khi tốn một thread nào; và `_rejected` nhớ pattern đã bị từ chối nên mỗi
    pattern độc chỉ rò tối đa một thread trong toàn bộ vòng đời tiến trình.
    """
    result: list = []

    def _work() -> None:
        try:
            result.append(regex.compile(pattern))
        except BaseException as exc:  # gồm cả MemoryError từ pattern bệnh lý
            result.append(exc)

    worker = threading.Thread(target=_work, daemon=True, name="regex-compile")
    worker.start()
    worker.join(COMPILE_TIMEOUT_SECONDS)

    if not result:
        raise SafeRegexError("Regex pattern took too long to compile")
    outcome = result[0]
    if isinstance(outcome, regex.error):
        raise SafeRegexError("Regex pattern is invalid") from outcome
    if isinstance(outcome, BaseException):
        raise SafeRegexError("Regex pattern could not be compiled safely") from outcome
    return outcome


@lru_cache(maxsize=256)
def _compile(pattern: str) -> regex.Pattern:
    return _compile_with_deadline(pattern)


def validate_regex(pattern: str) -> str:
    if not isinstance(pattern, str) or not pattern:
        raise SafeRegexError("Regex pattern is required")
    if len(pattern) > MAX_PATTERN_LENGTH:
        raise SafeRegexError(f"Regex pattern exceeds {MAX_PATTERN_LENGTH} characters")
    cached_reason = _rejected.get(pattern)
    if cached_reason is not None:
        raise SafeRegexError(cached_reason)
    # Tuyến chính: chặn bom khai triển TRƯỚC khi biên dịch. Giới hạn độ dài
    # không đủ — một bom 31 ký tự đủ sức ngốn hàng phút CPU và cả bộ nhớ.
    expansion = repeat_expansion(pattern)
    if expansion > MAX_REPEAT_EXPANSION:
        reason = (
            f"Regex repetition expands to {expansion:,}+ branches, "
            f"over the {MAX_REPEAT_EXPANSION:,} limit"
        )
        if len(_rejected) < 1024:
            _rejected[pattern] = reason
        raise SafeRegexError(reason)
    try:
        _compile(pattern)
    except SafeRegexError as exc:
        if len(_rejected) < 1024:
            _rejected[pattern] = str(exc)
        raise
    return pattern


def safe_search(pattern: str, value: object) -> bool:
    validate_regex(pattern)
    text = str(value)
    if len(text) > MAX_VALUE_LENGTH:
        raise SafeRegexError(f"Regex input exceeds {MAX_VALUE_LENGTH} characters")

    budget = _current_budget.get()
    started = time.perf_counter()
    try:
        return _compile(pattern).search(text, timeout=MATCH_TIMEOUT_SECONDS) is not None
    except TimeoutError as exc:
        raise SafeRegexError("Regex evaluation timed out") from exc
    finally:
        if budget is not None:
            # Trừ ngân sách kể cả khi lần gọi này ném lỗi: công việc đã tiêu tốn
            # thật, và một pattern liên tục timeout chính là hình dạng tấn công.
            budget.spend(time.perf_counter() - started)
