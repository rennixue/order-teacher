from __future__ import annotations

import json
import re
from dataclasses import dataclass
from io import StringIO
from typing import TYPE_CHECKING, Any, Self, cast, override

if TYPE_CHECKING:
    from openai import AsyncStream, Stream
    from openai.types.chat import ChatCompletion, ChatCompletionChunk


class StringIOWithPrint(StringIO):
    @override
    def __init__(self, which_part: str) -> None:
        super().__init__()
        self.which_part = which_part

    @override
    def write(self, s: str) -> int:
        if self.tell() == 0:
            print()
            print("-" * 20 + " " + self.which_part + " " + "-" * 20)
        print(s, end="", flush=True)
        return super().write(s)

    @override
    def getvalue(self) -> str:
        if self.tell() != 0:
            print()
        return super().getvalue()


@dataclass(match_args=False)
class ThreeStringIO:
    content: StringIO
    reasoning_content: StringIO
    tool_calls: StringIO

    @classmethod
    def with_print(cls) -> Self:
        return cls(
            content=StringIOWithPrint("Content"),
            reasoning_content=StringIOWithPrint("Reasoning Content"),
            tool_calls=StringIOWithPrint("Tool Calls"),
        )


@dataclass(match_args=False)
class ToolCall:
    id: str
    name: str
    args: dict[str, Any]

    def to_message_item(self) -> dict[str, Any]:
        return {
            "type": "function",
            "id": self.id,
            "function": {
                "name": self.name,
                "arguments": json.dumps(self.args, ensure_ascii=False),
            },
        }


@dataclass(match_args=False)
class OpenAIAnswer:
    # empty is treated as None for content
    content: str | None
    reasoning_content: str | None
    # use str to avoid json.loads when receiving assistant message
    tool_calls: str | None
    finish_reason: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    # zero is not treated as None for reasoning_tokens
    reasoning_tokens: int | None

    @classmethod
    def empty(cls) -> Self:
        return cls(
            content=None,
            reasoning_content=None,
            tool_calls=None,
            finish_reason=None,
            prompt_tokens=None,
            completion_tokens=None,
            reasoning_tokens=None,
        )

    @classmethod
    async def from_astream(cls, stream: AsyncStream[ChatCompletionChunk], ios: ThreeStringIO | None = None) -> Self:
        answer = cls.empty()
        reasoning_content_io = ios.reasoning_content if ios and ios.content else StringIO()
        content_io = ios.content if ios and ios.content else StringIO()
        tool_calls_io = ios.tool_calls if ios and ios.tool_calls else StringIO()
        have_tool_calls = False
        async with stream:
            async for chunk in stream:
                if chunk.choices:
                    choice = chunk.choices[0]
                    if reasoning_content := getattr(choice.delta, "reasoning_content", None):
                        reasoning_content_io.write(reasoning_content)
                    if content := choice.delta.content:
                        content_io.write(content)
                    if tool_calls := choice.delta.tool_calls:
                        for tool_call in tool_calls:
                            # only the first chunk has tool_call.type == "function"
                            if tool_call.type == "function":
                                if have_tool_calls:
                                    tool_calls_io.write("}, ")
                                else:
                                    tool_calls_io.write("[")
                                    have_tool_calls = True
                                if tool_call.id:
                                    tool_calls_io.write('{"id": ' + json.dumps(tool_call.id, ensure_ascii=False))
                                if tool_call.function and tool_call.function.name:
                                    tool_calls_io.write(
                                        ', "name": '
                                        + json.dumps(tool_call.function.name, ensure_ascii=False)
                                        + ', "arguments": '
                                    )
                            if tool_call.function:
                                if arguments := tool_call.function.arguments:
                                    if have_tool_calls:
                                        tool_calls_io.write(arguments)
                    if choice.finish_reason:
                        answer.finish_reason = choice.finish_reason
                if usage := chunk.usage:
                    answer.prompt_tokens = usage.prompt_tokens
                    answer.completion_tokens = usage.completion_tokens
                    if usage.completion_tokens_details:
                        answer.reasoning_tokens = usage.completion_tokens_details.reasoning_tokens
        if have_tool_calls:
            tool_calls_io.write("}]")
        if reasoning_content := reasoning_content_io.getvalue():
            answer.reasoning_content = reasoning_content
        if content := content_io.getvalue():
            answer.content = content
        if tool_calls_str := tool_calls_io.getvalue():
            answer.tool_calls = tool_calls_str
        return answer

    @classmethod
    def from_stream(cls, stream: Stream[ChatCompletionChunk], ios: ThreeStringIO | None = None) -> Self:
        answer = cls.empty()
        reasoning_content_io = ios.reasoning_content if ios and ios.content else StringIO()
        content_io = ios.content if ios and ios.content else StringIO()
        tool_calls_io = ios.tool_calls if ios and ios.tool_calls else StringIO()
        have_tool_calls = False
        with stream:
            for chunk in stream:
                if chunk.choices:
                    choice = chunk.choices[0]
                    if reasoning_content := getattr(choice.delta, "reasoning_content", None):
                        reasoning_content_io.write(reasoning_content)
                    if content := choice.delta.content:
                        content_io.write(content)
                    if tool_calls := choice.delta.tool_calls:
                        for tool_call in tool_calls:
                            # only the first chunk has tool_call.type == "function"
                            if tool_call.type == "function":
                                if have_tool_calls:
                                    tool_calls_io.write("}, ")
                                else:
                                    tool_calls_io.write("[")
                                    have_tool_calls = True
                                if tool_call.id:
                                    tool_calls_io.write('{"id": ' + json.dumps(tool_call.id, ensure_ascii=False))
                                if tool_call.function and tool_call.function.name:
                                    tool_calls_io.write(
                                        ', "name": '
                                        + json.dumps(tool_call.function.name, ensure_ascii=False)
                                        + ', "arguments": '
                                    )
                            if tool_call.function:
                                if arguments := tool_call.function.arguments:
                                    if have_tool_calls:
                                        tool_calls_io.write(arguments)
                    if choice.finish_reason:
                        answer.finish_reason = choice.finish_reason
                if usage := chunk.usage:
                    answer.prompt_tokens = usage.prompt_tokens
                    answer.completion_tokens = usage.completion_tokens
                    if usage.completion_tokens_details:
                        answer.reasoning_tokens = usage.completion_tokens_details.reasoning_tokens
        if have_tool_calls:
            tool_calls_io.write("}]")
        if reasoning_content := reasoning_content_io.getvalue():
            answer.reasoning_content = reasoning_content
        if content := content_io.getvalue():
            answer.content = content
        if tool_calls_str := tool_calls_io.getvalue():
            answer.tool_calls = tool_calls_str
        return answer

    @classmethod
    def from_nonstream(cls, chat_completion: ChatCompletion) -> Self:
        answer = cls.empty()
        if chat_completion.choices:
            choice = chat_completion.choices[0]
            if reasoning_content := getattr(choice.message, "reasoning_content", None):
                answer.reasoning_content = reasoning_content
            if content := choice.message.content:
                answer.content = content
            if tool_calls := choice.message.tool_calls:
                answer_tool_calls: list[str] = []
                for tool_call in tool_calls:
                    if tool_call.type == "function":
                        if tool_call.function:
                            answer_tool_calls.append(
                                '{"id": '
                                + json.dumps(tool_call.id, ensure_ascii=False)
                                + ', "name": '
                                + json.dumps(tool_call.function.name, ensure_ascii=False)
                                + ', "arguments": '
                                + tool_call.function.arguments
                                + "}",
                            )
                if answer_tool_calls:
                    answer.tool_calls = "[" + ", ".join(answer_tool_calls) + "]"
            if choice.finish_reason:
                answer.finish_reason = choice.finish_reason
        if usage := chat_completion.usage:
            answer.prompt_tokens = usage.prompt_tokens
            answer.completion_tokens = usage.completion_tokens
            if usage.completion_tokens_details:
                answer.reasoning_tokens = usage.completion_tokens_details.reasoning_tokens
        return answer

    @property
    def nonempty_content(self) -> str:
        if not self.content:
            raise ValueError("empty content")
        return self.content

    @property
    def nonempty_reasoning_content(self) -> str:
        if not self.reasoning_content:
            raise ValueError("empty reasoning_content")
        return self.reasoning_content

    @property
    def nonempty_tool_calls(self) -> str:
        if not self.tool_calls:
            raise ValueError("empty tool_calls")
        return self.tool_calls

    def content_parse_markdown(self) -> str:
        if self.content is None:
            return ""
        # can be empty content "```markdown\n```"
        content = self.content.strip().removeprefix("```markdown\n").removesuffix("```").strip()
        return content

    def content_parse_json(self) -> Any | None:
        if self.content is None:
            return None
        content = self.content.strip().removeprefix("```json\n").removesuffix("\n```").strip()
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            pass
        if m := re.search(r"(?s)```json\n(.+)\n```", content):
            try:
                return json.loads(m[1])
            except json.JSONDecodeError:
                pass
        return None

    def tool_calls_parse_json(self) -> list[ToolCall] | None:
        if self.tool_calls is None:
            return None
        try:
            tool_call_objs = json.loads(self.tool_calls)
        except json.JSONDecodeError:
            return None
        if not isinstance(tool_call_objs, list):
            return None
        tool_calls: list[ToolCall] = []
        for tool_call in cast(list[object], tool_call_objs):
            if not (
                isinstance(tool_call, dict)
                and "id" in tool_call
                and "name" in tool_call
                and "arguments" in tool_call
                and isinstance(tool_call["id"], str)
                and tool_call["id"]
                and isinstance(tool_call["name"], str)
                and tool_call["name"]
                and isinstance(tool_call["arguments"], dict)
                and all(isinstance(it, str) for it in cast(dict[object, object], tool_call["arguments"].keys()))
            ):
                continue
            tool_calls.append(
                ToolCall(
                    id=tool_call["id"],
                    name=tool_call["name"],
                    args=cast(dict[str, object], tool_call["arguments"]),
                )
            )
        if not tool_calls:
            return None
        return tool_calls
