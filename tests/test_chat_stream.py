"""The Chat page (Ali, 18 Oct: "answers ... not like chat interaction"): answers type out live, a list of chats,
Regenerate and Edit. The model clients stream when the page is watching; the route sends newline-delimited JSON."""
import json

import httpx
from fastapi.testclient import TestClient

from airtac import llm, memory
from airtac.main import app

client = TestClient(app, base_url="http://localhost")


class Sink:
    def __init__(self):
        self.events = []

    def start(self):
        self.events.append(("start",))

    def token(self, t):
        self.events.append(("token", t))

    def step(self, tool, status):
        self.events.append(("step", tool, status))


def _events(r) -> list[dict]:
    return [json.loads(line) for line in r.text.splitlines() if line.strip()]


def _stream(body: dict) -> list[dict]:
    r = client.post("/api/chat/stream", json={"session_id": "c1", **body})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/x-ndjson")
    return _events(r)


def test_ollama_streams_words_then_returns_the_whole_answer():
    lines = [{"message": {"content": "Hel"}}, {"message": {"content": "lo **Ali**"}},
             {"message": {"content": ""}, "done": True, "prompt_eval_count": 7, "eval_count": 3}]
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, content="\n".join(json.dumps(x) for x in lines).encode())

    model = llm.OllamaLLM("http://ollama", "m", http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    sink = Sink()
    token = llm.STREAM.set(sink)
    try:
        reply = model.chat([{"role": "user", "content": "hi"}], [])
    finally:
        llm.STREAM.reset(token)
    assert seen["body"]["stream"] is True
    assert reply.text == "Hello **Ali**" and reply.tool_call is None
    assert sink.events == [("start",), ("token", "Hel"), ("token", "lo **Ali**")]
    assert llm.OllamaLLM("http://ollama", "m", http_client=httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"message": {"content": "whole"}})))).chat([{"role": "user", "content": "x"}], []).text == "whole"


def test_ollama_stream_tool_call_and_error_answer():
    lines = [{"message": {"content": "", "tool_calls": [{"function": {"name": "weather_now", "arguments": {"day": "today"}}}]}},
             {"done": True}]
    model = llm.OllamaLLM("http://ollama", "m", http_client=httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, content="\n".join(json.dumps(x) for x in lines).encode()))))
    token = llm.STREAM.set(Sink())
    try:
        reply = model.chat([{"role": "user", "content": "weather"}], [{"type": "function", "function": {"name": "weather_now"}}])
        assert reply.tool_call.name == "weather_now" and reply.tool_call.args == {"day": "today"}
        bad = llm.OllamaLLM("http://ollama", "m", http_client=httpx.Client(transport=httpx.MockTransport(
            lambda r: httpx.Response(500, json={"error": "boom"}))))
        try:
            bad.chat([{"role": "user", "content": "x"}], [])
            raise AssertionError("should fail")
        except llm.LLMUnavailable as e:
            assert "boom" in str(e)
    finally:
        llm.STREAM.reset(token)


def test_openai_compatible_server_sent_events_join_text_and_tool_calls():
    chunks = [{"choices": [{"delta": {"content": "Two "}}]}, {"choices": [{"delta": {"content": "parts"}}]},
              {"choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 2}}]
    body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, content=body.encode(), headers={"content-type": "text/event-stream"})

    model = llm.OpenAICompatLLM("http://x/v1", "m", "k", http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    sink = Sink()
    token = llm.STREAM.set(sink)
    try:
        assert model.chat([{"role": "user", "content": "hi"}], []).text == "Two parts"
        assert seen["body"]["stream"] is True and sink.events[1:] == [("token", "Two "), ("token", "parts")]
        calls = [{"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c9", "function": {"name": "web_", "arguments": '{"q'}}]}}]},
                 {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"name": "search", "arguments": '": "gpu"}'}}]}}]}]
        sse = "".join(f"data: {json.dumps(c)}\n\n" for c in calls) + "data: [DONE]\n\n"
        tool_model = llm.OpenAICompatLLM("http://x/v1", "m", "k", http_client=httpx.Client(transport=httpx.MockTransport(
            lambda r: httpx.Response(200, content=sse.encode()))))
        reply = tool_model.chat([{"role": "user", "content": "search"}], [{"type": "function", "function": {"name": "web_search"}}])
        assert reply.tool_call.name == "web_search" and reply.tool_call.args == {"q": "gpu"} and reply.tool_call.id == "c9"
        limited = llm.OpenAICompatLLM("http://x/v1", "m", "k", max_429_retries=0, http_client=httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(429, json={"error": "rate limit"}))))
        try:
            limited.chat([{"role": "user", "content": "x"}], [])
            raise AssertionError("should fail")
        except llm.QuotaExceeded:
            pass
    finally:
        llm.STREAM.reset(token)


def test_chat_route_streams_a_reply_with_its_text_and_ids():
    events = _stream({"message": "hello there"})
    done = events[-1]
    assert done["type"] == "done"
    reply = done["reply"]
    assert reply["text"] and reply["spoken"] and reply["message_id"] and reply["user_message_id"]
    msgs = memory.thread("c1")
    assert [m["role"] for m in msgs] == ["user", "assistant"] and msgs[1]["content"] == reply["text"]


def test_tool_steps_are_streamed():
    events = _stream({"message": "Draw a binary tree 8 4 12"})
    steps = [e for e in events if e["type"] == "step"]
    assert steps and steps[0]["status"] == "running" and steps[-1]["status"] in ("ok", "error")


def test_regenerate_and_edit_replace_the_answer():
    _stream({"message": "first question"})
    _stream({"message": "second question"})
    assert len(memory.thread("c1")) == 4
    again = _stream({"regenerate": True})[-1]["reply"]
    msgs = memory.thread("c1")
    assert len(msgs) == 4 and msgs[2]["content"] == "second question" and msgs[3]["id"] == again["message_id"]
    first_id = msgs[0]["id"]
    _stream({"message": "first, edited", "edit_from": first_id})
    msgs = memory.thread("c1")
    assert [m["content"] for m in msgs if m["role"] == "user"] == ["first, edited"]
    r = client.post("/api/chat/stream", json={"session_id": "c1", "message": "x", "edit_from": 999999})
    assert r.status_code == 404
    assert client.post("/api/chat/stream", json={"session_id": "empty", "regenerate": True}).status_code == 400
    assert client.post("/api/chat/stream", json={"session_id": "c1", "message": "  "}).status_code == 422


def test_conversation_list_rename_and_open():
    _stream({"message": "plan my week"})
    client.post("/api/chat", json={"message": "hi", "session_id": "app:schedule"})
    chats = client.get("/api/conversations").json()["chats"]
    assert [c["id"] for c in chats] == ["c1"] and chats[0]["title"].startswith("plan my week")
    assert client.post("/api/conversations/c1/rename", json={"title": "  Week   plan "}).json()["title"] == "Week plan"
    assert client.get("/api/conversations").json()["chats"][0]["title"] == "Week plan"
    one = client.get("/api/conversations/c1").json()
    assert one["title"] == "Week plan" and len(one["messages"]) == 2
    client.post("/api/history/delete", json={"chats": ["c1"]})
    assert client.get("/api/conversations").json()["chats"] == []
    assert memory.kv_get("chat_title", "c1") == ""


def test_long_chat_answers_are_kept_whole_and_read_out_short():
    from airtac import agent
    long = "## Plan\n\nFirst sentence here. Second one. Third one.\n\n```python\nprint(1)\n```\n" + "More. " * 400
    r = agent._finish("c2", long, [], [], mode="chat")
    assert r.text.startswith("## Plan") and "print(1)" in r.text
    assert r.spoken == "Plan First sentence here. Second one." and "print" not in r.spoken
    assert memory.thread("c2")[-1]["content"] == r.text
    assert llm.system_prompt([], "chat").startswith("You are AIRTAC") and "markdown" in llm.system_prompt([], "chat")
    assert "no markdown" in llm.system_prompt([])
