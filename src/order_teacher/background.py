import asyncio
import inspect
import sys
import uuid
from collections.abc import Callable
from types import TracebackType
from typing import Any, Self


class MyBackgroundTasks:
    def __init__(self, timeout: float | None = None) -> None:
        self._mapping: dict[str, asyncio.Task[Any]] = {}
        self._timeout = timeout

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self, exc_ty: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> bool | None:
        await self.wait()

    def create_task[**P](self, func: Callable[P, Any], *args: P.args, **kwargs: P.kwargs) -> None:
        async def wrapper(token: str) -> None:
            try:
                if inspect.iscoroutinefunction(func):
                    await func(*args, **kwargs)
                else:
                    await asyncio.to_thread(func, *args, **kwargs)
            finally:
                self._mapping.pop(token)

        while True:
            token = uuid.uuid4().hex
            if token in self._mapping:
                continue
            self._mapping[token] = asyncio.create_task(wrapper(token))
            break

    async def wait(self) -> None:
        tasks = self._mapping.values()
        if not tasks:
            return
        _, pending = await asyncio.wait(tasks, timeout=self._timeout)
        if pending:
            print("{!r} exit with {} tasks not done".format(self, len(pending)), file=sys.stderr, flush=True)
            for it in pending:
                it.cancel()
