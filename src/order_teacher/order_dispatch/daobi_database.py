import logging
from collections.abc import Sequence
from itertools import groupby
from textwrap import dedent

import sqlalchemy
from sqlalchemy.ext.asyncio import create_async_engine

from .models import *  # noqa: F403

logger = logging.getLogger(__name__)


class DaobiDatabase:
    def __init__(self, url: str) -> None:
        self._engine = create_async_engine(url, pool_size=2, pool_recycle=600)

    async def select_teacher_ids_by_same_course(self, order_id: int, excl_teacher_ids: Sequence[int]) -> list[int]:
        async with self._engine.connect() as conn:
            cursor = await conn.execute(
                sqlalchemy.text("""
                    SELECT university_id, course_code FROM stud_purchase_order WHERE course_id = :order_id LIMIT 1
                """),
                {"order_id": order_id},
            )
            row = cursor.one_or_none()
            if row is None:
                return []
            univ_id, crscode = row
            if univ_id is None or crscode is None or crscode == "":
                return []
            if excl_teacher_ids:
                cursor = await conn.execute(
                    sqlalchemy.text("""
                        SELECT tu.id
                        FROM teac_user tu
                        JOIN teac_order `to` ON `to`.user_id = tu.id
                        JOIN stud_purchase_order spo ON spo.course_id = `to`.course_id
                        WHERE tu.statused = 0
                        AND `to`.statused IN (1, 2, 4, 16)
                        AND spo.university_id = :univ_id AND spo.course_code = :crscode
                        AND tu.id NOT IN :excl_teacher_ids
                        GROUP BY tu.id
                        ORDER BY count(*) DESC, tu.id
                        LIMIT 100
                    """).bindparams(excl_teacher_ids=tuple(excl_teacher_ids)),
                    {"univ_id": univ_id, "crscode": crscode},
                )
            else:
                cursor = await conn.execute(
                    sqlalchemy.text("""
                        SELECT tu.id
                        FROM teac_user tu
                        JOIN teac_order `to` ON `to`.user_id = tu.id
                        JOIN stud_purchase_order spo ON spo.course_id = `to`.course_id
                        WHERE tu.statused = 0
                        AND `to`.statused IN (1, 2, 4, 16)
                        AND spo.university_id = :univ_id AND spo.course_code = :crscode
                        GROUP BY tu.id
                        ORDER BY count(*) DESC, tu.id
                        LIMIT 100
                    """),
                    {"univ_id": univ_id, "crscode": crscode},
                )
            teacher_ids = cursor.scalars().all()
        return list(teacher_ids)

    async def select_teacher_ids_by_same_student(self, order_id: int, excl_teacher_ids: Sequence[int]) -> list[int]:
        async with self._engine.connect() as conn:
            cursor = await conn.execute(
                sqlalchemy.text("""
                    SELECT user_id FROM stud_course WHERE id = :order_id LIMIT 1
                """),
                {"order_id": order_id},
            )
            student_id = cursor.scalar_one_or_none()
            if student_id is None:
                return []
            if excl_teacher_ids:
                cursor = await conn.execute(
                    sqlalchemy.text("""
                        SELECT tu.id
                        FROM teac_user tu
                        JOIN teac_order `to` ON `to`.user_id = tu.id
                        JOIN stud_course sc ON sc.id = `to`.course_id
                        WHERE tu.statused = 0
                        AND `to`.statused IN (1, 2, 4, 16)
                        AND sc.user_id = :student_id
                        AND tu.id NOT IN :excl_teacher_ids
                        GROUP BY tu.id
                        ORDER BY count(*) DESC, tu.id
                        LIMIT 100
                    """).bindparams(excl_teacher_ids=tuple(excl_teacher_ids)),
                    {"student_id": student_id},
                )
            else:
                cursor = await conn.execute(
                    sqlalchemy.text("""
                        SELECT tu.id
                        FROM teac_user tu
                        JOIN teac_order `to` ON `to`.user_id = tu.id
                        JOIN stud_course sc ON sc.id = `to`.course_id
                        WHERE tu.statused = 0
                        AND `to`.statused IN (1, 2, 4, 16)
                        AND sc.user_id = :student_id
                        GROUP BY tu.id
                        ORDER BY count(*) DESC, tu.id
                        LIMIT 100
                    """),
                    {"student_id": student_id},
                )
            teacher_ids = cursor.scalars().all()
        return list(teacher_ids)

    async def select_teacher_ids_by_same_major(self, order_id: int, excl_teacher_ids: Sequence[int]) -> list[int]:
        async with self._engine.connect() as conn:
            cursor = await conn.execute(
                sqlalchemy.text("""
                    SELECT professional_id FROM stud_course WHERE id = :order_id LIMIT 1
                """),
                {"order_id": order_id},
            )
            prof_id = cursor.scalar_one_or_none()
            if prof_id is None:
                return []
            if excl_teacher_ids:
                cursor = await conn.execute(
                    sqlalchemy.text("""
                        SELECT tu.id
                        FROM teac_user tu
                        JOIN teac_education_professional tep ON tep.teac_id = tu.id
                        WHERE tu.statused = 0
                        AND tu.id NOT IN :excl_teacher_ids
                        AND tep.pro_id = :prof_id
                        LIMIT 1000
                    """).bindparams(excl_teacher_ids=tuple(excl_teacher_ids)),
                    {"prof_id": prof_id},
                )
            else:
                cursor = await conn.execute(
                    sqlalchemy.text("""
                        SELECT tu.id
                        FROM teac_user tu
                        JOIN teac_education_professional tep ON tep.teac_id = tu.id
                        WHERE tu.statused = 0
                        AND tep.pro_id = :prof_id
                        LIMIT 1000
                    """),
                    {"prof_id": prof_id},
                )
            teacher_ids = cursor.scalars().all()
        return list(teacher_ids)

    async def fetch_latest_courses_by_profession(self, profession_id: int) -> list[CourseProfessionRecord]:
        async with self._engine.connect() as conn:
            cursor = await conn.execute(
                sqlalchemy.text(
                    dedent("""
                        SELECT
                            p.id prof_id, p.name prof_en_name, p.chinese_name prof_zh_name,
                            c.professional_name prof_name_input,
                            c.id course_id, c.create_at created_at, c.order_no order_name,
                            o.course_name
                        FROM stud_course c
                        JOIN stud_purchase_order o ON c.id = o.course_id
                        JOIN sys_professional_courses p ON c.professional_id = p.id
                        WHERE c.professional_id = :profession_id
                        ORDER BY c.id DESC
                        LIMIT 10
                    """)
                ),
                {"profession_id": profession_id},
            )
            rows = cursor.all()
        return [CourseProfessionRecord.model_validate(row, from_attributes=True) for row in rows]

    async def fetch_course_info(self, course_id: int) -> CourseInfo | None:
        async with self._engine.connect() as conn:
            cursor = await conn.execute(
                sqlalchemy.text(
                    dedent("""
                        SELECT
                            c.id course_id, c.create_at created_at, o.`type` order_type, c.order_no order_name,
                            o.university_id univ_id, su.name univ_name,
                            c.professional_id prof_id, concat(sp.chinese_name, '(', sp.name, ')') prof_name,
                            c.specialty_class_id spec_id, concat(ss.name, '(', ss.en_name, ')') spec_name,
                            o.course_code, o.course_name
                        FROM stud_course c
                        JOIN stud_purchase_order o ON c.id = o.course_id
                        LEFT JOIN sys_university su ON o.university_id = su.id
                        LEFT JOIN sys_professional_courses sp ON c.professional_id = sp.id
                        LEFT JOIN sys_specialty_class ss ON c.specialty_class_id = ss.id
                        WHERE c.id = :course_id
                        LIMIT 1
                    """)
                ),
                {"course_id": course_id},
            )
            row = cursor.one_or_none()
            if row is None:
                return None
            info = CourseInfo.model_validate(row, from_attributes=True)
            cursor = await conn.execute(
                sqlalchemy.text(
                    dedent("""
                        SELECT
                            o.remark, o.oper_remark, o.special_offer_remark,
                            o.seller_demand_desc, o.description, o.general_client_message,
                            cc.first_lesson_needs, cc.other_needs, cc.order_requirements
                        FROM stud_purchase_order o
                        LEFT JOIN stud_course_customized cc ON cc.course_id = o.course_id
                        WHERE o.course_id = :course_id
                        LIMIT 1
                    """)
                ),
                {"course_id": course_id},
            )
            row = cursor.one_or_none()
            if row is None:
                needs = {}
            else:
                needs = {k: v for k, v in row._asdict().items() if v and v != "[]"}  # pyright: ignore[reportPrivateUsage]
        if needs:
            info.needs = needs
        return info

    async def fetch_coursewares(self, course_id: int) -> list[RemoteCourseware]:
        async with self._engine.connect() as conn:
            cursor = await conn.execute(
                sqlalchemy.text(
                    dedent("""
                        SELECT w.id AS courseware_id, w.cd_id AS course_id, w.name, w.url
                        FROM stud_courseware w
                        WHERE w.delete_flag = 0 AND w.is_hide = 0
                        AND w.cd_id = :course_id
                        AND w.group_id IN (5, 6, 21, 24, 26)
                        AND w.name RLIKE '.zip$|.rar$|.pdf$|.docx$|.doc$|.pptx$|.ppt$'
                        AND w.url <> ''
                        ORDER BY w.id
                        LIMIT 100
                    """)
                ),
                {"course_id": course_id},
            )
            rows = cursor.all()
        return [RemoteCourseware.model_validate(row, from_attributes=True) for row in rows]

    async def fetch_teacher_info(self, teacher_id: int) -> TeacherInfo | None:
        async with self._engine.connect() as conn:
            cursor = await conn.execute(
                sqlalchemy.text(
                    dedent("""
                        SELECT
                            tu.id teacher_id, tu.username, tu.nick_name nickname, tu.alias wxwork_name,
                            tu.statused job_status, tu.`type` job_type, tu.introduce intro
                        FROM teac_user tu
                        WHERE tu.id = :teacher_id
                        LIMIT 1
                    """)
                ),
                {"teacher_id": teacher_id},
            )
            row = cursor.one_or_none()
            if row is None:
                return None
            info = TeacherInfo.model_validate(row, from_attributes=True)
            cursor = await conn.execute(
                sqlalchemy.text(
                    dedent("""
                        SELECT
                            te.`type` edu_type,
                            te.university_id univ_id, su.name univ_name,
                            tep.pro_id prof_id, concat(sp.chinese_name, '(', sp.name, ')') prof_name
                        FROM teac_user tu
                        JOIN teac_education te ON te.teac_id = tu.id
                        LEFT JOIN teac_education_professional tep ON tep.education_id = te.id
                        LEFT JOIN sys_university su ON su.id = te.university_id
                        LEFT JOIN sys_professional_courses sp ON sp.id = tep.pro_id
                        WHERE tu.id = :teacher_id
                        AND te.delete_flag = 0
                        AND tep.delete_flag = 0
                        ORDER BY te.`type`, te.university_id, tep.pro_id
                        LIMIT 100
                    """)
                ),
                {"teacher_id": teacher_id},
            )
            row_dicts = [it._asdict() for it in cursor.all()]  # pyright: ignore[reportPrivateUsage]
            edus: list[TeacherEdu] = []
            for key, prof_dicts in groupby(row_dicts, key=lambda it: (it["edu_type"], it["univ_id"], it["univ_name"])):
                edu_type, univ_id, univ_name = key
                edus.append(
                    TeacherEdu.model_validate(
                        dict(edu_type=edu_type, univ_id=univ_id, univ_name=univ_name, profs=list(prof_dicts))
                    )
                )
            info.edus = edus
        return info

    async def fetch_teacher_files(self, teacher_id: int) -> list[RemoteTeacherFile]:
        files: list[RemoteTeacherFile] = []
        async with self._engine.connect() as conn:
            cursor = await conn.execute(
                sqlalchemy.text(
                    dedent("""
                        SELECT tud.resume resume_url, tud.resume_name
                        FROM teac_user_details tud
                        WHERE tud.teac_id = :teacher_id
                        AND tud.resume <> ''
                        AND tud.resume_name <> ''
                        AND tud.delete_flag = 0
                        LIMIT 1
                    """)
                ),
                {"teacher_id": teacher_id},
            )
            row = cursor.one_or_none()
            if row:
                files.append(
                    RemoteTeacherFile(
                        teacher_id=teacher_id, teacher_file_id=0, file_type=0, name=row.resume_name, url=row.resume_url
                    )
                )
            cursor = await conn.execute(
                sqlalchemy.text("""
                    SELECT tu.en_report_url, tu.en_report_name
                    FROM teac_user tu
                    WHERE tu.id = :teacher_id
                    AND tu.en_report_name <> ''
                    AND tu.en_report_url <> ''
                    AND tu.delete_flag = 0
                    LIMIT 1
                """),
                {"teacher_id": teacher_id},
            )
            row = cursor.one_or_none()
            if row:
                files.append(
                    RemoteTeacherFile(
                        teacher_id=teacher_id,
                        teacher_file_id=1,
                        file_type=1,
                        name=row.en_report_name,
                        url=row.en_report_url,
                    )
                )
            cursor = await conn.execute(
                sqlalchemy.text(
                    dedent("""
                        SELECT tuf.teac_id teacher_id, tuf.id teacher_file_id, 1 file_type, tuf.file_name name, tuf.file_url url
                        FROM teac_user_file tuf
                        WHERE tuf.teac_id = :teacher_id
                        AND tuf.file_name <> ''
                        AND tuf.file_url <> ''
                        AND tuf.delete_flag = 0
                        LIMIT 10
                    """)
                ),
                {"teacher_id": teacher_id},
            )
            rows = cursor.all()
            files.extend(RemoteTeacherFile.model_validate(row, from_attributes=True) for row in rows)
        return files

    async def fetch_teacher_statistics(self, teacher_id: int) -> list[TeacherStatistic]:
        async with self._engine.connect() as conn:
            cursor = await conn.execute(
                sqlalchemy.text(
                    dedent("""
                        SELECT
                            data_type, order_type course_type,
                            finish_order_num num_order, high_rate, fail_rate, complaint_rate, avg_score,
                            CASE
                              WHEN order_type IN (0, 1, 26) THEN round(class_experience_molecule / 3600000, 3)
                              WHEN order_type IN (65, 67, 71) THEN round(class_experience / 1000, 3)
                              WHEN order_type IS NULL THEN round(class_experience_molecule / 3600000, 3)
                            END experience
                        FROM ds_teacher
                        WHERE data_type IN (1, 2)
                        AND (order_type IN (0, 1, 26, 65, 67, 71) OR order_type IS NULL)
                        AND teacher_id = :teacher_id
                        ORDER BY data_type, order_type
                        LIMIT 100
                    """)
                ),
                {"teacher_id": teacher_id},
            )
            rows = cursor.all()
        return [TeacherStatistic.model_validate(row, from_attributes=True) for row in rows]

    async def fetch_teacher_courses(self, teacher_id: int, num: int) -> list[int]:
        async with self._engine.connect() as conn:
            cursor = await conn.execute(
                sqlalchemy.text(
                    dedent("""
                        SELECT `to`.course_id
                        FROM teac_order `to`
                        WHERE `to`.user_id = :teacher_id
                        AND `to`.statused IN (16)
                        ORDER BY `to`.course_id DESC
                        LIMIT :limit
                    """)
                ),
                {"teacher_id": teacher_id, "limit": num},
            )
            course_ids = cursor.scalars().all()
        return list(course_ids)

    async def fetch_teacher_products(self, teacher_id: int) -> list[TeacherProduct]:
        async with self._engine.connect() as conn:
            cursor = await conn.execute(
                sqlalchemy.text(
                    dedent("""
                        SELECT `type` some_type, statused status
                        FROM teac_product
                        WHERE teac_id = :teacher_id
                        ORDER BY `type`
                        LIMIT 100
                    """)
                ),
                {"teacher_id": teacher_id},
            )
            rows = cursor.all()
        return [TeacherProduct.model_validate(row, from_attributes=True) for row in rows]

    async def fetch_same_course_order_ids(self, univ_id: int, course_code: str, order_id: int, limit: int) -> list[int]:
        async with self._engine.connect() as conn:
            cursor = await conn.execute(
                sqlalchemy.text(
                    dedent("""
                        SELECT c.id
                        FROM stud_course c
                        JOIN stud_purchase_order o ON c.id = o.course_id
                        WHERE o.university_id = :univ_id AND o.course_code = :course_code
                        AND o.`type` IN (0, 1, 26, 65, 67, 71)
                        AND c.id >= 200000
                        AND c.id < :order_id
                        ORDER BY c.id DESC
                        LIMIT :limit
                    """)
                ),
                {"univ_id": univ_id, "course_code": course_code, "order_id": order_id, "limit": min(limit, 10)},
            )
            order_ids = cursor.scalars().all()
        return list(order_ids)
