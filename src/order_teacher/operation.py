import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import assert_never

from .constants import ORDER_TYPES
from .daobi_database import DaobiDatabaseService
from .models import OperationError, OrderTagging, TeacherMatch, TeacherTagging
from .order_dispatch.agent import Agent
from .order_dispatch.daobi_database import DaobiDatabase
from .order_dispatch.database import Database
from .order_dispatch.main_op import MainOperation
from .order_dispatch.match_op import MatchOperation
from .order_dispatch.models import AgentSettings, MoonshotSettings
from .order_dispatch.order_op import OrderOperation
from .order_dispatch.parse import MoonshotParser
from .order_dispatch.teacher_op import TeacherOperation

logger = logging.getLogger(__name__)


class OperationService:
    def __init__(
        self,
        daobi_database_url: str,
        database_url: str,
        moonshot_api_key: str,
        tmp_dir: Path,
        volcengine_api_key: str,
        volcengine_models: dict[str, str],
    ) -> None:
        agent_settings = AgentSettings(api_key=volcengine_api_key, models=volcengine_models)
        agent = Agent(agent_settings, "default")
        database = Database(database_url)
        daobi_database = DaobiDatabase(daobi_database_url)
        moonshot_settings = MoonshotSettings(api_key=moonshot_api_key)
        parser = MoonshotParser(moonshot_settings)
        order_op = OrderOperation(agent, daobi_database, parser, tmp_dir / "order")
        teacher_op = TeacherOperation(agent, database, daobi_database, order_op, parser, tmp_dir / "teacher")
        match_op = MatchOperation(agent, daobi_database, database)
        self._main_op = MainOperation(database, order_op, teacher_op, match_op)
        self._daobi_database = DaobiDatabaseService(daobi_database_url)

    async def run(self, order_id: int) -> list[TeacherMatch]:
        try:
            order_tagging = await self.fetch_order_tagging(order_id)
        except Exception as exc:
            logger.error("fail to fetch_order_tagging for %s: %r", order_id, exc)
            raise OperationError("fail to run_order: fail to fetch_order_tagging")

        result = await self._main_op.run_order(order_id)
        if not result.ok:
            assert result.err
            raise OperationError(f"fail to run_order: {result.err.msg}")
        assert result.data
        pairs = result.data.pairs

        try:
            forbid_teacher_ids = await self._daobi_database.select_forbid(
                [it[0] for it in pairs],
                ORDER_TYPES[order_tagging.order_type]["prod_id"],
            )
        except Exception:
            raise OperationError("fail to run_order: fail to select_forbid")
        order_tagging.t_forbid = forbid_teacher_ids

        triples: list[tuple[int, float, int]] = []
        for teacher_id, score in pairs:
            try:
                teacher_tagging = await self.fetch_teacher_tagging(order_tagging, teacher_id)
            except Exception as exc:
                logger.error("fail to fetch_teacher_tagging for %s: %r", teacher_id, exc)
                tier = 19
            else:
                tier = self.judge_tier(order_tagging, teacher_tagging)
            triples.append((teacher_id, score, tier))

        await self.supplement_teachers(triples, order_tagging)

        triples.sort(key=lambda it: (it[2], -it[1]))
        matches: list[TeacherMatch] = [
            TeacherMatch(no=i, teacher_id=it[0], prof_score=it[1], tier=it[2]) for i, it in enumerate(triples, 1)
        ]
        return matches

    async def fetch_order_tagging(self, order_id: int) -> OrderTagging:
        ret = await self._daobi_database.select_order_basic(order_id)
        assert ret
        student_id, course_code, order_type, univ_id = ret
        assert order_type in ORDER_TYPES, f"invalid {order_type=}"
        parent_type = ORDER_TYPES[order_type]["parent_type"]
        assert parent_type != "other", "order_super_type should not be 'other'"
        assign, unassign = await self._daobi_database.select_order_assign(order_id)
        if course_code and univ_id:
            same_code = await self._daobi_database.select_same_code(order_id, course_code, univ_id)
        else:
            same_code = []
        if student_id:
            same_student = await self._daobi_database.select_same_student(student_id)
        else:
            same_student = []
        return OrderTagging(
            order_id=order_id,
            order_type=order_type,
            parent_type=parent_type,
            student_id=student_id,
            t_assign=assign,
            t_unassign=unassign,
            t_forbid=[],
            t_same_code=same_code,
            t_same_student=same_student,
        )

    async def fetch_teacher_tagging(self, order: OrderTagging, teacher_id: int) -> TeacherTagging:
        if order.student_id:
            has_accident = await self._daobi_database.select_teacher_student_accident(teacher_id, order.student_id)
            client_score = await self._daobi_database.select_teacher_client_score(teacher_id, order.student_id)
        else:
            has_accident = False
            client_score = None
        fail_rate, complain_rate, prod_score = await self._daobi_database.select_teacher_prod_stats_v2(
            teacher_id, order.order_type
        )
        bad_count = await self._daobi_database.select_teacher_bad_count(teacher_id)
        return TeacherTagging(
            teacher_id=teacher_id,
            has_accident=has_accident,
            client_score=client_score,
            fail_rate=fail_rate,
            complain_rate=complain_rate,
            prod_score=prod_score,
            bad_count=bad_count,
        )

    def judge_tier(self, order: OrderTagging, teacher: TeacherTagging) -> int:
        # NOTE assign can be forbid, but is ok
        if teacher.teacher_id in order.t_assign:
            return 0
        if teacher.teacher_id in order.t_unassign:
            return 11
        if teacher.teacher_id in order.t_forbid:
            return 12
        if order.parent_type == "project":
            if teacher.teacher_id in order.t_same_code and teacher.has_accident is False:
                return 1
            if (
                teacher.teacher_id in order.t_same_student
                and (teacher.client_score is not None and teacher.client_score >= 60.0)
                and teacher.has_accident is False
            ):
                return 2
            if (
                (teacher.prod_score is not None and teacher.prod_score >= 60.0)
                and teacher.fail_rate <= 0.05
                and teacher.complain_rate <= 0.05
            ) or (teacher.prod_score is None and teacher.bad_count == 0):
                return 3
            if (
                (teacher.prod_score is not None and teacher.prod_score >= 50.0)
                and teacher.fail_rate <= 0.1
                and teacher.complain_rate <= 0.1
                and teacher.bad_count <= 3
            ) or (teacher.prod_score is None and teacher.bad_count <= 3):
                return 4
            return 10
        elif order.parent_type == "period":
            if teacher.teacher_id in order.t_same_code and teacher.has_accident is False:
                return 1
            if teacher.teacher_id in order.t_same_student and teacher.has_accident is False:
                return 2
            if teacher.complain_rate <= 0.05 and teacher.bad_count == 0:
                return 3
            if teacher.complain_rate <= 0.2 and teacher.bad_count <= 3:
                return 4
            return 10
        else:
            assert_never(order.parent_type)

    async def supplement_teachers(self, mut_triples: list[tuple[int, float, int]], order: OrderTagging) -> None:
        idle_teacher_ids: list[int] = []
        if possible_teacher_ids := [*order.t_assign, *order.t_same_code]:
            try:
                idle_teacher_ids = await self._daobi_database.select_idle_teacher_ids_after(
                    possible_teacher_ids, datetime.now() - timedelta(days=180)
                )
            except Exception:
                pass
        for teacher_id in order.t_assign:
            if not any(it[0] == teacher_id for it in mut_triples):
                mut_triples.append((teacher_id, 0.0, 0))
        for teacher_id in order.t_unassign:
            if not any(it[0] == teacher_id for it in mut_triples):
                mut_triples.append((teacher_id, 0.0, 11))

        same_code_teacher_ids = order.t_same_code
        try:
            same_code_forbid_teacher_ids = await self._daobi_database.select_forbid(
                same_code_teacher_ids,
                ORDER_TYPES[order.order_type]["prod_id"],
            )
        except Exception:
            same_code_forbid_teacher_ids = []
        for teacher_id in same_code_teacher_ids:
            if not any(it[0] == teacher_id for it in mut_triples):
                if teacher_id not in same_code_forbid_teacher_ids:
                    if student_id := order.student_id:
                        try:
                            has_accident = await self._daobi_database.select_teacher_student_accident(
                                teacher_id, student_id
                            )
                        except Exception:
                            has_accident = True
                        if has_accident is False:
                            if teacher_id not in idle_teacher_ids:
                                tier = 1
                            else:
                                tier = 13
                        else:
                            tier = 14
                    else:
                        tier = 14
                else:
                    tier = 12
                mut_triples.append((teacher_id, 0.0, tier))

    async def add_feedback(self, order_id: int, teacher_id: int, choice: int | None, message: str) -> None:
        message = message.strip()
        if not message:
            return
        try:
            await self._main_op.refresh_teacher_feedback(order_id, teacher_id, message)
        except Exception as exc:
            logger.error("fail to refresh_teacher_feedback: %r", exc)
