import asyncio
import functools
import logging
import random
from asyncio import Semaphore
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Awaitable, Callable, ParamSpec, TypeVar, cast

from openai import AsyncOpenAI, AsyncStream, RateLimitError
from openai.types.chat import ChatCompletion, ChatCompletionChunk
from tenacity import retry, stop_after_attempt

from .answer import OpenAIAnswer, ThreeStringIO
from .constants import ProfessionDict
from .models import *  # noqa: F403
from .template import JinjaTemplateManager

logger = logging.getLogger(__name__)


P = ParamSpec("P")
R = TypeVar("R")


def with_fallback(func: Callable[P, Awaitable[R]], fallback: Callable[P, R]) -> Callable[P, Awaitable[R]]:
    @functools.wraps(func)
    async def inner(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            result = await func(*args, **kwargs)
        except Exception as exc:
            logger.error("agent %s fail: %r", func.__name__, exc)
            result = fallback(*args, **kwargs)
        return result

    return inner


class BaseAgent:
    def __init__(self, settings: AgentSettings, default_model: str) -> None:
        self._client = AsyncOpenAI(base_url=settings.base_url, api_key=settings.api_key, timeout=30.0)
        self._models = settings.models
        self._default_model = self._models.get(default_model, default_model)
        self._semaphore = Semaphore(settings.concurrency)
        mngr = JinjaTemplateManager(Path(__file__).parent / "prompts", trim_blocks=True, lstrip_blocks=True)
        self._templates = mngr.load_all_templates()

    async def _ask(self, messages: str | list[Any], **kwargs: Any) -> OpenAIAnswer:
        if isinstance(messages, str):
            messages = [{"role": "user", "content": messages}]
        verbose = int(kwargs.pop("verbose", 0))  # may be bool
        if verbose >= 2:
            print("-" * 60)
            print(messages[-1]["content"])
        model: str = kwargs.pop("model", self._default_model)
        model = self._models.get(model, model)
        stream: bool = kwargs.pop("stream", True)
        if stream:
            if stream_options := kwargs.get("stream_options"):
                if "include_usage" not in stream_options:
                    kwargs["stream_options"]["include_usage"] = True
            else:
                kwargs["stream_options"] = {"include_usage": True}
        max_completion_tokens: int = kwargs.pop("max_completion_tokens", 8192)
        kwargs.pop("max_tokens", None)
        temperature: float = kwargs.pop("temperature", 0.1)
        if extra_body := kwargs.get("extra_body"):
            if thinking := extra_body.get("thinking"):
                if thinking["type"] == "enabled" and "reasoning_effort" not in kwargs:
                    kwargs["reasoning_effort"] = "low"
            else:
                kwargs["extra_body"]["thinking"] = {"type": "disabled"}
        else:
            if kwargs.get("reasoning_effort"):
                kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
            else:
                kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
        async with self._semaphore:
            attempt = 0
            while True:
                try:
                    maybe_stream = await self._client.chat.completions.create(
                        messages=messages,
                        model=model,
                        stream=stream,
                        max_completion_tokens=max_completion_tokens,
                        temperature=temperature,
                        **kwargs,
                    )
                except RateLimitError as exc:
                    logger.warning("rate limited %r", exc)
                    attempt += 1
                    if attempt == 2:
                        raise exc
                    await asyncio.sleep(10 * 2**attempt + random.random())
                    continue
                if stream:
                    answer = await OpenAIAnswer.from_astream(
                        cast(AsyncStream[ChatCompletionChunk], maybe_stream),
                        ThreeStringIO.with_print() if verbose >= 1 else None,
                    )
                else:
                    answer = OpenAIAnswer.from_nonstream(cast(ChatCompletion, maybe_stream))
                break
        if verbose >= 1:
            print("-" * 60)
            print(
                "Usage: prompt={}, completion={}, reasoning={}".format(
                    answer.prompt_tokens, answer.completion_tokens, answer.reasoning_tokens
                )
            )
        return answer


class Agent(BaseAgent):
    @retry(reraise=True, stop=stop_after_attempt(2))
    async def _picked_coursewares(self, names: Sequence[str]) -> list[str]:
        if not names:
            return []
        user_msg = self._templates["course/picked_coursewares"].render(names=names)
        answer = await self._ask(user_msg, stream=False, max_completion_tokens=256)
        output = IdsSchema.model_validate(answer.content_parse_json())
        return [names[i - 1] for i in output.ids if 1 <= i <= len(names)]

    picked_coursewares = with_fallback(_picked_coursewares, lambda self, names: [])

    async def _lecture_overview(self, name: str, text: str) -> str:
        if not text:
            return ""
        user_msg = self._templates["course/lecture_overview"].render(name=name, text=text)
        answer = await self._ask(user_msg, max_completion_tokens=2048)
        return answer.nonempty_content

    lecture_overview = with_fallback(_lecture_overview, lambda self, name, text: "")

    @retry(reraise=True, stop=stop_after_attempt(2))
    async def _course_overview(self, texts: Sequence[str]) -> str:
        if not texts:
            return ""
        user_msg = self._templates["course/course_overview"].render(texts=texts)
        answer = await self._ask(user_msg, max_completion_tokens=4096)
        return answer.nonempty_content

    course_overview = with_fallback(_course_overview, lambda self, texts: "")

    @retry(reraise=True, stop=stop_after_attempt(2))
    async def _course_profs(self, summary: str, professions: Sequence[ProfessionDict]) -> list[int]:
        if not summary:
            return []
        user_msg = self._templates["course/course_profs"].render(summary=summary, professions=professions)
        answer = await self._ask(user_msg, stream=False, max_completion_tokens=256)
        output = IdsSchema.model_validate(answer.content_parse_json())
        return [professions[i - 1]["id"] for i in output.ids if 1 <= i <= len(professions)]

    course_profs = with_fallback(_course_profs, lambda self, summary, professions: [])

    @retry(reraise=True, stop=stop_after_attempt(2))
    async def _resume_overview(self, text: str) -> ResumeOverviewSchema:
        if not text:
            return ResumeOverviewSchema.fallback()
        user_msg = self._templates["teacher/resume_overview"].render(text=text)
        answer = await self._ask(user_msg, max_completion_tokens=1024)
        return ResumeOverviewSchema.model_validate(answer.content_parse_json())

    resume_overview = with_fallback(_resume_overview, lambda self, text: ResumeOverviewSchema.fallback())

    @retry(reraise=True, stop=stop_after_attempt(2))
    async def _transcript_overview(self, text: str, univ_pairs: list[tuple[int, str]]) -> TranscriptOverviewSchema:
        if not text:
            return TranscriptOverviewSchema.fallback()
        user_msg = self._templates["teacher/transcript_overview"].render(text=text, univ_pairs=univ_pairs)
        answer = await self._ask(user_msg, max_completion_tokens=4096)
        return TranscriptOverviewSchema.model_validate(answer.content_parse_json())

    transcript_overview = with_fallback(
        _transcript_overview, lambda self, text, univ_pairs: TranscriptOverviewSchema.fallback()
    )

    @retry(reraise=True, stop=stop_after_attempt(2))
    async def _nonvolatile_profs(self, summary: TeacherBioSummary, professions: Sequence[ProfessionDict]) -> list[int]:
        if summary.is_empty():
            return []
        # Do not render in prompt text file.
        texts: list[str] = [
            "Subject Areas:",
            *[f"- {it}" for it in summary.subject_areas],
            "",
            "Skills:",
            *[f"- {it}" for it in summary.skills],
            "",
            "Course Grades:",
            *[f"- {it.course_name}: {it.grade}" for transcript in summary.transcripts for it in transcript.grades],
        ]
        user_msg = self._templates["teacher/nonvolatile_profs"].render(text="\n".join(texts), professions=professions)
        answer = await self._ask(user_msg, stream=False, max_completion_tokens=256)
        output = IdsSchema.model_validate(answer.content_parse_json())
        return [professions[i - 1]["id"] for i in output.ids if 1 <= i <= len(professions)]

    nonvolatile_profs = with_fallback(_nonvolatile_profs, lambda self, summary, professions: [])

    @retry(reraise=True, stop=stop_after_attempt(2))
    async def _volatile_overview(self, texts: Sequence[str]) -> str:
        if not texts:
            return ""
        user_msg = self._templates["teacher/volatile_overview"].render(texts=texts)
        answer = await self._ask(user_msg, max_completion_tokens=4096)
        return answer.nonempty_content

    volatile_overview = with_fallback(_volatile_overview, lambda self, texts: "")

    @retry(reraise=True, stop=stop_after_attempt(2))
    async def _volatile_overview_refresh(self, texts: Sequence[str], old: str) -> str:
        if not texts:
            return ""
        user_msg = self._templates["teacher/volatile_overview_refresh"].render(texts=texts, old_summary=old)
        answer = await self._ask(user_msg, max_completion_tokens=4096)
        return answer.nonempty_content

    volatile_overview_refresh = with_fallback(_volatile_overview_refresh, lambda self, texts, old: "")

    @retry(reraise=True, stop=stop_after_attempt(2))
    async def _volatile_profs(self, summary: str, professions: Sequence[ProfessionDict]) -> list[int]:
        if not summary:
            return []
        user_msg = self._templates["teacher/volatile_profs"].render(summary=summary, professions=professions)
        answer = await self._ask(user_msg, stream=False, max_completion_tokens=256)
        output = IdsSchema.model_validate(answer.content_parse_json())
        return [professions[i - 1]["id"] for i in output.ids if 1 <= i <= len(professions)]

    volatile_profs = with_fallback(_volatile_profs, lambda self, summary, professions: [])

    @retry(reraise=True, stop=stop_after_attempt(2))
    async def _volatile_profs_refresh(
        self, summary: str, old: Sequence[int], professions: Sequence[ProfessionDict]
    ) -> list[int]:
        if not summary:
            return []
        old_professions = [it for it in professions if it["id"] in old]
        user_msg = self._templates["teacher/volatile_profs_refresh"].render(
            summary=summary, old_professions=old_professions, professions=professions
        )
        answer = await self._ask(user_msg, stream=False, max_completion_tokens=256)
        output = IdsSchema.model_validate(answer.content_parse_json())
        return [professions[i - 1]["id"] for i in output.ids if 1 <= i <= len(professions)]

    volatile_profs_refresh = with_fallback(_volatile_profs_refresh, lambda self, summary, old, professions: [])

    @retry(reraise=True, stop=stop_after_attempt(2))
    async def _short_match(self, course: str, teachers: Sequence[str]) -> list[int]:
        if not course or not teachers:
            return []
        user_msg = self._templates["match/short_match"].render(course=course, teachers=teachers)
        answer = await self._ask(user_msg, max_completion_tokens=1024)
        output = IdsSchema.model_validate(answer.content_parse_json())
        return [i - 1 for i in output.ids if 1 <= i <= len(teachers)]

    short_match = with_fallback(_short_match, lambda self, course, teachers: [])

    @retry(reraise=True, stop=stop_after_attempt(2))
    async def _long_match(self, course: str, teachers: Sequence[str]) -> list[int]:
        if not course or not teachers:
            return []
        user_msg = self._templates["match/long_match"].render(course=course, teachers=teachers)
        answer = await self._ask(user_msg, max_completion_tokens=1024)
        output = IdsSchema.model_validate(answer.content_parse_json())
        return [i - 1 for i in output.ids if 1 <= i <= len(teachers)]

    long_match = with_fallback(_long_match, lambda self, course, teachers: [])

    @retry(reraise=True, stop=stop_after_attempt(2))
    async def _should_refresh_course(self, course: str, order: str) -> bool:
        if not course or not order:
            return False
        user_msg = self._templates["course/should_refresh_course"].render(course=course, order=order)
        answer = await self._ask(user_msg, stream=False, max_completion_tokens=16)
        output = answer.nonempty_content.strip().strip('"').lower().startswith("yes")
        return output

    should_refresh_course = with_fallback(_should_refresh_course, lambda self, course, order: False)

    @retry(reraise=True, stop=stop_after_attempt(2))
    async def _refresh_course(self, course: str, order: str) -> str:
        if not course or not order:
            return ""
        user_msg = self._templates["course/refresh_course"].render(course=course, order=order)
        answer = await self._ask(user_msg, max_completion_tokens=4096)
        output = answer.nonempty_content.strip()
        return output

    refresh_course = with_fallback(_refresh_course, lambda self, course, order: "")
