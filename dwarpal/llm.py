"""One thin client for every model call, over any OpenAI-compatible chat-completions endpoint.

Gemini's free tier, Groq, NVIDIA NIM and a local Ollama all speak this API, so the model is
interchangeable. Nothing here is trusted: callers parse the reply as JSON and validate it, and every
caller has a deterministic fake for tests and offline demos (``FakeLLM``).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


class LLMError(Exception):
    """Provider error, timeout, empty reply, or unparseable output."""


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict
    raw: dict


@dataclass
class ChatResult:
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    assistant_message: dict = field(default_factory=dict)


def parse_json_block(text: str) -> Any:
    """Pull a JSON value out of a model reply that may wrap it in fences or prose."""
    if text is None:
        raise LLMError("empty model reply")
    s = text.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s[3:]
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
        s = s.strip()
    try:
        return json.loads(s)
    except ValueError:
        pass
    starts = [i for i in (s.find("{"), s.find("[")) if i >= 0]
    if not starts:
        raise LLMError("no JSON found in model output")
    start = min(starts)
    end = max(s.rfind("}"), s.rfind("]"))
    if end <= start:
        raise LLMError("no complete JSON value in model output")
    try:
        return json.loads(s[start:end + 1])
    except ValueError as exc:
        raise LLMError(f"model output is not valid JSON: {exc}") from exc


class LLMClient:
    def __init__(self, base_url: str, api_key: str, model: str, timeout_s: int = 60, max_retries: int = 1,
                 client=None):
        if client is None:
            from openai import OpenAI  # imported lazily; tests inject a stub

            client = OpenAI(base_url=base_url, api_key=api_key, timeout=timeout_s, max_retries=max_retries)
        self.client = client
        self.model = model
        self.base_url = base_url

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> ChatResult:
        kwargs: dict[str, Any] = {"model": self.model, "messages": messages}
        if tools:
            kwargs["tools"] = tools  # no tool_choice: some compatible servers reject it
        try:
            resp = self.client.chat.completions.create(**kwargs)
            msg = resp.choices[0].message
        except Exception as exc:
            raise LLMError(f"{type(exc).__name__}: {exc}") from exc
        calls: list[ToolCall] = []
        echoed: list[dict] = []
        for tc in getattr(msg, "tool_calls", None) or []:
            if getattr(tc, "type", "function") != "function" or getattr(tc, "function", None) is None:
                continue
            fn = tc.function
            raw_args = fn.arguments or "{}"
            try:
                args = json.loads(raw_args)
            except ValueError:
                args = {}
            raw = {"id": tc.id, "type": "function", "function": {"name": fn.name, "arguments": raw_args}}
            extra = getattr(tc, "extra_content", None)  # Gemini thought signatures must be echoed back
            if extra:
                raw["extra_content"] = extra
            calls.append(ToolCall(tc.id, fn.name, args if isinstance(args, dict) else {}, raw))
            echoed.append(raw)
        content = getattr(msg, "content", None)
        assistant: dict = {"role": "assistant", "content": content or ""}
        if echoed:
            assistant["tool_calls"] = echoed
        return ChatResult(content, calls, assistant)

    def complete_json(self, system: str, user: str) -> Any:
        r = self.chat([{"role": "system", "content": system}, {"role": "user", "content": user}])
        if not r.content:
            raise LLMError("empty model reply")
        return parse_json_block(r.content)


class FakeLLM:
    """Scripted stand-in. Each response is a string (text reply), a list of ``{"name", "arguments"}`` (tool
    calls), or an exception to raise."""

    def __init__(self, responses: list):
        self.responses = list(responses)
        self.calls: list[dict] = []
        self.model = "fake"

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> ChatResult:
        self.calls.append({"messages": list(messages), "tools": tools})
        if not self.responses:
            raise LLMError("fake: no scripted responses left")
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        if isinstance(r, str):
            return ChatResult(r, [], {"role": "assistant", "content": r})
        calls = []
        for i, c in enumerate(r):
            args = c.get("arguments", {}) or {}
            raw = {"id": f"call_{len(self.calls)}_{i}", "type": "function",
                   "function": {"name": c["name"], "arguments": json.dumps(args)}}
            calls.append(ToolCall(raw["id"], c["name"], args, raw))
        return ChatResult(None, calls, {"role": "assistant", "content": "", "tool_calls": [c.raw for c in calls]})

    def complete_json(self, system: str, user: str) -> Any:
        r = self.chat([{"role": "system", "content": system}, {"role": "user", "content": user}])
        if not r.content:
            raise LLMError("fake: tool call where a JSON reply was expected")
        return parse_json_block(r.content)
