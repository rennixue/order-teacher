from collections.abc import Sequence
from datetime import datetime

from sqlalchemy.dialects.mysql.types import DATETIME, INTEGER, MEDIUMTEXT, TEXT, TINYINT, VARCHAR
from sqlalchemy.ext.asyncio import AsyncAttrs, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql import delete, select, text, update
from sqlalchemy.sql.schema import UniqueConstraint


class Base(AsyncAttrs, DeclarativeBase):
    pass


"""
CREATE DATABASE some_database
CHARACTER SET utf8mb4
COLLATE utf8mb4_unicode_ci;
"""


class OrderRecord(Base):
    __tablename__ = "algo_order"
    id: Mapped[int] = mapped_column(INTEGER, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(INTEGER, index=True)
    created_at: Mapped[datetime] = mapped_column(DATETIME, server_default=text("CURRENT_TIMESTAMP"))
    data: Mapped[str | None] = mapped_column(MEDIUMTEXT)
    err_msg: Mapped[str | None] = mapped_column(TEXT)


class TeacherStableRecord(Base):
    __tablename__ = "algo_teacher_stable"
    id: Mapped[int] = mapped_column(INTEGER, primary_key=True, autoincrement=True)
    teacher_id: Mapped[int] = mapped_column(INTEGER, index=True)
    created_at: Mapped[datetime] = mapped_column(DATETIME, server_default=text("CURRENT_TIMESTAMP"))
    data: Mapped[str | None] = mapped_column(MEDIUMTEXT)
    err_msg: Mapped[str | None] = mapped_column(TEXT)


class TeacherUnstableRecord(Base):
    __tablename__ = "algo_teacher_unstable"
    id: Mapped[int] = mapped_column(INTEGER, primary_key=True, autoincrement=True)
    teacher_id: Mapped[int] = mapped_column(INTEGER, index=True)
    created_at: Mapped[datetime] = mapped_column(DATETIME, server_default=text("CURRENT_TIMESTAMP"))
    data: Mapped[str | None] = mapped_column(MEDIUMTEXT)
    err_msg: Mapped[str | None] = mapped_column(TEXT)


class TeacherMajorRecord(Base):
    __tablename__ = "algo_teacher_major"
    __table_args__ = (UniqueConstraint("teacher_id", "major_id", "kind"),)
    id: Mapped[int] = mapped_column(INTEGER, primary_key=True, autoincrement=True)
    teacher_id: Mapped[int] = mapped_column(INTEGER, index=True)
    major_id: Mapped[int] = mapped_column(INTEGER, index=True)
    kind: Mapped[int] = mapped_column(TINYINT)
    created_at: Mapped[datetime] = mapped_column(DATETIME, server_default=text("CURRENT_TIMESTAMP"))
    inactive: Mapped[int] = mapped_column(TINYINT(1), default=0, index=True)


class CourseRecord(Base):
    __tablename__ = "algo_course"
    __table_args__ = (UniqueConstraint("univ_id", "course_code"),)
    id: Mapped[int] = mapped_column(INTEGER, primary_key=True, autoincrement=True)
    univ_id: Mapped[int] = mapped_column(INTEGER, index=True)
    course_code: Mapped[str] = mapped_column(VARCHAR(255), index=True)
    created_at: Mapped[datetime] = mapped_column(DATETIME, server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = mapped_column(
        DATETIME, server_default=text("CURRENT_TIMESTAMP"), server_onupdate=text("CURRENT_TIMESTAMP")
    )
    summary: Mapped[str] = mapped_column(MEDIUMTEXT)


class CourseOrderRecord(Base):
    __tablename__ = "algo_course_order"
    id: Mapped[int] = mapped_column(INTEGER, primary_key=True, autoincrement=True)
    univ_id: Mapped[int] = mapped_column(INTEGER, index=True)
    course_code: Mapped[str] = mapped_column(VARCHAR(255), index=True)
    order_id: Mapped[int] = mapped_column(INTEGER, index=True)
    created_at: Mapped[datetime] = mapped_column(DATETIME, server_default=text("CURRENT_TIMESTAMP"))
    course_name: Mapped[str] = mapped_column(TEXT)
    prof_id: Mapped[int] = mapped_column(INTEGER)
    spec_id: Mapped[int | None] = mapped_column(INTEGER)


class Database:
    def __init__(self, url: str) -> None:
        self._engine = create_async_engine(url, pool_size=2, pool_recycle=600)
        self._session_factory = async_sessionmaker(self._engine, expire_on_commit=False)

    async def metadata_create_all(self) -> None:
        async with self._engine.connect() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def select_order_exists(self, order_id: int) -> bool:
        async with self._session_factory() as session:
            r = await session.scalar(
                select(OrderRecord)
                .where(OrderRecord.order_id == order_id)
                .where(OrderRecord.data.is_not(None))
                .limit(1)
            )
        return r is not None

    async def insert_order(self, order_id: int, data: str) -> None:
        async with self._session_factory() as session:
            session.add(OrderRecord(order_id=order_id, data=data))
            await session.commit()

    async def insert_order_err(self, order_id: int, err_msg: str) -> None:
        async with self._session_factory() as session:
            session.add(OrderRecord(order_id=order_id, err_msg=err_msg))
            await session.commit()

    async def select_teacher_stable_exists(self, teacher_id: int) -> bool:
        async with self._session_factory() as session:
            r = await session.scalar(
                select(TeacherStableRecord)
                .where(TeacherStableRecord.teacher_id == teacher_id)
                .where(TeacherStableRecord.data.is_not(None))
                .limit(1)
            )
        return r is not None

    async def insert_teacher_stable(
        self, teacher_id: int, data: str, major_ids: Sequence[int], edu_major_ids: Sequence[int]
    ) -> None:
        async with self._session_factory() as session:
            session.add(TeacherStableRecord(teacher_id=teacher_id, data=data))
            await session.execute(
                delete(TeacherMajorRecord)
                .where(TeacherMajorRecord.teacher_id == teacher_id)
                .where(TeacherMajorRecord.kind.in_([1, 2]))
                .where(TeacherMajorRecord.inactive == 0)
            )
            excl_major_ids = (
                await session.scalars(
                    select(TeacherMajorRecord.major_id)
                    .where(TeacherMajorRecord.teacher_id == teacher_id)
                    .where(TeacherMajorRecord.inactive == 1)
                )
            ).all()
            session.add_all(
                [
                    TeacherMajorRecord(teacher_id=teacher_id, major_id=it, kind=1)
                    for it in edu_major_ids
                    if it not in excl_major_ids
                ]
            )
            session.add_all(
                [
                    TeacherMajorRecord(teacher_id=teacher_id, major_id=it, kind=2)
                    for it in major_ids
                    if it not in excl_major_ids
                ]
            )
            await session.commit()

    async def insert_teacher_stable_err(self, teacher_id: int, err_msg: str) -> None:
        async with self._session_factory() as session:
            session.add(TeacherStableRecord(teacher_id=teacher_id, err_msg=err_msg))
            await session.commit()

    async def select_teacher_unstable_exists(self, teacher_id: int) -> bool:
        async with self._session_factory() as session:
            r = await session.scalar(
                select(TeacherUnstableRecord)
                .where(TeacherUnstableRecord.teacher_id == teacher_id)
                .where(TeacherUnstableRecord.data.is_not(None))
                .limit(1)
            )
        return r is not None

    async def insert_teacher_unstable(self, teacher_id: int, data: str, major_ids: Sequence[int]) -> None:
        async with self._session_factory() as session:
            session.add(TeacherUnstableRecord(teacher_id=teacher_id, data=data))
            await session.execute(
                delete(TeacherMajorRecord)
                .where(TeacherMajorRecord.teacher_id == teacher_id)
                .where(TeacherMajorRecord.kind == 3)
                .where(TeacherMajorRecord.inactive == 0)
            )
            excl_major_ids = (
                await session.scalars(
                    select(TeacherMajorRecord.major_id)
                    .where(TeacherMajorRecord.teacher_id == teacher_id)
                    .where(TeacherMajorRecord.inactive == 1)
                )
            ).all()
            session.add_all(
                [
                    TeacherMajorRecord(teacher_id=teacher_id, major_id=it, kind=3)
                    for it in major_ids
                    if it not in excl_major_ids
                ]
            )
            await session.commit()

    async def insert_teacher_unstable_err(self, teacher_id: int, err_msg: str) -> None:
        async with self._session_factory() as session:
            session.add(TeacherUnstableRecord(teacher_id=teacher_id, err_msg=err_msg))
            await session.commit()

    async def select_teacher_ids_by_major_ids(
        self, major_ids: Sequence[int], kinds: Sequence[int], excl_teacher_ids: Sequence[int]
    ) -> list[int]:
        stmt = (
            select(TeacherMajorRecord.teacher_id)
            .distinct()
            .where(TeacherMajorRecord.major_id.in_(major_ids))
            .where(TeacherMajorRecord.kind.in_(kinds))
            .where(TeacherMajorRecord.inactive == 0)
        )
        if excl_teacher_ids:
            stmt = stmt.where(TeacherMajorRecord.teacher_id.not_in(excl_teacher_ids))
        async with self._session_factory() as session:
            scalars = await session.scalars(stmt)
            teacher_ids = scalars.all()
        return list(teacher_ids)

    async def get_latest_order(self, order_id: int, only_success: bool = True) -> OrderRecord | None:
        stmt = (
            select(OrderRecord).where(OrderRecord.order_id == order_id).order_by(OrderRecord.created_at.desc()).limit(1)
        )
        if only_success:
            stmt = stmt.where(OrderRecord.data.is_not(None))
        async with self._session_factory() as session:
            order = await session.scalar(stmt)
        return order

    async def get_latest_teacher_stable(self, teacher_id: int) -> TeacherStableRecord | None:
        async with self._session_factory() as session:
            teacher_stable = await session.scalar(
                select(TeacherStableRecord)
                .where(TeacherStableRecord.teacher_id == teacher_id)
                .where(TeacherStableRecord.data.is_not(None))
                .order_by(TeacherStableRecord.created_at.desc())
                .limit(1)
            )
        return teacher_stable

    async def get_latest_teacher_unstable(self, teacher_id: int) -> TeacherUnstableRecord | None:
        async with self._session_factory() as session:
            teacher_unstable = await session.scalar(
                select(TeacherUnstableRecord)
                .where(TeacherUnstableRecord.teacher_id == teacher_id)
                .where(TeacherUnstableRecord.data.is_not(None))
                .order_by(TeacherUnstableRecord.created_at.desc())
                .limit(1)
            )
        return teacher_unstable

    async def get_course(self, univ_id: int, course_code: str) -> CourseRecord | None:
        async with self._session_factory() as session:
            course = await session.scalar(
                select(CourseRecord)
                .where(CourseRecord.univ_id == univ_id)
                .where(CourseRecord.course_code == course_code)
                .limit(1)
            )
        return course

    async def insert_course(
        self,
        univ_id: int,
        course_code: str,
        summary: str,
        order_id: int,
        course_name: str,
        prof_id: int,
        spec_id: int | None,
    ) -> None:
        async with self._session_factory() as session:
            session.add(CourseRecord(univ_id=univ_id, course_code=course_code, summary=summary))
            session.add(
                CourseOrderRecord(
                    univ_id=univ_id,
                    course_code=course_code,
                    order_id=order_id,
                    course_name=course_name,
                    prof_id=prof_id,
                    spec_id=spec_id,
                )
            )
            await session.commit()

    async def update_course(
        self,
        univ_id: int,
        course_code: str,
        summary: str | None,
        order_id: int,
        course_name: str,
        prof_id: int,
        spec_id: int | None,
    ) -> None:
        async with self._session_factory() as session:
            if summary:
                await session.execute(
                    update(CourseRecord)
                    .where(CourseRecord.univ_id == univ_id)
                    .where(CourseRecord.course_code == course_code)
                    .values(summary=summary)
                )
            session.add(
                CourseOrderRecord(
                    univ_id=univ_id,
                    course_code=course_code,
                    order_id=order_id,
                    course_name=course_name,
                    prof_id=prof_id,
                    spec_id=spec_id,
                )
            )
            await session.commit()
