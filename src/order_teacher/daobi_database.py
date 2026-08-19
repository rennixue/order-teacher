import logging
from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from .models import *  # noqa: F403

logger = logging.getLogger(__name__)


class DaobiDatabaseService:
    def __init__(self, url: str) -> None:
        self._engine = create_async_engine(url, pool_size=2, pool_recycle=600)

    async def is_healthy(self) -> bool:
        try:
            async with self._engine.connect() as conn:
                cursor = await conn.execute(text("SELECT 1"))
                assert cursor.scalar_one() == 1
        except Exception as exc:
            logger.error("fail to connect to daobi_database: %r", exc)
            return False
        else:
            return True

    async def select_order_type(self, order_id: int) -> Literal[-1] | int | None:
        async with self._engine.connect() as conn:
            cursor = await conn.execute(
                text("SELECT `type` FROM stud_purchase_order WHERE course_id = :order_id LIMIT 1"),
                {"order_id": order_id},
            )
            row = cursor.one_or_none()
        if row is None:
            return None
        order_type = row[0]
        if order_type is None:
            return -1
        return order_type

    async def select_order_basic(self, order_id: int) -> tuple[int | None, str | None, int, int | None] | None:
        async with self._engine.connect() as conn:
            cursor = await conn.execute(
                text("""
                    SELECT sc.user_id, spo.course_code, spo.`type`, spo.university_id
                    FROM stud_course sc
                    JOIN stud_purchase_order spo ON spo.course_id = sc.id
                    WHERE sc.id = :order_id
                    LIMIT 1
                """),
                {"order_id": order_id},
            )
            row = cursor.one_or_none()
        if row is None:
            return None
        return tuple(row)

    async def select_order_assign(self, order_id: int) -> tuple[list[int], list[int]]:
        async with self._engine.connect() as conn:
            cursor = await conn.execute(
                text("""
                    SELECT teac_id, `type`
                    FROM stud_course_appoint_teac
                    WHERE course_id = :order_id
                    AND `type` IN (1, 2)
                    AND delete_flag = 0
                    LIMIT 100
                """),
                {"order_id": order_id},
            )
            rows = cursor.all()
        assign: list[int] = []
        unassign: list[int] = []
        for teacher_id, kind in rows:
            if kind == 1:
                assign.append(teacher_id)
            elif kind == 2:
                unassign.append(teacher_id)
        return assign, unassign

    async def select_same_student(self, student_id: int) -> list[int]:
        async with self._engine.connect() as conn:
            cursor = await conn.execute(
                text("""
                    SELECT DISTINCT `to`.user_id
                    FROM teac_order `to`
                    JOIN stud_course sc ON sc.id = `to`.course_id
                    WHERE sc.user_id = :student_id
                    AND `to`.delete_flag = 0
                    LIMIT 100
                """),
                {"student_id": student_id},
            )
            scalars = cursor.scalars().all()
        return list(scalars)

    async def select_same_code(self, order_id: int, course_code: str, univ_id: int) -> list[int]:
        async with self._engine.connect() as conn:
            cursor = await conn.execute(
                text("""
                    SELECT DISTINCT `to`.user_id
                    FROM teac_order `to`
                    JOIN stud_purchase_order spo ON spo.course_id = `to`.course_id
                    WHERE spo.university_id = :univ_id AND spo.course_code = :course_code
                    AND `to`.course_id <> :order_id
                    AND `to`.delete_flag = 0
                    LIMIT 100
                """),
                {"order_id": order_id, "univ_id": univ_id, "course_code": course_code},
            )
            scalars = cursor.scalars().all()
        return list(scalars)

    async def select_forbid(self, teacher_ids: list[int], prod_type: int) -> list[int]:
        async with self._engine.connect() as conn:
            cursor = await conn.execute(
                text("""
                    SELECT DISTINCT tu.id
                    FROM teac_user tu
                    LEFT JOIN teac_product tp_all ON tp_all.teac_id = tu.id AND tp_all.type = 0 AND tp_all.deleteed = 0
                    LEFT JOIN teac_product tp_one ON tp_one.teac_id = tu.id AND tp_one.type = :prod_type AND tp_one.deleteed = 0
                    WHERE tu.id IN :teacher_ids
                    AND (
                      tu.statused IN (1, 4, 5)
                      OR (tp_all.statused IN (2, 3) OR tp_one.statused IN (2, 3))
                    )
                    LIMIT 200
                """).bindparams(teacher_ids=tuple(teacher_ids)),
                {"prod_type": prod_type},
            )
            scalars = cursor.scalars().all()
        return list(scalars)

    async def select_teacher_student_accident(self, teacher_id: int, student_id: int) -> bool:
        stmt1 = """
-- 4a. 事故数量
-- 查询指定讲师在指定客户下的事故单数量（queryType=1 场景）
SELECT order_id
FROM (
    -- 来源1：销售分数评价差评 (evaluate_type=2, evaluate_res=2)
    SELECT sof.order_id
    FROM sys_order_feedback sof
    INNER JOIN stud_course sc ON sc.id = sof.order_id
    WHERE sof.delete_flag = 0
      AND sof.no_feedback_flag = 0
      AND sof.evaluate_type = 2
      AND sof.evaluate_res = 2
      AND sc.teacher_id = :teacher_id
      AND sc.user_id = :student_id
    UNION ALL
    -- 来源2：销售普通评价差评 (evaluate_type=1, evaluate_res=2)
    SELECT sof.order_id
    FROM sys_order_feedback sof
    INNER JOIN stud_course sc ON sc.id = sof.order_id
    WHERE sof.delete_flag = 0
      AND sof.no_feedback_flag = 0
      AND sof.evaluate_type = 1
      AND sof.evaluate_res = 2
      AND sc.teacher_id = :teacher_id
      AND sc.user_id = :student_id
    UNION ALL
    -- 来源3：课堂评价差评 (praise=0)
    SELECT scl.cd_id AS order_id
    FROM stud_classroom scl
    INNER JOIN stud_classroom_evaluate sce ON scl.id = sce.room_id
    INNER JOIN stud_course sc ON scl.cd_id = sc.id
    INNER JOIN stud_purchase_order spo ON sc.id = spo.course_id
    INNER JOIN teac_order tod ON tod.course_id = sc.id AND tod.user_id = scl.teacher_id
    WHERE scl.delete_flag = 0
      AND sce.delete_flag = 0
      AND tod.delete_flag = 0
      AND sce.praise = 0
      AND scl.teacher_id = :teacher_id
      AND sc.user_id = :student_id
    UNION ALL
    -- 来源4：售后讲师责任单 (responsible=7)
    SELECT saso.course_id AS order_id
    FROM sys_after_sales_order saso
    INNER JOIN sys_after_sales_order_resp sasor ON sasor.order_id = saso.id
    INNER JOIN stud_course sc ON sc.id = saso.course_id
    WHERE saso.delete_flag = 0
      AND sasor.delete_flag = 0
      AND sasor.responsible = 7
      AND sasor.responsible_person_id IS NOT NULL
      AND sasor.responsible_person_id = :teacher_id
      AND sc.user_id = :student_id
) t
LIMIT 1
        """
        stmt2 = """
-- 4b. 事故(对应更换讲师次数)记录数 (来源 TeacherRecommendationMapper.queryTeacherResponsibility)
SELECT result.teacherId
FROM sys_after_sales_order sasoa
INNER JOIN
    (SELECT changeOrder.teacherId,
        GROUP_CONCAT(changeOrder.courseId) courseIds,
        max( sasoa.after_sales_check_time ) AS after_sales_check_time
    FROM sys_after_sales_order sasoa
    INNER JOIN
       (SELECT sc.teacher_id AS teacherId,
        sc.id AS courseId
       FROM stud_course sc
       WHERE sc.statused = 131072
             AND sc.teacher_id IS NOT null
             AND sc.his_change_order_ids IS NOT null
             -- 直接指定对应的客户和讲师ID
             AND sc.user_id = :student_id
             AND sc.teacher_id = :teacher_id
             ) changeOrder
          ON changeOrder.courseId = sasoa.course_id
       WHERE sasoa.after_sales_result = 4
             AND sasoa.after_sales_status = 2
             AND sasoa.delete_flag = 0
             AND sasoa.after_sales_check_time IS NOT NULL
       GROUP BY  changeOrder.teacherId ) result
       ON FIND_IN_SET(sasoa.course_id, result.courseIds) > 0
       AND result.after_sales_check_time = sasoa.after_sales_check_time
LIMIT 1
        """
        async with self._engine.connect() as conn:
            cursor = await conn.execute(
                text(stmt1),
                {"teacher_id": teacher_id, "student_id": student_id},
            )
            rows = cursor.all()
            if rows:
                return True
            cursor = await conn.execute(
                text(stmt2),
                {"teacher_id": teacher_id, "student_id": student_id},
            )
            rows = cursor.all()
            if rows:
                return True
        return False

    async def select_teacher_client_score(self, teacher_id: int, student_id: int) -> float | None:
        async with self._engine.connect() as conn:
            cursor = await conn.execute(
                text("""
                    SELECT
                        SUM(
                            sof.score 
                            * (sc.teac_fixed_price / 100.0)
                            * CASE WHEN (
                                (sct.code = 853 AND sct.currency = 'CNY')
                                OR (sct.code = 852 AND sct.currency = 'HKD')
                                OR (sct.code = 1 AND sct.currency = 'USD')
                                OR (sct.code = 1 AND sct.currency = 'CAD')
                                OR (sct.code = 86 AND sct.currency = 'CNY')
                            ) THEN 0.75 ELSE 1 END
                        ) / NULLIF(SUM(sc.teac_fixed_price / 100.0), 0.0) AS customerAvgScore
                    FROM stud_course sc
                    JOIN sys_order_feedback sof ON sof.order_id = sc.id
                    JOIN stud_user su ON su.user_id  = sc.user_id
                    JOIN sys_country sct ON sct.id = su.country_id
                    WHERE sc.teacher_id = :teacher_id
                    AND sc.user_id = :student_id
                    AND sof.evaluate_type = 2
                    AND sof.score IS NOT NULL
                    AND sof.delete_flag = 0
                    LIMIT 1
                """),
                {"teacher_id": teacher_id, "student_id": student_id},
            )
            scalar = cursor.scalar_one_or_none()
        return scalar

    async def select_teacher_prod_stats(self, teacher_id: int, order_type: int) -> tuple[float, float, float | None]:
        if order_type in (0, 1, 26):
            order_types = (0, 1, 26)
            calc_order_types = (0, 1)
        elif order_type in (65, 67, 71):
            order_types = (65, 67, 71)
            calc_order_types = (65, 71)
        else:
            return 0.0, 0.0, None
        async with self._engine.connect() as conn:
            cursor = await conn.execute(
                text("""
                    SELECT
                      order_type,
                      coalesce(finish_order_num, 0) order_total_count,
                      coalesce(fail_rate, 0.0) fail_rate,
                      coalesce(complaint_rate, 0.0) complaint_rate,
                      coalesce(avg_score, 0.0) avg_score
                    FROM ds_teacher
                    WHERE teacher_id = :teacher_id
                    AND order_type IN :order_types
                    AND delete_flag = 0
                    LIMIT 10
                """).bindparams(order_types=order_types),
                {"teacher_id": teacher_id},
            )
            rows = [it._asdict() for it in cursor.all()]  # pyright: ignore[reportPrivateUsage]
        mapping = {it["order_type"]: it for it in rows}
        fail_total, complaint_total, avg_score_total, count = 0.0, 0.0, 0.0, 0
        for it in calc_order_types:
            if v := mapping.get(it):
                fail_total += float(v["fail_rate"]) * v["order_total_count"]
                complaint_total += float(v["complaint_rate"]) * v["order_total_count"]
                avg_score_total += float(v["avg_score"]) * v["order_total_count"]
                count += v["order_total_count"]
        if count > 0:
            fail_rate, complaint_rate, avg_score = fail_total / count, complaint_total / count, avg_score_total / count
        else:
            fail_rate, complaint_rate, avg_score = 0.0, 0.0, None
        return fail_rate, complaint_rate, avg_score

    async def select_teacher_prod_stats_v2(self, teacher_id: int, order_type: int) -> tuple[float, float, float | None]:
        if order_type in (0, 1, 26):
            calc_order_type = 1001
        elif order_type in (65, 67, 71):
            calc_order_type = 1002
        else:
            return 0.0, 0.0, None
        async with self._engine.connect() as conn:
            cursor = await conn.execute(
                text("""
                    SELECT
                      coalesce(fail_rate, 0.0) fail_rate,
                      coalesce(complaint_rate, 0.0) complaint_rate,
                      avg_score
                    FROM ds_teacher
                    WHERE teacher_id = :teacher_id
                    AND data_type = 101
                    AND order_type = :order_type
                    AND delete_flag = 0
                    LIMIT 1
                """),
                {"teacher_id": teacher_id, "order_type": calc_order_type},
            )
            row = cursor.one_or_none()
        if row is None:
            return 0.0, 0.0, None
        if row[2] is None:
            avg_score = None
        else:
            row_2_float = float(row[2])
            if row_2_float == 0.0 or row_2_float < 1e-3:
                avg_score = None
            else:
                avg_score = row_2_float
        return float(row[0]), float(row[1]), avg_score

    async def select_teacher_bad_count(self, teacher_id: int) -> int:
        async with self._engine.connect() as conn:
            cursor = await conn.execute(
                text("""
                    SELECT count(DISTINCT tt.id)
                    FROM teac_tag tt
                    LEFT JOIN sys_tag_config stc ON tt.tag_config_id = stc.id
                    LEFT JOIN sys_tag_child stch ON stc.tag_child_id = stch.id
                    WHERE tt.teac_id = :teacher_id
                    AND tt.deleteed = 0
                    AND stch.tag_id = 1 /* 核心 */
                    AND stc.`type` = 3 /* 一般(差) */
                    AND stc.deleteed = 0
                    LIMIT 1
                """),
                {"teacher_id": teacher_id},
            )
            scalar = cursor.scalar_one_or_none()
        if scalar is None:
            return 0
        return scalar
