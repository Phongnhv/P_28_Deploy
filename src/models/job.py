import uuid
from sqlalchemy import Column, String, Integer, DateTime, Text, func
from src.services.rule_store import Base

class JobModel(Base):
    __tablename__ = "jobs"
    
    id = Column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    type = Column(String(64), nullable=False)
    idempotency_key = Column(String(256), unique=True, index=True, nullable=False)
    linked_entity = Column(String(256))
    status = Column(String(32), default='PENDING', index=True)
    error = Column(Text)
    attempt_count = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
