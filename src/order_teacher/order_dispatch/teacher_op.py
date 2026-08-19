import asyncio
import logging
import shutil
from collections.abc import AsyncIterator, Iterable, Iterator
from pathlib import Path

import httpx
from pydantic import ValidationError

from .agent import Agent
from .constants import PROFESSIONS
from .daobi_database import DaobiDatabase
from .database import Database
from .models import *  # noqa: F403
from .order_op import OrderOperation, extract_rar, extract_zip
from .parse import IParse

logger = logging.getLogger(__name__)


def get_teacher_dir(base_dir: Path, teacher_id: int, create: bool) -> Path:
    teacher_dir = base_dir / str(teacher_id)
    if create:
        teacher_dir.mkdir(parents=True, exist_ok=True)
    return teacher_dir


async def download_teacher_files(
    base_dir: Path, records: Iterable[RemoteTeacherFile]
) -> AsyncIterator[tuple[RemoteTeacherFile, Path]]:
    async with httpx.AsyncClient() as client:
        for record in records:
            course_dir = get_teacher_dir(base_dir, record.teacher_id, create=True)
            ext = record.name.rpartition(".")[-1]
            if ext == "":
                continue
            path = course_dir / f"{record.teacher_file_id}.{ext}"
            if path.exists():
                yield record, path
                continue
            try:
                async with client.stream("GET", record.url) as stream:
                    with open(path, "wb") as fp:
                        async for chunk in stream.aiter_bytes(65536):
                            fp.write(chunk)
            except httpx.HTTPError as exc:
                logger.error("fail to download %d: %r", record.teacher_file_id, exc)
                continue
            yield record, path


def path_is_doc(path: Path) -> bool:
    ext = path.suffix.removeprefix(".")
    if ext not in ("pdf", "docx", "doc", "pptx", "ppt", "png", "jpg", "jpeg", "webp"):
        return False
    if ext.startswith(".") or ext.startswith("~") or ext.startswith("$"):
        return False
    return True


def iter_doc_paths(base: Path) -> Iterator[Path]:
    for bpath_str, dnames, fnames in os.walk(base):
        bpath = Path(bpath_str)
        try:
            i = dnames.index("__MACOSX")
        except ValueError:
            pass
        else:
            del dnames[i]
        for fname in fnames:
            fpath = bpath / fname
            if path_is_doc(fpath):
                yield fpath


async def download_and_extract_teacher_files(
    base_dir: Path, records: Iterable[RemoteTeacherFile]
) -> list[LocalTeacherFile]:
    candidates: list[LocalTeacherFile] = []
    async for record, src_path in download_teacher_files(base_dir, records):
        match src_path.suffix:
            case ".zip" | ".rar" as suffix:
                tgt_dir = src_path.with_suffix("")
                if tgt_dir.exists():
                    continue
                if suffix == ".zip":
                    try:
                        await asyncio.to_thread(extract_zip, src_path, tgt_dir)
                    except Exception as exc:
                        logger.error("fail to extract_zip: %r", exc)
                        continue
                if suffix == ".rar":
                    try:
                        await asyncio.to_thread(extract_rar, src_path, tgt_dir)
                    except Exception as exc:
                        logger.error("fail to extract_rar: %r", exc)
                        continue
                for path in iter_doc_paths(tgt_dir):
                    candidates.append(
                        LocalTeacherFile(
                            teacher_id=record.teacher_id,
                            teacher_file_id=record.teacher_file_id,
                            file_type=record.file_type,
                            namelike=path.relative_to(tgt_dir),
                            path=path,
                        )
                    )
            case _:
                # suffix matches, but there are other conditions
                if path_is_doc(src_path):
                    candidates.append(
                        LocalTeacherFile(
                            teacher_id=record.teacher_id,
                            teacher_file_id=record.teacher_file_id,
                            file_type=record.file_type,
                            namelike=Path(record.name),
                            path=src_path,
                        )
                    )
    return candidates


class TeacherOperation:
    def __init__(
        self,
        agent: Agent,
        database: Database,
        daobi_database: DaobiDatabase,
        order_op: OrderOperation,
        parse: IParse,
        tmp_dir: Path,
    ) -> None:
        self._agent = agent
        self._database = database
        self._daobi_database = daobi_database
        self._order_op = order_op
        self._parse = parse
        self._base_dir = tmp_dir.absolute()
        self._base_dir.mkdir(parents=True, exist_ok=True)

    async def process_stable(self, teacher_id: int) -> ProcessTeacherStableData:
        try:
            teacher_info = await self._daobi_database.fetch_teacher_info(teacher_id)
        except Exception as exc:
            logger.error("fetch_teacher_info fail at %s: %r", teacher_id, exc)
            raise OperationError("fail to fetch teacher info")
        if teacher_info is None:
            logger.error("teacher %s does not exist", teacher_id)
            raise OperationError("teacher does not exist")
        try:
            teacher_files = await self._daobi_database.fetch_teacher_files(teacher_id)
        except Exception as exc:
            logger.error("fetch_teacher_files fail at %s: %r", teacher_id, exc)
            teacher_files = []
        else:
            if not teacher_files:
                logger.warning("teacher %s has no useful file records", teacher_id)
        if teacher_files:
            try:
                processed = await self._process_teacher_files(teacher_id, teacher_info, teacher_files)
            except Exception as exc:
                logger.error("process_teacher_files fail at %s: %r", teacher_id, exc)
                processed = ProcessTeacherStableData.fallback(teacher_id, teacher_info)
            finally:
                teacher_dir = get_teacher_dir(self._base_dir, teacher_id, create=False)
                if teacher_dir.exists():
                    shutil.rmtree(teacher_dir, ignore_errors=True)
        else:
            processed = ProcessTeacherStableData.fallback(teacher_id, teacher_info)
        return processed

    async def refresh_stable(self, teacher_id: int) -> ...:
        raise NotImplementedError("refresh teacher stable not implemented")

    async def process_unstable(self, teacher_id: int) -> ProcessTeacherUnstableData:
        try:
            order_ids = await self._daobi_database.fetch_teacher_courses(teacher_id, 20)
        except Exception as exc:
            logger.error("daobi fetch_teacher_courses fail teacher %s: %r", teacher_id, exc)
            raise OperationError("fail to fetch teacher courses")
        order_datas: list[ProcessOrderData] = []
        for order_id in order_ids:
            try:
                order_data = await self._get_order_data(order_id)
            except Exception:
                continue
            if order_data == "bad":
                continue
            if order_data is None:
                try:
                    order_data = await self._order_op.process(order_id)
                    await self._database.insert_order(order_id, order_data.model_dump_json())
                except Exception as exc:
                    logger.error("fail to process_order %s: %r", order_id, exc)
                    err_msg = exc.msg if isinstance(exc, OperationError) else "Unexpected error: " + repr(exc)
                    try:
                        await self._database.insert_order_err(order_id, err_msg)
                    except Exception as exc_exc:
                        logger.error("fail to insert_order_err %s: %r", order_id, exc_exc)
                else:
                    logger.info("process_order %s ok", order_id)
            if order_data is not None:
                if order_data.summary:
                    order_datas.append(order_data)
        if not order_datas:
            logger.warning("teacher %s has no useful orders", teacher_id)
        summary = await self._agent.volatile_overview([it.summary for it in order_datas])
        major_ids = await self._agent.volatile_profs(summary, PROFESSIONS)
        return ProcessTeacherUnstableData(
            teacher_id=teacher_id,
            summary=summary,
            processed_order_ids=[it.order_id for it in order_datas],
            major_ids=major_ids,
        )

    async def refresh_unstable(
        self, teacher_id: int, old: ProcessTeacherUnstableData
    ) -> ProcessTeacherUnstableData | None:
        try:
            order_ids = await self._daobi_database.fetch_teacher_courses(teacher_id, 20)
        except Exception as exc:
            logger.error("daobi fetch_teacher_courses fail teacher %s: %r", teacher_id, exc)
            raise OperationError("fail to fetch teacher courses")
        order_ids = [it for it in order_ids if it not in old.processed_order_ids]
        logger.info("new order_ids: %s", order_ids)
        order_datas: list[ProcessOrderData] = []
        for order_id in order_ids:
            try:
                order_data = await self._get_order_data(order_id)
            except Exception:
                continue
            if order_data == "bad":
                continue
            if order_data is None:
                try:
                    order_data = await self._order_op.process(order_id)
                    await self._database.insert_order(order_id, order_data.model_dump_json())
                except Exception as exc:
                    logger.error("fail to process_order %s: %r", order_id, exc)
                    err_msg = exc.msg if isinstance(exc, OperationError) else "Unexpected error: " + repr(exc)
                    try:
                        await self._database.insert_order_err(order_id, err_msg)
                    except Exception as exc_exc:
                        logger.error("fail to insert_order_err %s: %r", order_id, exc_exc)
                else:
                    logger.info("process_order %s ok", order_id)
            if order_data is not None:
                if order_data.summary:
                    order_datas.append(order_data)
        if not order_datas:
            logger.warning("teacher %s has no useful orders to refresh", teacher_id)
            return None
        if old.summary:
            # fmt: off
            if (
                (len(old.processed_order_ids) >= 10 and len(order_datas) < 4)
                or (len(old.processed_order_ids) >= 5 and len(order_datas) < 2)
            ):
            # fmt: on
                logger.warning("teacher %s has too few orders to refresh", teacher_id)
                return None
            summary = await self._agent.volatile_overview_refresh([it.summary for it in order_datas], old.summary)
        else:
            summary = await self._agent.volatile_overview([it.summary for it in order_datas])
        if old.major_ids:
            major_ids = await self._agent.volatile_profs_refresh(summary, old.major_ids, PROFESSIONS)
        else:
            major_ids = await self._agent.volatile_profs(summary, PROFESSIONS)
        return ProcessTeacherUnstableData(
            teacher_id=teacher_id,
            summary=summary,
            processed_order_ids=[
                *[it.order_id for it in order_datas],
                *old.processed_order_ids,
            ],  # order is desc
            major_ids=major_ids,
        )

    async def _process_teacher_files(
        self, teacher_id: int, info: TeacherInfo, remote_files: Iterable[RemoteTeacherFile]
    ) -> ProcessTeacherStableData:
        downloaded = await download_and_extract_teacher_files(self._base_dir, remote_files)
        contents = await self._parse.parse_many([it.path for it in downloaded])
        parsed = [it.with_content(content) for it, content in zip(downloaded, contents) if content]
        resumes = [it for it in parsed if it.file_type == 0]
        transcripts = [it for it in parsed if it.file_type == 1]
        resume_summaries = await asyncio.gather(*[self._agent.resume_overview(it.content) for it in resumes])
        univ_pairs = [(it.univ_id, it.univ_name) for it in info.edus]
        transcript_summaries = await asyncio.gather(
            *[self._agent.transcript_overview(it.content, univ_pairs) for it in transcripts]
        )
        processed = [
            *[it.with_summary(summary) for it, summary in zip(resumes, resume_summaries)],
            *[it.with_summary(summary) for it, summary in zip(transcripts, transcript_summaries)],
        ]
        summary = TeacherBioSummary.fallback()
        for it in processed:
            if it.subject_areas:
                summary.subject_areas.extend(it.subject_areas)
            if it.skills:
                summary.skills.extend(it.skills)
            if it.grades:
                summary.transcripts.append(
                    TeacherBioSummaryTranscript(
                        univ_id=it.university_id if it.university_id > 0 else None,
                        university=it.university,
                        grades=it.grades,
                    )
                )
        prof_ids = await self._agent.nonvolatile_profs(summary, PROFESSIONS)
        return ProcessTeacherStableData(
            teacher_id=teacher_id,
            profile=TeacherProfile.from_teacher_bio_summary(summary),
            major_ids=prof_ids,
            raw_info=TeacherRawInfo.from_teacher_info(info),
        )

    async def _get_order_data(self, order_id: int) -> ProcessOrderData | None | Literal["bad"]:
        order_data = None
        try:
            order_record = await self._database.get_latest_order(order_id)
        except Exception as exc:
            logger.error("fail to get_latest_order %s: %r", order_id, exc)
        else:
            if order_record:
                if order_record.data is not None:
                    try:
                        order_data = ProcessOrderData.model_validate_json(order_record.data)
                    except ValidationError:
                        logger.error("invalid order data format for %s", order_id)
                else:
                    return "bad"
        return order_data
