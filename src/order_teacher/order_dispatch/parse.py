import asyncio
import json
import logging
from abc import ABC, abstractmethod
from asyncio import Semaphore
from collections.abc import Sequence
from typing import cast

from openai import AsyncOpenAI

from .models import MoonshotSettings, StrPath

logger = logging.getLogger(__name__)


class IParse(ABC):
    @abstractmethod
    async def parse_one(self, in_path: StrPath) -> str: ...

    async def parse_many(self, in_paths: Sequence[StrPath]) -> list[str]:
        return await asyncio.gather(*[self.parse_one(it) for it in in_paths])


class MoonshotParser(IParse):
    def __init__(self, settings: MoonshotSettings) -> None:
        self._client = AsyncOpenAI(base_url=settings.base_url, api_key=settings.api_key)
        self._semaphore = Semaphore(settings.concurrency)

    async def parse_one(self, in_path: StrPath) -> str:
        text_content = ""
        try:
            async with self._semaphore:
                file = await self._client.files.create(file=in_path, purpose="file-extract")  # pyright: ignore[reportArgumentType]
                try:
                    content = await self._client.files.content(file_id=file.id)
                    content_text = cast(object, content.text)
                    if isinstance(content_text, str):
                        text_content = json.loads(content_text)["content"]
                    else:
                        logger.warning("not text: %s", type(content_text))
                finally:
                    await self._client.files.delete(file_id=file.id)
        except Exception as exc:
            logger.error("Moonshot fail to parse '%s': %r", in_path, exc)
            return ""
        if text_content == "":
            logger.error("Moonshot empty content '%s'", in_path)
        return text_content
