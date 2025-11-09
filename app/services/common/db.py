from sqlalchemy import create_engine, Integer, String, Float, DateTime, Boolean, func
from sqlalchemy.orm import DeclarativeBase, mapped_column, Mapped, sessionmaker
from sqlalchemy.dialects.sqlite import JSON
from .config import DB_URL

engine = create_engine(DB_URL, echo=False, future=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

class Base(DeclarativeBase): pass

class FileMeta(Base):
    __tablename__="file_meta"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String, unique=True, index=True)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    content_type: Mapped[str] = mapped_column(String, default="application/octet-stream")
    tier: Mapped[str] = mapped_column(String, default="warm")
    location_primary: Mapped[str] = mapped_column(String)
    location_replicas: Mapped[str] = mapped_column(String, default="")
    last_access_ts: Mapped[str] = mapped_column(DateTime, default=func.now())
    access_1h: Mapped[int] = mapped_column(Integer, default=0)
    access_24h: Mapped[int] = mapped_column(Integer, default=0)
    checksum: Mapped[str] = mapped_column(String, default="")
    heat_score: Mapped[float] = mapped_column(Float, default=0.0)
    version_token: Mapped[str] = mapped_column(String, default="")

class AccessEvent(Base):
    __tablename__="access_event"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String, index=True)
    ts: Mapped[str] = mapped_column(DateTime, default=func.now())
    action: Mapped[str] = mapped_column(String, default="read")

class MigrationTask(Base):
    __tablename__="migration_task"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String, index=True)
    src: Mapped[str] = mapped_column(String)
    dst: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="queued")
    error: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[str] = mapped_column(DateTime, default=func.now())
    updated_at: Mapped[str] = mapped_column(DateTime, default=func.now(), onupdate=func.now())
    attempts: Mapped[int] = mapped_column(Integer, default=0)

class SystemSetting(Base):
    __tablename__="system_setting"
    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(String)

class AlertEvent(Base):
    __tablename__="alert_event"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    type: Mapped[str] = mapped_column(String)
    severity: Mapped[str] = mapped_column(String, default="info")
    message: Mapped[str] = mapped_column(String)
    data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[str] = mapped_column(DateTime, default=func.now())

def init_db():
    Base.metadata.create_all(engine)
