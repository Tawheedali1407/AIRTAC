"""The brain behind AIRTAC Advance. Three interchangeable providers, one method:
chat(messages, tools) -> LLMReply (text, or one tool call).

- OllamaLLM: an open model running on this laptop through Ollama's own API (default).
- OpenAICompatLLM: any OpenAI-compatible server (LM Studio, llama.cpp, vLLM, or a hosted one).
- MockLLM: keyword rules contributed by the plugins. No model at all: tests and quick checks.
"""
import json
import logging
import re
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import date

import httpx

from airtac import config
from airtac import tools as registry  # not `tools`: chat() has a parameter by that name

log = logging.getLogger("airtac.llm")

SYSTEM_PROMPT = (
    "You are AIRTAC, {name}'s private assistant running on their own laptop. "
    "Reply in 1 to 3 short sentences that sound natural when read aloud: no markdown, no lists, "
    "no web addresses. If there is more to show, say \"details are on screen\". "
    "Use at most one tool per turn, and only when it helps. Never make up tool results, dates or numbers. "
    "Today is {today}."
)


# The Chat page (Ali, 18 Oct: "answers ... not like chat interaction"): full answers, written to be read, with
# markdown. The voice style above stays for Home, the phone and the wake word.
CHAT_PROMPT = (
    "You are AIRTAC, {name}'s private assistant running on their own laptop, in a chat window. "
    "Answer the way a good chat assistant does: complete, clear and well organised. Use markdown when it helps "
    "(short headings, lists, tables, code blocks with the language). Keep simple answers short. "
    "Use at most one tool per turn, and only when it helps. Never make up tool results, dates, numbers or sources; "
    "say when you don't know. Today is {today}."
)

# Live typing: the Chat page's request sets a sink here; the model clients then ask for a streamed answer and pass
# each piece on. start() is called at the beginning of every model call (a tool step or a failover begins again).
STREAM: ContextVar = ContextVar("airtac_stream", default=None)


PERSONAS = {"friendly": "persona_v1.txt", "neutral": "persona_neutral.txt"}


def persona(tone: str | None = None) -> str:
    """The shared persona file (prompts/), picked by Settings > Tone. AIRTAC 1 and the phone read the same files."""
    if tone is None:
        try:
            from plugins.settings import get
            tone = get("tone")
        except Exception:
            tone = "friendly"
    path = config.ROOT / "prompts" / PERSONAS.get(tone, PERSONAS["friendly"])
    try:
        return path.read_text(encoding="utf-8").strip().replace("{name}", config.USER_NAME or "the user")
    except OSError:
        return ""


def system_prompt(facts: list[str], mode: str = "voice") -> str:
    text = (CHAT_PROMPT if mode == "chat" else SYSTEM_PROMPT).format(name=config.USER_NAME or "the user", today=date.today().strftime("%A %d %B %Y"))
    style = persona()
    if style:
        text += "\n\n" + style
    try:
        from airtac import memory
        about = memory.profile_text()
    except Exception:   # no database yet (first start, some tests)
        about = ""
    if about:
        text += f"\n\nAbout the user (from Settings, About you): {about}"
    if facts:
        text += "\n\nThings the user told you earlier:\n" + "\n".join(f"- {f}" for f in facts)
    return text


@dataclass
class ToolCall:
    name: str
    args: dict
    id: str = "call_1"


@dataclass
class LLMReply:
    text: str = ""
    tool_call: ToolCall | None = None  # one per turn keeps small models on track


def _free_gb() -> float:
    try:
        import psutil
        return psutil.virtual_memory().available / 1e9
    except Exception:
        return 99.0


def _length(tier: str, max_tokens: int) -> int:
    """The answer length the plugins allow (the tokens plugin's Short / Normal / Long). Never skips here."""
    from airtac import plugins  # imported here: plugins load after this module
    return plugins.brain_gate(tier, max_tokens) or max_tokens


def _report(tier: str, model: str, prompt: int, completion: int) -> None:
    from airtac import plugins, providers
    providers.TURN["prompt"] = providers.TURN.get("prompt", 0) + max(0, int(prompt or 0))       # for the chat header
    providers.TURN["completion"] = providers.TURN.get("completion", 0) + max(0, int(completion or 0))
    plugins.report_usage(tier, model, prompt, completion)


class LLMUnavailable(Exception):
    """The brain can't be reached. The message is shown to the user as is."""


class QuotaExceeded(LLMUnavailable):
    """A service's free limit or credits ran out (HTTP 429 or 402). The failover router moves on."""

    def __init__(self, message: str, retry_after: float = 0):
        super().__init__(message)
        self.retry_after = retry_after


class TooSlow(LLMUnavailable):
    """A chat call passed its deadline (no first word in time, or the whole answer took too long). It is an
    LLMUnavailable, so the failover router can try the next brain; the chat shows the message otherwise."""


class Cancelled(Exception):
    """Stop was pressed (or the page went away). Not an LLMUnavailable: no other brain is tried."""


# ---------------------------------------------------------------- the chat's guard: deadlines and Stop
# Ali, 20 Oct: "chat is sometimes unresponsive". A model call used to wait on the socket (120 s per read, no limit
# overall), and Stop only closed the browser's side, so Ollama kept the CPU busy and the next question queued behind
# it. Now the HTTP call runs in a helper thread and the chat waits on it a quarter second at a time: Stop or a
# deadline returns at once and closes the stream, which tells Ollama (or the cloud) to stop generating.

@dataclass
class Guard:
    cancel: "threading.Event"
    first_token: float = 45.0
    total: float = 90.0
    # Brains that were too slow earlier in this turn ("ollama", or a cloud tier): later calls to them in the same turn
    # (a plugin summarising the chat after the answer, say) fail at once instead of each waiting out the deadline again.
    stuck: set = field(default_factory=set)


GUARD: ContextVar = ContextVar("airtac_guard", default=None)
_TICK = 0.25


def new_guard() -> Guard:
    import threading
    return Guard(cancel=threading.Event(), first_token=config.CHAT_FIRST_TOKEN_SECONDS, total=config.CHAT_TOTAL_SECONDS)


class _Clock:
    """One model call's deadlines: the first word by `first_token`, the whole answer by `total` (from the start)."""

    def __init__(self, guard: Guard, who: str, brain: str = ""):
        self.guard, self.who, self.brain, self.start, self.first = guard, who, brain or who, time.monotonic(), False
        if self.brain in guard.stuck:
            raise TooSlow(f"{who} was stuck a moment ago in this answer, so it isn't asked again now.")

    def _stuck(self, text: str) -> TooSlow:
        self.guard.stuck.add(self.brain)
        return TooSlow(text)

    def check(self) -> float:
        """Raise when Stop was pressed or a deadline passed; else the seconds left to wait for the next piece."""
        if self.guard.cancel.is_set():
            raise Cancelled("Stopped.")
        spent = time.monotonic() - self.start
        if not self.first and spent >= self.guard.first_token:
            raise self._stuck(f"{self.who} didn't start answering within {self.guard.first_token:.0f} seconds (on this PC "
                              "the model may still be loading, or another app is using the memory).")
        if spent >= self.guard.total:
            raise self._stuck(f"{self.who} took longer than {self.guard.total:.0f} seconds, so I stopped it.")
        return min(_TICK, (self.guard.total if self.first else self.guard.first_token) - spent)


def _guarded(clock: _Clock, fn):
    """Run fn() (the request, up to its response headers) in a helper thread; wait here with Stop and deadlines."""
    import queue
    import threading
    box: queue.Queue = queue.Queue()
    gone = threading.Event()

    def run():
        try:
            r = fn()
        except BaseException as e:   # passed back to the waiting thread
            box.put((False, e))
            return
        if gone.is_set():            # nobody waits any more: close it, so the server stops generating
            try:
                r.close()
            except Exception:
                pass
            return
        box.put((True, r))
    threading.Thread(target=run, daemon=True, name="airtac-llm-call").start()
    while True:
        try:
            wait = clock.check()
        except BaseException:
            gone.set()
            raise
        try:
            ok, value = box.get(timeout=max(0.01, wait))
        except queue.Empty:
            continue
        if ok:
            return value
        raise value


def _guarded_lines(clock: _Clock, r):
    """r.iter_lines() read in a helper thread; lines come out here, with Stop and deadlines checked between them."""
    import queue
    import threading
    box: queue.Queue = queue.Queue()
    end = object()

    def pump():
        try:
            for line in r.iter_lines():
                box.put(line)
            box.put(end)
        except BaseException as e:
            box.put(e)
    threading.Thread(target=pump, daemon=True, name="airtac-llm-stream").start()
    try:
        while True:
            try:
                item = box.get(timeout=max(0.01, clock.check()))
            except queue.Empty:
                continue
            if item is end:
                return
            if isinstance(item, BaseException):
                raise item
            yield item
    finally:
        r.close()   # Stop, a deadline or the end: closing the stream makes the server stop generating


class _QuietSink:
    """A chat call with no page watching (Home's /api/chat) still streams, so the guard can see progress."""

    def start(self):
        pass

    def token(self, piece):
        pass

    def step(self, tool, status):
        pass


# ---------------------------------------------------------------- MockLLM

HELP_TEXT = ("I'm running without a model right now, on keyword rules. Ask about your schedule, GitHub, "
             "LeetCode, papers, practice problems, study questions, diagrams, or documents.")


class MockLLM:
    """Pretends to be the model with the plugins' keyword rules, so AIRTAC runs with no model."""

    model = "keyword rules"

    def chat(self, messages: list[dict], tools: list[dict], max_tokens: int = 600) -> LLMReply:
        last = messages[-1]
        if last["role"] == "tool":
            # Second call of a turn: the tool already ran, so answer with its spoken text.
            return LLMReply(text=json.loads(last["content"])["spoken"])
        text = last["content"]
        low = text.lower().replace("’", "'")  # phone keyboards send curly apostrophes
        available = {t["function"]["name"] for t in tools}
        for rule in registry.RULES:
            if rule.tool in available and any(re.search(rf"\b{re.escape(k)}\b", low) for k in rule.keywords):
                return LLMReply(tool_call=ToolCall(rule.tool, rule.args(text, messages)))
        return LLMReply(text=HELP_TEXT)


# ---------------------------------------------------------------- Ollama (native API)

def missing_model_text(wanted: str, fallback: str, installed: list[str] | None) -> str:
    """Why the PC's model can't answer when Ollama runs but doesn't have it. An empty list usually means Ollama is
    reading another models folder (OLLAMA_MODELS not picked up), not that the models are gone."""
    if installed == []:
        return (f"Ollama is running but sees no models, so {wanted} can't answer. If you moved them to "
                "F:\\Ollama\\models, set OLLAMA_MODELS to that folder and restart Ollama.")
    other = f" (nor the backup {fallback})" if fallback and fallback != wanted else ""
    return f"Ollama doesn't have {wanted}{other}. Run: ollama pull {wanted}"


class OllamaLLM:
    """Ollama's /api/chat. Native rather than its OpenAI-compatible endpoint, because only the native
    one can switch thinking off (a 4B model thinking on a laptop CPU can take a minute)."""

    def __init__(self, base_url: str | None = None, model: str | None = None, http_client: httpx.Client | None = None,
                 think: bool | None = None):
        # Read config here, not as default arguments, so a changed setting takes effect.
        self.base_url, self.model = (base_url or config.LLM_BASE_URL).rstrip("/"), model or config.LLM_MODEL
        self.client = http_client or httpx.Client(timeout=config.LLM_TIMEOUT)
        self.think = config.LLM_THINK if think is None else think
        self.send_think = True   # dropped if this model refuses the "think" option
        self.send_tools = True   # dropped if this model can't call tools (MedGemma, for one)
        self.tokens_used = 0

    @staticmethod
    def _convert(messages: list[dict]) -> list[dict]:
        """OpenAI-style history -> Ollama's: tool-call arguments are objects, tool results name the tool."""
        out, names = [], {}
        for m in messages:
            if m["role"] == "assistant" and m.get("tool_calls"):
                calls = []
                for c in m["tool_calls"]:
                    names[c.get("id", "")] = c["function"]["name"]
                    args = c["function"]["arguments"]
                    calls.append({"function": {"name": c["function"]["name"],
                                               "arguments": json.loads(args) if isinstance(args, str) else args}})
                out.append({"role": "assistant", "content": m.get("content") or "", "tool_calls": calls})
            elif m["role"] == "tool":
                out.append({"role": "tool", "content": m["content"], "tool_name": names.get(m.get("tool_call_id", ""), "")})
            else:
                out.append({"role": m["role"], "content": m["content"]})
        return out

    def _post(self, body: dict):
        """POST /api/chat. Streamed when the Chat page is watching; an error answer is read whole either way."""
        url = f"{self.base_url}/api/chat"
        if not body.get("stream"):
            return self.client.post(url, json=body)
        r = self.client.send(self.client.build_request("POST", url, json=body), stream=True)
        if r.status_code >= 400:
            r.read()
            r.close()
        return r

    def _read_stream(self, r, sink, clock: "_Clock | None" = None) -> dict:
        """Ollama's streamed lines -> the same shape as its one-piece answer, passing the words on as they come.
        With a clock (the chat path), Stop and the deadlines are checked between lines."""
        content, calls, last = [], [], {}
        sink.start()
        try:
            for line in (_guarded_lines(clock, r) if clock else r.iter_lines()):
                if not line.strip():
                    continue
                try:
                    chunk = json.loads(line)
                except ValueError:
                    continue
                if chunk.get("error"):
                    raise LLMUnavailable(f"Ollama stopped with an error: {str(chunk['error'])[:200]}")
                msg = chunk.get("message") or {}
                if clock and (msg.get("content") or msg.get("thinking") or msg.get("tool_calls")):
                    clock.first = True   # it started answering: from here only the total deadline counts
                if msg.get("content"):
                    content.append(msg["content"])
                    sink.token(msg["content"])
                calls.extend(msg.get("tool_calls") or [])
                if chunk.get("done"):
                    last = chunk
                    break
        except httpx.TimeoutException:
            raise LLMUnavailable("The model took too long. Try a shorter question, or a smaller model in config/airtac.toml.")
        except httpx.HTTPError:
            raise LLMUnavailable("Ollama stopped answering in the middle of a reply. Please try again.")
        finally:
            r.close()
        return {**last, "message": {"content": "".join(content), "tool_calls": calls}}

    def _installed(self) -> list[str] | None:
        """The models Ollama can see (`ollama list`), or None when it doesn't answer."""
        try:
            r = self.client.get(f"{self.base_url}/api/tags", timeout=5)
            if r.status_code != 200:
                return None
            return [m.get("name", "") for m in r.json().get("models", [])]
        except (httpx.HTTPError, ValueError, AttributeError):
            return None

    def chat(self, messages: list[dict], tools: list[dict], max_tokens: int = 600) -> LLMReply:
        max_tokens = _length("ollama", max_tokens)
        guard = GUARD.get()   # set on the chat path: deadlines, Stop, a lighter model setting
        body = {"model": self.model, "messages": self._convert(messages), "stream": False,
                "keep_alive": config.CHAT_KEEP_ALIVE if guard else "10m",
                "options": {"num_ctx": config.CHAT_NUM_CTX if guard else config.LLM_NUM_CTX, "temperature": 0.3,
                            "num_predict": max_tokens}}
        if tools and self.send_tools:
            body["tools"] = tools
        if self.send_think:
            body["think"] = self.think
        sink = STREAM.get()
        if sink is None and guard is not None:
            sink = _QuietSink()      # always streamed in a chat, so a slow model shows progress and can be stopped
        if sink is not None:
            body["stream"] = True
        clock = _Clock(guard, f"The model on this PC ({self.model})", "ollama") if guard else None
        send = (lambda: _guarded(clock, lambda: self._post(body))) if clock else (lambda: self._post(body))
        try:
            r = send()
            for _ in range(2):  # a model may lack a thinking switch or tool support: drop it and ask again
                low = r.text.lower() if r.status_code == 400 else ""
                if "think" in low and self.send_think:
                    self.send_think = False
                    body.pop("think", None)
                elif "tool" in low and "tools" in body:
                    self.send_tools = False
                    body.pop("tools", None)
                else:
                    break
                r = send()
        except httpx.ConnectError:
            raise LLMUnavailable("Ollama isn't running. Open Ollama from the Start menu (or run 'ollama serve'), then ask again.")
        except httpx.TimeoutException:
            raise LLMUnavailable("The model took too long. Try a shorter question, or a smaller model in config/airtac.toml.")
        if r.status_code == 404:
            if config.LLM_FALLBACK and self.model != config.LLM_FALLBACK and not getattr(self, "_fell_back", False):
                log.warning("%s isn't downloaded; using the fallback model %s", self.model, config.LLM_FALLBACK)
                self._wanted = self.model
                self._fell_back, self.model = True, config.LLM_FALLBACK
                return self.chat(messages, tools, max_tokens)
            wanted = getattr(self, "_wanted", "") or self.model
            raise LLMUnavailable(missing_model_text(wanted, self.model, self._installed()))
        if r.status_code >= 400:
            detail = ""
            try:
                detail = str(r.json().get("error", ""))[:200]
            except ValueError:
                pass
            log.warning("ollama error %s for %s: %s", r.status_code, self.model, detail)
            low = detail.lower()
            if "0xc0000005" in low or "process has terminated" in low:
                # Seen on Ali's PC (17 Oct): Ollama's Vulkan backend crashes on an old AMD/Intel GPU. CPU only fixes it.
                raise LLMUnavailable("Ollama's model runner crashed (usually the graphics driver). Run it on the CPU: set "
                                     "OLLAMA_VULKAN=0 for your account and restart Ollama (docs/MANUAL.md, Ollama on the CPU).")
            if "requires more system memory" in low or "out of memory" in low or _free_gb() < 3:
                raise LLMUnavailable(f"Ollama couldn't load {self.model}: only {_free_gb():.1f} GB of memory is free and it "
                                     "needs about 3 GB. Close some apps or browser tabs, then ask again.")
            raise LLMUnavailable(f"Ollama answered with an error ({r.status_code}): {detail or 'see the server log'}.")
        data = self._read_stream(r, sink, clock) if sink is not None else r.json()
        self.tokens_used += int(data.get("prompt_eval_count", 0)) + int(data.get("eval_count", 0))
        _report("ollama", self.model, data.get("prompt_eval_count", 0), data.get("eval_count", 0))
        msg = data.get("message", {})
        calls = msg.get("tool_calls") or []
        if calls:
            fn = calls[0].get("function", {})
            args = fn.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except ValueError:
                    args = {}
            return LLMReply(tool_call=ToolCall(fn.get("name", ""), args if isinstance(args, dict) else {}))
        return LLMReply(text=(msg.get("content") or "").strip())

    def status(self) -> dict:
        """Is Ollama up, and is the model downloaded? Quick check for the badge on the page."""
        try:
            r = self.client.get(f"{self.base_url}/api/tags", timeout=2)
            names = [m.get("name", "") for m in r.json().get("models", [])]
        except (httpx.HTTPError, ValueError):
            return {"ready": False, "reason": "Ollama isn't running"}
        model = self.model if ":" in self.model else self.model + ":latest"
        if model not in names and self.model not in names:
            return {"ready": False, "reason": f"Run: ollama pull {self.model}"}
        return {"ready": True, "reason": ""}


# ---------------------------------------------------------------- any OpenAI-compatible server

def _retry_after(r) -> float:
    """Seconds from a Retry-After header (0 if missing or not a number)."""
    try:
        return float(r.headers.get("retry-after", 0))
    except ValueError:
        return 0.0


class OpenAICompatLLM:
    """POST {base_url}/chat/completions. base_url usually ends in /v1."""

    MAX_429_RETRIES = 2

    def __init__(self, base_url: str | None = None, model: str | None = None, api_key: str | None = None,
                 http_client: httpx.Client | None = None, sleep=time.sleep, max_429_retries: int | None = None,
                 timeout: float | None = None, tier: str = "", temperature: float | None = 0.3,
                 token_param: str = "max_tokens", token_scale: float = 1.0, extra: dict | None = None):
        self.tier = tier or config.LLM_PROVIDER   # which brain this is, for the token counts
        self.temperature, self.token_param, self.token_scale = temperature, token_param, token_scale
        self.extra = dict(extra or {})
        self.base_url = (base_url or config.LLM_BASE_URL).rstrip("/")
        self.model = model or config.LLM_MODEL
        self.api_key = config.LLM_API_KEY if api_key is None else api_key
        self.client = http_client or httpx.Client(timeout=timeout or config.LLM_TIMEOUT)
        self.sleep = sleep
        self.max_429_retries = self.MAX_429_RETRIES if max_429_retries is None else max_429_retries

    def chat(self, messages: list[dict], tools: list[dict], max_tokens: int = 600) -> LLMReply:
        max_tokens = _length(self.tier, max_tokens)
        body = {"model": self.model, "messages": messages, self.token_param: int(max_tokens * self.token_scale), **self.extra}
        if self.temperature is not None:
            body["temperature"] = self.temperature
        if tools:
            body["tools"] = tools
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        guard = GUARD.get()
        sink = STREAM.get()
        if sink is None and guard is not None:
            sink = _QuietSink()
        if sink is not None:
            body["stream"] = True
        clock = _Clock(guard, f"{self.tier or 'The model server'} ({self.model})", self.tier or self.base_url) if guard else None
        for attempt in range(self.max_429_retries + 1):
            try:
                r = _guarded(clock, lambda: self._post(body, headers)) if clock else self._post(body, headers)
            except httpx.ConnectError:
                raise LLMUnavailable(f"Can't reach the model server at {self.base_url}. Is it running?")
            except httpx.TimeoutException:
                raise LLMUnavailable("The model took too long. Try a shorter question.")
            if r.status_code != 429 or attempt == self.max_429_retries:
                break
            self.sleep(min(_retry_after(r) or 2, 10))
        low = r.text[:500].lower() if r.status_code >= 400 else ""
        if r.status_code in (429, 402) or (r.status_code in (400, 403) and ("quota" in low or "rate limit" in low
                                                                           or "insufficient" in low)):
            raise QuotaExceeded(f"{self.base_url} is out of free requests or credits ({r.status_code}).",
                                _retry_after(r))
        if r.status_code in (401, 403):
            raise LLMUnavailable("The model server refused the key. Check the key in .env.")
        if r.status_code >= 400:
            raise LLMUnavailable(f"The model server answered with an error ({r.status_code}).")
        data = self._read_stream(r, sink, clock) if sink is not None else r.json()
        msg = data["choices"][0]["message"]
        used = data.get("usage") or {}
        if not used:   # a server that doesn't count: about 4 characters a token
            used = {"prompt_tokens": len(json.dumps(messages)) // 4, "completion_tokens": len(json.dumps(msg)) // 4}
        _report(self.tier, self.model, used.get("prompt_tokens", 0), used.get("completion_tokens", 0))
        if msg.get("tool_calls"):
            tc = msg["tool_calls"][0]
            try:
                args = json.loads(tc["function"].get("arguments") or "{}")
            except ValueError:
                args = {}
            return LLMReply(tool_call=ToolCall(tc["function"]["name"], args if isinstance(args, dict) else {}, tc.get("id", "call_1")))
        return LLMReply(text=(msg.get("content") or "").strip())

    def _post(self, body: dict, headers: dict):
        url = f"{self.base_url}/chat/completions"
        if not body.get("stream"):
            return self.client.post(url, json=body, headers=headers)
        r = self.client.send(self.client.build_request("POST", url, json=body, headers=headers), stream=True)
        if r.status_code >= 400:
            r.read()
            r.close()
        return r

    def _read_stream(self, r, sink, clock: "_Clock | None" = None) -> dict:
        """Server-sent events ("data: {...}") -> one chat.completion: text pieces go on as they come; tool-call
        pieces are joined by their index."""
        content, calls, usage = [], {}, {}
        sink.start()
        try:
            for line in (_guarded_lines(clock, r) if clock else r.iter_lines()):
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                except ValueError:
                    continue
                if chunk.get("error"):
                    raise LLMUnavailable(f"The model server stopped with an error: {str(chunk['error'])[:200]}")
                usage = chunk.get("usage") or usage
                for choice in chunk.get("choices") or []:
                    delta = choice.get("delta") or {}
                    if clock and (delta.get("content") or delta.get("tool_calls") or delta.get("reasoning_content")):
                        clock.first = True
                    if delta.get("content"):
                        content.append(delta["content"])
                        sink.token(delta["content"])
                    for tc in delta.get("tool_calls") or []:
                        slot = calls.setdefault(tc.get("index", 0), {"id": "", "name": "", "arguments": ""})
                        slot["id"] = tc.get("id") or slot["id"]
                        fn = tc.get("function") or {}
                        slot["name"] += fn.get("name") or ""
                        slot["arguments"] += fn.get("arguments") or ""
        except httpx.TimeoutException:
            raise LLMUnavailable("The model took too long. Try a shorter question.")
        except httpx.HTTPError:
            raise LLMUnavailable("The model server stopped answering in the middle of a reply.")
        finally:
            r.close()
        msg = {"content": "".join(content)}
        if calls:
            msg["tool_calls"] = [{"id": c["id"] or "call_1", "type": "function",
                                  "function": {"name": c["name"], "arguments": c["arguments"]}} for _, c in sorted(calls.items())]
        return {"choices": [{"message": msg}], **({"usage": usage} if usage else {})}

    def status(self) -> dict:
        return {"ready": True, "reason": ""}


def get_llm(role: str = "general"):
    """The model for a role (general, coding, reasoning, medical). See airtac/brains.py."""
    from airtac import brains  # imported here: brains builds the LLM classes above
    return brains.get(role)


def describe() -> dict:
    """For the badge: which brain, and whether it's ready."""
    if config.LLM_PROVIDER == "mock":
        return {"provider": "mock", "model": "keyword rules", "ready": True, "reason": ""}
    return {"provider": config.LLM_PROVIDER, "model": config.LLM_MODEL, **get_llm().status()}


# ---------------------------------------------------------------- small helpers for tools that need the model

def brain_on() -> bool:
    """False in the no-model mode (keyword rules), where tools fall back to their sample answers."""
    return config.LLM_PROVIDER != "mock"


def ask_text(system: str, content: str, role: str = "general", max_tokens: int = 900) -> str:
    reply = get_llm(role).chat([{"role": "system", "content": system}, {"role": "user", "content": content}], [],
                               max_tokens=max_tokens)
    return reply.text


def ask_json(system: str, content: str, role: str = "general", retries: int = 1):
    """Ask the model for JSON. Retries once if the reply doesn't parse. None on failure."""
    for _ in range(retries + 1):
        try:
            text = ask_text(system, content, role)
        except Exception:
            log.exception("model call failed")
            return None
        text = re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.M).strip()
        try:
            return json.loads(text)
        except ValueError:
            m = re.search(r"[\[{].*[\]}]", text, re.S)  # sometimes the JSON has words around it
            if m:
                try:
                    return json.loads(m.group(0))
                except ValueError:
                    pass
    return None
