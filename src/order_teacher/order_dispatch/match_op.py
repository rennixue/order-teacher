import logging
import math
from collections.abc import Sequence
from typing import TYPE_CHECKING, NamedTuple

from pydantic import ValidationError

from .agent import Agent
from .daobi_database import DaobiDatabase
from .database import Database
from .models import *  # noqa: F403
from .utils import split_paragraphs

if TYPE_CHECKING:
    from .main_op import MainOperation

logger = logging.getLogger(__name__)


class TeacherTriple(NamedTuple):
    teacher_id: int
    stable: ProcessTeacherStableData | None
    unstable: ProcessTeacherUnstableData | None
    feedback: str | None


class MatchOperation:
    def __init__(self, agent: Agent, daobi_database: DaobiDatabase, database: Database) -> None:
        self._agent = agent
        self._daobi_database = daobi_database
        self._database = database

    async def by_no_data(self, order_id: int, main_op: "MainOperation | None" = None) -> MatchOrderData:
        course_info = await self._daobi_database.fetch_course_info(order_id)
        assert course_info is not None
        try:
            teacher_ids = await self._database.select_teacher_ids_by_major_ids([course_info.prof_id], [1, 2, 3], [])
        except Exception as exc:
            logger.error("fail to select_teacher_ids_by_major_ids for order %s: %r", order_id, exc)
            teacher_ids = []
        teacher_datas = [await self._get_teacher_data(it) for it in teacher_ids]
        order_data = ProcessOrderData(
            order_id=order_id,
            summary="(Summary Not Available)",
            major_ids=[],
            processed_paths=[],
            raw_info=OrderRawInfo.from_course_info(course_info),
        )
        pairs = await self._sort_teachers_by_order(teacher_datas, order_data, main_op)
        return MatchOrderData(pairs=pairs)

    async def by_same_major(
        self, order_id: int, order_data: ProcessOrderData, main_op: "MainOperation | None" = None
    ) -> MatchOrderData:
        if order_data.major_ids:
            major_ids = order_data.major_ids
            orig_prof_id = order_data.raw_info.prof_id
            if orig_prof_id is not None:
                if orig_prof_id not in major_ids:
                    major_ids.append(orig_prof_id)
        else:
            orig_prof_id = order_data.raw_info.prof_id
            if orig_prof_id is None:
                logger.warning("order %s has no summarized major ids or raw_info.prod_id", order_id)
                return MatchOrderData(pairs=[])
            major_ids = [orig_prof_id]
        try:
            teacher_ids = await self._database.select_teacher_ids_by_major_ids(major_ids, [1, 2, 3], [])
        except Exception as exc:
            logger.error("fail to select_teacher_ids_by_major_ids for order %s: %r", order_id, exc)
            teacher_ids = []
        teacher_datas = [await self._get_teacher_data(it) for it in teacher_ids]
        pairs = await self._sort_teachers_by_order(teacher_datas, order_data, main_op)
        return MatchOrderData(pairs=pairs)

    async def _get_teacher_data(self, teacher_id: int) -> TeacherTriple:
        stable_data, unstable_data, feedback = None, None, None
        try:
            stable_record = await self._database.get_latest_teacher_stable(teacher_id)
        except Exception as exc:
            logger.error("fail to get_latest_teacher_stable %s: %r", teacher_id, exc)
        else:
            if stable_record:
                assert stable_record.data is not None, "null stable_record.data should not be selected by sql"
                try:
                    stable_data = ProcessTeacherStableData.model_validate_json(stable_record.data)
                except ValidationError:
                    logger.error("invalid teacher stable data format for %s", teacher_id)
        try:
            unstable_record = await self._database.get_latest_teacher_unstable(teacher_id)
        except Exception as exc:
            logger.error("fail to get_latest_teacher_unstable %s: %r", teacher_id, exc)
        else:
            if unstable_record:
                assert unstable_record.data is not None, "null unstable_record.data should not be selected by sql"
                try:
                    unstable_data = ProcessTeacherUnstableData.model_validate_json(unstable_record.data)
                except ValidationError:
                    logger.error("invalid teacher unstable data format for %s", teacher_id)
                feedback = unstable_record.feedback
        return TeacherTriple(teacher_id, stable_data, unstable_data, feedback)

    async def _sort_teachers_by_order(
        self,
        teacher_datas: Sequence[TeacherTriple],
        order_data: ProcessOrderData,
        main_op: "MainOperation | None" = None,
    ) -> list[tuple[int, float]]:
        if not teacher_datas:
            return []
        order_needs = "\n".join((order_data.raw_info.needs or {}).values())
        if order_data.summary:
            order_summary = order_data.summary
        else:
            logger.info("no order summary")
            order_summary = order_data.raw_info.course_name or ""
            if (univ_id := order_data.raw_info.univ_id) and (course_code := order_data.raw_info.course_code):
                try:
                    course_record = await self._database.get_course(univ_id, course_code)
                except Exception:
                    logger.error("fail to get_course")
                else:
                    if course_record and course_record.summary:
                        logger.info("use course summary")
                        order_summary = course_record.summary
                    else:
                        if main_op:
                            order_ids = await self._daobi_database.fetch_same_course_order_ids(
                                univ_id, course_code, order_data.order_id, 5
                            )
                            for it in order_ids:
                                if other_record := await self._database.get_latest_order(it):
                                    try:
                                        data = ProcessOrderData.model_validate_json(other_record.data or "")
                                    except Exception:
                                        logger.error("fail to validate other order data")
                                        result = ProcessOrderResult(ok=False, err=BaseError(msg="..."))
                                    else:
                                        try:
                                            # NOTE this order is never refreshed, since we are in the try-else branch
                                            await main_op.refresh_course(data)
                                        except Exception as exc:
                                            logger.error("fail to refresh_course by %s: %r", data.order_id, exc)
                                        result = ProcessOrderResult(ok=True, data=data)
                                else:
                                    result = await main_op.process_order(it)
                                if result.ok and result.data and result.data.summary:
                                    logger.info("use other order summary")
                                    order_summary = result.data.summary
                                    break
        if not order_summary:
            logger.warning("empty order summary")
            return [(it[0], 1.0) for it in teacher_datas]
        teacher_summary_pairs: list[tuple[int, str, str]] = []
        for teacher_id, stable, unstable, feedback in teacher_datas:
            intro, subject, summary = "", "", ""
            if stable:
                if maybe_str := stable.raw_info.intro:
                    intro = maybe_str.replace("\n", "").strip()
                if maybe_list := stable.profile.subject_areas:
                    subject = "This teacher is good at: " + ", ".join(maybe_list)
            if unstable:
                summary = unstable.summary.strip()
            teacher_summary = intro + " " + subject + " " + summary
            if not teacher_summary:
                continue
            teacher_summary_pairs.append((teacher_id, teacher_summary, feedback or ""))
        teacher_ids_long, teacher_ids_short = await self._match_short_long(
            order_summary, teacher_summary_pairs, order_needs
        )
        teacher_ids_other = [
            it[0] for it in teacher_datas if it[0] not in teacher_ids_long and it[0] not in teacher_ids_short
        ]
        return [
            *[(it, 0.5 + 1 / (1 + math.sqrt(i)) - 0.001) for i, it in enumerate(teacher_ids_long, 1)],
            *[(it, 1 / (1 + math.sqrt(i)) - 0.001) for i, it in enumerate(teacher_ids_short, 1)],
            *[(it, 0.05) for it in teacher_ids_other],
        ]

    async def _match_short_long(
        self, course_summary: str, teacher_summary_pairs: Sequence[tuple[int, str, str]], order_needs: str
    ) -> tuple[list[int], list[int]]:
        if not teacher_summary_pairs:
            return [], []
        course_pair = split_paragraphs(course_summary, 3)[:2]
        course_text = course_pair[0] + "\n" + course_pair[1] + "\n\nSpecial Requirements:\n" + order_needs
        teacher_triples = [
            (teacher_id, (triple := split_paragraphs(teacher_summary, 3))[0], triple[1], feedback)
            for teacher_id, teacher_summary, feedback in teacher_summary_pairs
        ]
        indices_short = await self._agent.short_match(course_text, [it[1].strip() for it in teacher_triples])
        # NOTE remove this line
        indices_short.extend(i for i in range(len(teacher_triples)) if i not in indices_short)
        teacher_triples_short = [teacher_triples[i] for i in indices_short]
        indices_long = await self._agent.long_match(
            course_text,
            [(it[1] + "\n" + it[2] + "\n" + " ".join(it[3].split("\n"))).strip() for it in teacher_triples_short],
        )
        teacher_triples_long = [teacher_triples_short[i] for i in indices_long]
        teacher_triples_other = [
            it for it in teacher_triples_short if not any(other[0] == it[0] for other in teacher_triples_long)
        ]
        return [it[0] for it in teacher_triples_long], [it[0] for it in teacher_triples_other]

    async def refresh_feedback(self, order_id: int, teacher_id: int, message: str) -> None:
        message = message.strip()
        if message == "1" or message == "3":
            return
        if message.startswith("1") or message.endswith("1") or message.startswith("3") or message.endswith("3"):
            return
        if not (message == "2" or await self._agent.cannot_teach(message)):
            return
        order_record = await self._database.get_latest_order(order_id)
        if order_record is None or not order_record.data:
            return
        order_data = ProcessOrderData.model_validate_json(order_record.data)
        course_name = order_data.raw_info.course_name
        if summary := order_data.summary:
            short_summary = split_paragraphs(summary, 3)[0]
        else:
            short_summary = None
        if course_name and short_summary:
            text = f"This teacher cannot teach {course_name}, which is about: {short_summary}"
        elif course_name:
            text = f"This teacher cannot teach {course_name}."
        elif short_summary:
            text = f"This teacher cannot teach a course about: {short_summary}"
        else:
            return
        await self._database.update_teacher_feedback(teacher_id, text)
