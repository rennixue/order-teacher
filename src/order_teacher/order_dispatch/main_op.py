import logging

from pydantic import ValidationError

from .database import Database
from .match_op import MatchOperation
from .models import *  # noqa: F403
from .order_op import OrderOperation
from .teacher_op import TeacherOperation

logger = logging.getLogger(__name__)


def to_err_msg(exc: Exception) -> str:
    return exc.msg if isinstance(exc, OperationError) else "Unexpected error: " + repr(exc)


class MainOperation:
    def __init__(
        self,
        database: Database,
        order_op: OrderOperation,
        teacher_op: TeacherOperation,
        match_op: MatchOperation,
    ) -> None:
        self._database = database
        self._order_op = order_op
        self._teacher_op = teacher_op
        self._match_op = match_op

    async def run_order(self, order_id: int) -> RunOrderResult:
        err_msg = ""
        order_data = None
        if order_record := await self._database.get_latest_order(order_id):
            assert order_record.data is not None, "null order_record.data should not be selected by sql"
            try:
                order_data = ProcessOrderData.model_validate_json(order_record.data)
            except ValidationError:
                logger.error("run_order %s invalid processed order", order_id)
        else:
            process_order_result = await self.process_order(order_id)
            if process_order_result.ok:
                order_data = process_order_result.unwrap()
            else:
                logger.error("run_order %s cannot process order", order_id)
                err_msg = process_order_result.unwrap_err().msg
        if order_data:
            order_ok = True
            match_order_result = await self.match_order_same_major(order_id, order_data)
        else:
            order_ok = False
            match_order_result = await self.match_order_no_data(order_id)
        if match_order_result.ok:
            pairs = match_order_result.unwrap().pairs
            logger.info("run_order %s ok", order_id)
            return RunOrderResult(ok=True, order_ok=order_ok, data=RunOrderData(pairs=pairs))
        else:
            more_err_msg = match_order_result.unwrap_err().msg
            logger.error("run_order %s fail: %s, %s", order_id, err_msg, more_err_msg)
            return RunOrderResult(ok=False, order_ok=order_ok, err=BaseError(msg=err_msg + ", " + more_err_msg))

    async def process_order(self, order_id: int) -> ProcessOrderResult:
        try:
            if await self._database.select_order_exists(order_id):
                raise OperationError("order exists")
            data = await self._order_op.process(order_id)
            await self._database.insert_order(order_id, data.model_dump_json())
            try:
                await self.refresh_course(data)
            except Exception as exc:
                logger.error("fail to refresh_course by %s: %r", order_id, exc)
        except Exception as exc:
            logger.error("fail to process_order %s: %r", order_id, exc)
            err_msg = to_err_msg(exc)
            try:
                await self._database.insert_order_err(order_id, err_msg)
            except Exception as exc_exc:
                logger.error("fail to insert_order_err %s: %r", order_id, exc_exc)
            return ProcessOrderResult(ok=False, err=BaseError(msg=err_msg))
        else:
            logger.info("process_order %s ok", order_id)
            return ProcessOrderResult(ok=True, data=data)

    async def refresh_order(self, order_id: int) -> ...:
        raise NotImplementedError("refresh order not implemented")

    async def process_teacher_stable(self, teacher_id: int) -> ProcessTeacherStableResult:
        try:
            if await self._database.select_teacher_stable_exists(teacher_id):
                raise OperationError("teacher stable exists")
            data = await self._teacher_op.process_stable(teacher_id)
            edu_major_ids: list[int] = []
            if educations := data.raw_info.educations:
                for edu in educations:
                    if prof_ids := edu.prof_ids:
                        edu_major_ids.extend(prof_ids)
            await self._database.insert_teacher_stable(
                teacher_id, data.model_dump_json(), data.major_ids, list(dict.fromkeys(edu_major_ids))
            )
        except Exception as exc:
            logger.error("fail to process_teacher_stable %s: %r", teacher_id, exc)
            err_msg = to_err_msg(exc)
            try:
                await self._database.insert_teacher_stable_err(teacher_id, err_msg)
            except Exception as exc_exc:
                logger.error("fail to insert_teacher_stable_err %s: %r", teacher_id, exc_exc)
            return ProcessTeacherStableResult(ok=False, err=BaseError(msg=err_msg))
        else:
            logger.info("process_teacher_stable %s ok", teacher_id)
            return ProcessTeacherStableResult(ok=True, data=data)

    async def refresh_teacher_stable(self, teacher_id: int) -> ...:
        raise NotImplementedError("refresh teacher stable not implemented")

    async def process_teacher_unstable(self, teacher_id: int) -> ProcessTeacherUnstableResult:
        try:
            if await self._database.select_teacher_unstable_exists(teacher_id):
                raise OperationError("teacher unstable exists")
            data = await self._teacher_op.process_unstable(teacher_id)
            await self._database.insert_teacher_unstable(teacher_id, data.model_dump_json(), data.major_ids)
        except Exception as exc:
            logger.error("fail to process_teacher_unstable %s: %r", teacher_id, exc)
            err_msg = to_err_msg(exc)
            try:
                await self._database.insert_teacher_unstable_err(teacher_id, err_msg)
            except Exception as exc_exc:
                logger.error("fail to insert_teacher_unstable_err %s: %r", teacher_id, exc_exc)
            return ProcessTeacherUnstableResult(ok=False, err=BaseError(msg=err_msg))
        else:
            logger.info("process_teacher_unstable %s ok", teacher_id)
            return ProcessTeacherUnstableResult(ok=True, data=data)

    async def refresh_teacher_unstable(self, teacher_id: int) -> ProcessTeacherUnstableResult:
        try:
            old_record = await self._database.get_latest_teacher_unstable(teacher_id)
            if old_record is None:
                raise OperationError("teacher unstable does not exist")
            if old_record.data is None:
                old_data = ProcessTeacherUnstableData.fallback(teacher_id)
            else:
                try:
                    old_data = ProcessTeacherUnstableData.model_validate_json(old_record.data)
                except ValidationError as exc:
                    logger.error("fail to validate ProcessTeacherUnstableData from database: %r", exc)
                    old_data = ProcessTeacherUnstableData.fallback(teacher_id)
            data = await self._teacher_op.refresh_unstable(teacher_id, old_data)
            if data is None:
                return ProcessTeacherUnstableResult(ok=False, err=BaseError(msg="nothing or too few to refresh"))
            await self._database.insert_teacher_unstable(teacher_id, data.model_dump_json(), data.major_ids)
        except Exception as exc:
            logger.error("fail to refresh_teacher_unstable %s: %r", teacher_id, exc)
            # do not insert err to database
            return ProcessTeacherUnstableResult(ok=False, err=BaseError(msg=to_err_msg(exc)))
        else:
            logger.info("refresh_teacher_unstable %s ok", teacher_id)
            return ProcessTeacherUnstableResult(ok=True, data=data)

    async def match_order_no_data(self, order_id: int) -> MatchOrderResult:
        try:
            data = await self._match_op.by_no_data(order_id, self)
        except Exception as exc:
            logger.error("fail to match_order_no_data %s: %r", order_id, exc)
            return MatchOrderResult(ok=False, err=BaseError(msg=to_err_msg(exc)))
        else:
            logger.info("match_order_no_data %s ok", order_id)
            return MatchOrderResult(ok=True, data=data)

    async def match_order_same_major(self, order_id: int, order_data: ProcessOrderData) -> MatchOrderResult:
        try:
            data = await self._match_op.by_same_major(order_id, order_data, self)
        except Exception as exc:
            logger.error("fail to match_order_same_major %s: %r", order_id, exc)
            return MatchOrderResult(ok=False, err=BaseError(msg=to_err_msg(exc)))
        else:
            logger.info("match_order_same_major %s ok", order_id)
            return MatchOrderResult(ok=True, data=data)

    async def refresh_course(self, order_data: ProcessOrderData) -> None:
        univ_id = order_data.raw_info.univ_id
        course_code = order_data.raw_info.course_code
        if univ_id is None or not course_code:
            return
        course_code = course_code.strip().upper()
        order_id = order_data.order_id
        order_summary = order_data.summary
        course_name = order_data.raw_info.course_name or ""
        prof_id = order_data.raw_info.prof_id or -1
        spec_id = order_data.raw_info.spec_id
        course_record = await self._database.get_course(univ_id, course_code)
        if course_record is None:
            await self._database.insert_course(
                univ_id, course_code, order_summary, order_id, course_name, prof_id, spec_id
            )
        elif not course_record.summary:
            await self._database.update_course(
                univ_id, course_code, order_summary, order_id, course_name, prof_id, spec_id
            )
        else:
            try:
                new_summary = await self._order_op.try_update_course(course_record.summary, order_summary)
            except Exception as exc:
                logger.error("fail to may_update_course by %s: %r", order_id, exc)
                new_summary = None
            if new_summary is None:
                logger.info("should not update course")
            else:
                logger.info("update course")
            await self._database.update_course(
                univ_id, course_code, new_summary, order_id, course_name, prof_id, spec_id
            )
