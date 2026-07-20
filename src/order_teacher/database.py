import logging
from datetime import datetime

from sqlalchemy import ForeignKey, literal, text
from sqlalchemy.dialects.mysql.types import DATETIME, FLOAT, INTEGER, SMALLINT, TEXT, TINYINT, VARCHAR
from sqlalchemy.ext.asyncio import AsyncAttrs, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, joinedload, mapped_column, relationship

from .models import TeacherMatch

logger = logging.getLogger(__name__)


class Base(AsyncAttrs, DeclarativeBase):
    pass


class JobRecord(Base):
    __tablename__ = "app_job"
    job_id: Mapped[int] = mapped_column(INTEGER, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(INTEGER, index=True)
    created_at: Mapped[datetime] = mapped_column(DATETIME, server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = mapped_column(
        DATETIME, server_default=text("CURRENT_TIMESTAMP"), server_onupdate=text("CURRENT_TIMESTAMP")
    )
    status: Mapped[int] = mapped_column(TINYINT, server_default=literal(0))
    err_msg: Mapped[str | None] = mapped_column(VARCHAR(255))
    priv_msg: Mapped[str | None] = mapped_column(TEXT)
    matches: Mapped[list["MatchRecord"]] = relationship(back_populates="job", cascade="all, delete-orphan")


class MatchRecord(Base):
    __tablename__ = "app_match"
    match_id: Mapped[int] = mapped_column(INTEGER, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("app_job.job_id"))
    no: Mapped[int] = mapped_column(SMALLINT)
    teacher_id: Mapped[int] = mapped_column(INTEGER)
    tier: Mapped[int] = mapped_column(TINYINT)
    prof_score: Mapped[float] = mapped_column(FLOAT)
    job: Mapped["JobRecord"] = relationship(back_populates="matches")


class FeedbackRecord(Base):
    __tablename__ = "app_feedback"
    feedback_id: Mapped[int] = mapped_column(INTEGER, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(INTEGER, index=True)
    teacher_id: Mapped[int] = mapped_column(INTEGER, index=True)
    created_at: Mapped[datetime] = mapped_column(DATETIME, server_default=text("CURRENT_TIMESTAMP"))
    choice: Mapped[int | None] = mapped_column(SMALLINT)
    message: Mapped[str] = mapped_column(TEXT)


class DatabaseService:
    def __init__(self, url: str) -> None:
        self._engine = create_async_engine(url, pool_size=2, pool_recycle=600)
        self._session_factory = async_sessionmaker(self._engine, expire_on_commit=False)

    async def is_healthy(self) -> bool:
        try:
            async with self._engine.connect() as conn:
                cursor = await conn.execute(text("SELECT 1"))
                assert cursor.scalar_one() == 1
        except Exception as exc:
            logger.error("fail to connect to database: %r", exc)
            return False
        else:
            return True

    async def metadata_create_all(self) -> None:
        async with self._engine.connect() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def create_job(self, order_id: int) -> int:
        async with self._session_factory() as session:
            job = JobRecord(order_id=order_id)
            session.add(job)
            await session.commit()
            return job.job_id

    async def mark_job_succeed(self, job_id: int, teacher_matches: list[TeacherMatch]) -> None:
        async with self._session_factory() as session:
            if job := await session.get(JobRecord, job_id, options=[joinedload(JobRecord.matches)]):
                job.status = 1
                matches = [MatchRecord(**it.model_dump()) for it in teacher_matches]
                job.matches = matches
                await session.commit()

    async def mark_job_fail(self, job_id: int, err_msg: str, priv_msg: str | None = None) -> None:
        async with self._session_factory() as session:
            if job := await session.get(JobRecord, job_id):
                job.status = 2
                job.err_msg = err_msg
                job.priv_msg = priv_msg
                await session.commit()

    async def get_job(self, job_id: int, with_matches: bool = False) -> JobRecord | None:
        options = [joinedload(JobRecord.matches)] if with_matches else None
        async with self._session_factory() as session:
            if job := await session.get(JobRecord, job_id, options=options):
                return job
            return None

    async def create_feedback(self, order_id: int, teacher_id: int, choice: int | None, message: str) -> None:
        async with self._session_factory() as session:
            job = FeedbackRecord(order_id=order_id, teacher_id=teacher_id, choice=choice, message=message)
            session.add(job)
            await session.commit()
