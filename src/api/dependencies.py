from fastapi import Header, HTTPException
from sqlalchemy.orm import Session

from src.models.database import JobModel
from src.services.rule_store import get_engine


def verify_idempotency_key(idempotency_key: str = Header(..., alias="Idempotency-Key")):
    """Từ chối một Idempotency-Key đã được dùng.

    Thân phản hồi 409 KHÔNG kèm `job_id`. Tra cứu ở đây là toàn cục, nên trả về
    định danh job sẽ để lộ job của người khác cho bất kỳ ai đoán trúng khoá —
    trong khi người gọi hợp lệ không cần định danh đó để xử lý xung đột.

    CÒN LẠI: phạm vi đúng phải là (người gọi, khoá) chứ không phải khoá toàn
    cục, để không ai cố tình dùng trùng khoá nhằm CHẶN job hợp lệ của người
    khác. `JobModel` hiện chưa có cột chủ sở hữu nên chưa lọc theo người gọi
    được; việc đó cần thêm cột `created_by` kèm migration.
    """
    if not idempotency_key:
        raise HTTPException(status_code=400, detail="Idempotency-Key header is required")

    with Session(get_engine()) as session:
        existing_job = session.query(JobModel).filter_by(idempotency_key=idempotency_key).first()
        if existing_job:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "IDEMPOTENCY_CONFLICT",
                    "message": "Request already processed or in progress",
                    "status": existing_job.status,
                },
            )
    return idempotency_key
