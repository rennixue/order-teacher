import asyncio
import logging
import os
import os.path
import shutil
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Iterable, Iterator, cast
from zipfile import ZipFile

import chardet
import httpx
from rarfile import RarFile

from .agent import Agent
from .constants import PROFESSIONS
from .daobi_database import DaobiDatabase
from .models import *  # noqa: F403
from .parse import IParse

logger = logging.getLogger(__name__)


def get_course_dir(base_dir: Path, course_id: int, create: bool) -> Path:
    course_dir = base_dir / str(course_id)
    if create:
        course_dir.mkdir(parents=True, exist_ok=True)
    return course_dir


async def download_coursewares(
    base_dir: Path, records: Iterable[RemoteCourseware]
) -> AsyncIterator[tuple[RemoteCourseware, Path]]:
    async with httpx.AsyncClient() as client:
        for record in records:
            course_dir = get_course_dir(base_dir, record.course_id, create=True)
            ext = record.name.rpartition(".")[-1]
            if ext == "":
                continue
            path = course_dir / f"{record.courseware_id}.{ext}"
            if path.exists():
                yield record, path
                continue
            try:
                async with client.stream("GET", record.url) as stream:
                    with open(path, "wb") as fp:
                        async for chunk in stream.aiter_bytes(65536):
                            fp.write(chunk)
            except httpx.HTTPError as exc:
                logger.error("fail to download %d: %r", record.courseware_id, exc)
                continue
            yield record, path


def path_is_doc(path: Path) -> bool:
    ext = path.suffix.removeprefix(".")
    if ext not in ("pdf", "docx", "doc", "pptx", "ppt"):
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


def guess_encodings(namelist: Iterable[str]) -> tuple[str, str]:
    encode_enc = "utf-8"
    decode_enc = "utf-8"
    try:
        namelist_bytes = "\n".join(namelist).encode("cp437")
    except UnicodeEncodeError:
        pass
    else:
        encode_enc = "cp437"
        attempt = 0
        while attempt < 3:
            attempt += 1
            detected = chardet.detect(namelist_bytes)
            if detected["confidence"] >= 0.9:
                if detected_encoding := detected["encoding"]:
                    decode_enc = detected_encoding
                break
            # some file names are really short
            namelist_bytes = b"\n".join([namelist_bytes] * 2)
    return encode_enc, decode_enc


def extract_zip(src_path: Path, tgt_path: Path) -> None:
    with ZipFile(src_path) as file:
        namelist = file.namelist()
        encode_enc, decode_enc = guess_encodings(namelist)
        for member in file.infolist():
            # HACK start of zipfile._extract_member
            arcname = member.filename.replace("/", os.path.sep)
            if os.path.altsep:
                arcname = arcname.replace(os.path.altsep, os.path.sep)
            arcname = os.path.splitdrive(arcname)[1]
            invalid_path_parts = ("", os.path.curdir, os.path.pardir)
            arcname = os.path.sep.join(x for x in arcname.split(os.path.sep) if x not in invalid_path_parts)
            if os.path.sep == "\\":
                arcname = cast(str, ZipFile._sanitize_windows_name(arcname, os.path.sep))  # type: ignore
            if not arcname and not member.is_dir():
                raise ValueError("Empty filename.")
            # start of added lines
            if encode_enc != decode_enc:
                arcname = arcname.encode(encode_enc).decode(decode_enc)
            # end of added lines
            targetpath = os.path.join(tgt_path, arcname)  # this line changed
            targetpath = os.path.normpath(targetpath)
            upperdirs = os.path.dirname(targetpath)
            if upperdirs and not os.path.exists(upperdirs):
                os.makedirs(upperdirs)
            if member.is_dir():
                if not os.path.isdir(targetpath):
                    os.mkdir(targetpath)
                continue
            with file.open(member, pwd=None) as source, open(targetpath, "wb") as target:  # this line changed
                shutil.copyfileobj(source, target)
            # end of zipfile._extract_member


def extract_rar(src_path: Path, tgt_path: Path) -> None:
    with RarFile(src_path) as file:
        file.extractall(tgt_path)  # type: ignore


async def download_and_extract_coursewares(
    base_dir: Path, records: Iterable[RemoteCourseware]
) -> list[LocalCourseware]:
    candidates: list[LocalCourseware] = []
    async for record, src_path in download_coursewares(base_dir, records):
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
                        LocalCourseware(
                            course_id=record.course_id,
                            courseware_id=record.courseware_id,
                            namelike=path.relative_to(tgt_dir),
                            path=path,
                        )
                    )
            case _:
                # suffix matches, but there are other conditions
                if path_is_doc(src_path):
                    candidates.append(
                        LocalCourseware(
                            course_id=record.course_id,
                            courseware_id=record.courseware_id,
                            namelike=Path(record.name),
                            path=src_path,
                        )
                    )
    return candidates


class OrderOperation:
    def __init__(self, agent: Agent, daobi_database: DaobiDatabase, parse: IParse, tmp_dir: Path) -> None:
        self._agent = agent
        self._daobi_database = daobi_database
        self._parse = parse
        self._base_dir = tmp_dir.absolute()
        self._base_dir.mkdir(parents=True, exist_ok=True)

    async def process(self, order_id: int) -> ProcessOrderData:
        try:
            course_info = await self._daobi_database.fetch_course_info(order_id)
        except Exception as exc:
            logger.error("fetch_course_info fail at %s: %r", order_id, exc)
            raise OperationError("fail to fetch course info")
        if course_info is None:
            logger.error("course %s does not exist", order_id)
            raise OperationError("course does not exist")
        try:
            coursewares = await self._daobi_database.fetch_coursewares(order_id)
        except Exception as exc:
            logger.error("fetch_coursewares fail at %s: %r", order_id, exc)
            coursewares = []
        else:
            if not coursewares:
                logger.warning("course %s has no useful courseware records", order_id)
        if coursewares:
            try:
                processed = await self._process_coursewares(order_id, course_info, coursewares)
            except Exception as exc:
                logger.error("process_coursewares fail at %s: %r", order_id, exc)
                processed = ProcessOrderData.fallback(order_id, course_info)
            finally:
                course_dir = get_course_dir(self._base_dir, order_id, create=False)
                if course_dir.exists():
                    shutil.rmtree(course_dir, ignore_errors=True)
        else:
            processed = ProcessOrderData.fallback(order_id, course_info)
        return processed

    async def refresh(self, order_id: int) -> ...:
        raise NotImplementedError("refresh order not implemented")

    async def _process_coursewares(
        self, order_id: int, info: CourseInfo, remote_records: Iterable[RemoteCourseware]
    ) -> ProcessOrderData:
        downloaded = await download_and_extract_coursewares(self._base_dir, remote_records)
        names = await self._agent.picked_coursewares([it.name for it in downloaded])
        picked = [next(it for it in downloaded if it.name == name) for name in names]
        if len(picked) < 3 and len(downloaded) >= 3:
            for courseware in downloaded[:10]:
                if not any(it.name == courseware.name for it in picked):
                    picked.append(courseware)
        if len(picked) == 0:
            picked = downloaded[:20]
        contents = await self._parse.parse_many([it.path for it in picked])
        parsed = [it.with_content(content) for it, content in zip(picked, contents) if content]
        summaries = await asyncio.gather(*[self._agent.lecture_overview(it.name, it.content) for it in parsed])
        processed = [it.with_summary(summary) for it, summary in zip(parsed, summaries) if summary]
        summary = await self._agent.course_overview([it.summary for it in processed])
        prof_ids = await self._agent.course_profs(summary, PROFESSIONS)
        return ProcessOrderData(
            order_id=order_id,
            summary=summary,
            major_ids=prof_ids,
            processed_paths=[it.name for it in processed],
            raw_info=OrderRawInfo.from_course_info(info),
        )

    async def try_update_course(self, course_summary: str, order_summary: str) -> str | None:
        if not order_summary:
            return None
        if not course_summary:
            return order_summary
        if await self._agent.should_refresh_course(course_summary, order_summary):
            if new_summary := await self._agent.refresh_course(course_summary, order_summary):
                return new_summary
        return None
