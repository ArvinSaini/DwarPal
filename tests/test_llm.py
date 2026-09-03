from types import SimpleNamespace

import pytest

from agentgate.llm import FakeLLM, LLMClient, LLMError, parse_json_block


def test_parse_json_block_variants():
    assert parse_json_block('{"a": 1}') == {"a": 1}
    assert parse_json_block('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_block('Sure! Here you go:\n[{"id": "x"}]\nHope it helps.') == [{"id": "x"}]
    assert parse_json_block('```\n[1, 2]\n```') == [1, 2]
    with pytest.raises(LLMError):
        parse_json_block("no json here")
    with pytest.raises(LLMError):
        parse_json_block("{broken")


def test_fake_llm_returns_text_and_tool_calls():
    llm = FakeLLM(["hello", [{"name": "list_products", "arguments": {"q": "shoes"}}, {"name": "done"}]])
    r = llm.chat([{"role": "user", "content": "hi"}])
    assert r.content == "hello" and r.tool_calls == [] and r.assistant_message["content"] == "hello"
    r = llm.chat([{"role": "user", "content": "go"}], tools=[{"type": "function"}])
    assert [c.name for c in r.tool_calls] == ["list_products", "done"]
    assert r.tool_calls[0].arguments == {"q": "shoes"} and r.tool_calls[1].arguments == {}
    assert r.assistant_message["tool_calls"][0]["function"]["name"] == "list_products"
    assert len(llm.calls) == 2 and llm.calls[1]["tools"] == [{"type": "function"}]


def test_fake_llm_complete_json_and_exhaustion():
    llm = FakeLLM(['```json\n{"category": "footwear"}\n```'])
    assert llm.complete_json("sys", "user") == {"category": "footwear"}
    with pytest.raises(LLMError):
        llm.complete_json("sys", "user")
    with pytest.raises(LLMError):
        FakeLLM([LLMError("provider down")]).chat([])
    with pytest.raises(LLMError):
        FakeLLM([[{"name": "x"}]]).complete_json("s", "u")  # tool call where JSON text was expected


class StubOpenAI:
    """Mimics the openai client surface used by LLMClient: client.chat.completions.create(...)."""

    def __init__(self, message=None, error=None):
        self.kwargs = None
        me = self

        class Completions:
            def create(self, **kwargs):
                me.kwargs = kwargs
                if error:
                    raise error
                return SimpleNamespace(choices=[SimpleNamespace(message=message)])

        self.chat = SimpleNamespace(completions=Completions())


def test_llm_client_parses_tool_calls_and_echoes_extra_content():
    fn = SimpleNamespace(name="propose", arguments='{"items": [1]}')
    call = SimpleNamespace(id="call_1", type="function", function=fn, extra_content={"thought": "sig"})
    custom = SimpleNamespace(id="call_2", type="custom", function=None)
    msg = SimpleNamespace(content=None, tool_calls=[call, custom])
    llm = LLMClient("https://example.test/v1", "key", "model-x", client=StubOpenAI(message=msg))
    r = llm.chat([{"role": "user", "content": "x"}], tools=[{"type": "function", "function": {"name": "propose"}}])
    assert [c.name for c in r.tool_calls] == ["propose"] and r.tool_calls[0].arguments == {"items": [1]}
    assert r.assistant_message["tool_calls"][0]["extra_content"] == {"thought": "sig"}
    assert "tool_choice" not in llm.client.kwargs and llm.client.kwargs["model"] == "model-x"


def test_llm_client_wraps_provider_errors_and_bad_arguments():
    llm = LLMClient("https://example.test/v1", "key", "m", client=StubOpenAI(error=RuntimeError("429")))
    with pytest.raises(LLMError):
        llm.chat([])
    fn = SimpleNamespace(name="propose", arguments="{not json")
    msg = SimpleNamespace(content="", tool_calls=[SimpleNamespace(id="c", type="function", function=fn)])
    llm = LLMClient("https://example.test/v1", "key", "m", client=StubOpenAI(message=msg))
    assert llm.chat([]).tool_calls[0].arguments == {}
    msg = SimpleNamespace(content="", tool_calls=None)
    llm = LLMClient("https://example.test/v1", "key", "m", client=StubOpenAI(message=msg))
    with pytest.raises(LLMError):
        llm.complete_json("s", "u")
