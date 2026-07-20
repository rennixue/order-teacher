import asyncio
import os
import re
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, TypeAdapter
from pydantic_core import from_json, to_json

T = TypeVar("T")


def load_data(typ: type[T], path: str | os.PathLike[str]) -> T:
    path = Path(path)
    obj = from_json(path.read_bytes())
    if issubclass(typ, BaseModel):
        model = typ.model_validate(obj)
    else:
        model = TypeAdapter(typ).validate_python(obj)
    return model


async def load_data_async(typ: type[T], path: str | os.PathLike[str]) -> T:
    return await asyncio.to_thread(load_data, typ, path)


def save_data(obj: Any, path: str | os.PathLike[str]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(to_json(obj, indent=2))


async def save_data_async(obj: Any, path: str | os.PathLike[str]) -> None:
    return await asyncio.to_thread(save_data, obj, path)


def insert_blank_lines(content: str) -> str:
    content = re.sub(r"\n+1\. ", "\n\n1. ", content)
    # content = re.sub(r"\n+(\|[^\n]+\|\n\| *:?---+)", r"\n\n\1", content)
    content = re.sub(r"\n+((?:\|[^\n]*\|\n)+)\n*", r"\n\n\1\n\n", content)
    return content


def split_paragraphs(text: str, num: int) -> tuple[str, ...]:
    text = text.strip()
    text = re.sub(r"\n+", "\n", text)
    if num <= 1:
        return (text,)
    paras = text.split("\n", num - 1)
    if len(paras) < num:
        paras.extend([""] * (num - len(paras)))
    return tuple(paras)
