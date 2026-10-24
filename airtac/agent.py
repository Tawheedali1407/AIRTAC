"""The agent loop: pick a brain, ask it, run at most one tool per step, repeat until it answers in words.

Limits keep it safe on a small laptop: at most 6 steps, a time budget per turn, a kill switch,
and "write" or "device" tools (anything that changes things outside AIRTAC) wait for your Confirm.

What the model reads each turn (kept short, because a small model's context is small):
  - the system prompt, plus the chosen brain's own instructions (coding, engineering, medicine),
  - up to 5 facts you asked it to remember, and up to 3 lessons from your corrections,
  - background from plugins (passages from your knowledge library that match the question),
  - the last few messages of this conversation.
"""
import json
import logging
import threading
import time
import uuid

from airtac import brains, config, memory, plugins, safety
from airtac import llm as llm_mod
from airtac.llm import STREAM, Cancelled, LLMUnavailable, system_prompt
from airtac.models import SCREEN_LIMIT, ChatResponse, Pending, Step, clean_spoken
from airtac.plugin_api import CURRENT, CURRENT_SESSION
from airtac.tools import REGISTRY, openai_schemas, run_and_log, select_tools

log = logging.getLogger("airtac.agent")

MAX_STEPS = 6
TIME_BUDGET = 240.0  # seconds per turn: a 4B model on a laptop CPU needs several seconds per step
HISTORY = 8          # messages of context sent to the model (never the whole history)
CONTEXT_CHARS = 2500  # background text from plugins, at most
CONFIRM_RISKS = ("write", "device")
CHAT_TOKENS = 1600   # the Chat page's answers are read, not heard, so they may be longer
CHAT_TEXT_LIMIT = 12000
MEDICAL_NOTE = "General information for learning, not medical advice. See a doctor for anything that worries you."

_kill = threading.Event()        # set by /api/kill, checked before every step
_pending: dict[str, dict] = {}   # actions waiting for /api/confirm
MAX_PENDING = 50
PENDING_SECONDS = 600            # an old "yes" shouldn't fire much later


_guards: dict[str, "llm_mod.Guard"] = {}   # the chats answering right now: Stop cancels their model call at once


def kill(session_id: str | None = None) -> None:
    """Stop: the agent stops before its next step, and the model call in progress is cancelled now (its stream
    is closed, so Ollama stops generating instead of keeping the CPU busy for the next question)."""
    if session_id is None:
        _kill.set()
    for sid, guard in list(_guards.items()):
        if session_id is None or sid == session_id:
            guard.cancel.set()


def fallback_brains(skip: str = "") -> list[dict]:
    """Cloud brains with a key that a stuck or failed answer can be retried on (the keys stay on this PC)."""
    from airtac import providers
    return [{"id": n, "label": providers.PROVIDERS[n].label} for n in providers.order() if n != skip][:4]


def _brain_info(role: str) -> dict:
    from airtac import providers
    info = {"role": role, "label": brains.ROLES.get(role, brains.ROLES["general"])["label"],
            "model": brains.model_for(role) if config.LLM_PROVIDER != "mock" else "keyword rules"}
    if providers.LAST and config.LLM_PROVIDER != "mock":   # a free cloud tier (or the local last stop) answered
        info["model"], info["tier"] = providers.LAST.get("model", info["model"]), providers.LAST.get("tier", "")
    elif providers.ASKED.get("provider") == "ollama" and config.LLM_PROVIDER != "mock":
        # a model picked in the menu answered on its own (no failover router): name it, not the role's default
        info["model"], info["tier"] = providers.ASKED.get("model", info["model"]), "ollama"
    asked =providers.ASKED.get("provider", "")
    if asked and config.LLM_PROVIDER != "mock":
        info["asked"] = asked
        if asked != "ollama" and info.get("tier") and info["tier"] != asked:
            # the picked brain was busy or out of free requests, so the next one answered: say so under the answer
            why = providers.state(asked).last_error
            info["fallback"] = (f"{providers.PROVIDERS[asked].label} couldn't answer"
                                f"{' (out of free requests)' if 'quota' in why.lower() or '429' in why or 'limit' in why.lower() else ''}, "
                                f"so {providers.PROVIDERS[info['tier']].label if info['tier'] in providers.PROVIDERS else 'your PC'} did.")
    return info


def _session_usage(session_id: str) -> dict:
    """This message's tokens, added to the running total for the session (kept in the database, so the
    chat header still knows them after a restart). Only counts: never the text."""
    from airtac import providers
    turn = {"input": int(providers.TURN.get("prompt", 0)), "output": int(providers.TURN.get("completion", 0))}
    providers.TURN.clear()
    try:
        total = json.loads(memory.kv_get("session_tokens", session_id) or "{}")
    except ValueError:
        total = {}
    total = {"input": int(total.get("input", 0)) + turn["input"], "output": int(total.get("output", 0)) + turn["output"]}
    if turn["input"] or turn["output"]:
        memory.kv_set("session_tokens", session_id, json.dumps(total))
    return {"turn": {**turn, "total": turn["input"] + turn["output"]},
            "session": {**total, "total": total["input"] + total["output"]}}


def _first_sentences(text: str, n: int = 2) -> str:
    """What the voice reads from a long chat answer: its first sentences, without code or tables."""
    import re
    plain = re.sub(r"```.*?(```|$)", " ", text, flags=re.S)
    plain = "\n".join(line for line in plain.splitlines() if not line.lstrip().startswith("|"))
    parts = re.split(r"(?<=[.!?])\s+", clean_spoken(plain, 2000))
    return " ".join(parts[:n])


def _finish(session_id: str, spoken: str, steps: list[Step], screens: list[str], role: str = "general",
            sources: list[dict] | None = None, mode: str = "voice") -> ChatResponse:
    text = ""
    if mode == "chat":
        text = (spoken or "").strip()[:CHAT_TEXT_LIMIT] or "Done."
        spoken = clean_spoken(_first_sentences(text)) or "Done."
    else:
        spoken = clean_spoken(spoken) or "Done."
    message_id = memory.add_message(session_id, "assistant", text or spoken)
    if role == "medical":
        screens = screens + [MEDICAL_NOTE]
        if text:
            text += f"\n\n_{MEDICAL_NOTE}_"
    screen = "\n\n".join(s for s in screens if s)[:SCREEN_LIMIT]
    return ChatResponse(spoken=spoken, screen=screen, steps=steps, message_id=message_id, brain=_brain_info(role),
                        sources=sources or [], usage=_session_usage(session_id), text=text)


def plugin_context(message: str, session_id: str = "default") -> tuple[str, list[dict]]:
    """Background text from plugins' @p.context hooks, and the sources it came from.
    A hook may take (message) or (message, session_id) — session memory needs to know the current chat."""
    import inspect
    parts, sources = [], []
    for p in plugins.enabled():
        hook = p.hooks.get("context")
        if not hook:
            continue
        token = CURRENT.set(p.id)
        try:
            wants_session = len(inspect.signature(hook).parameters) >= 2
            found = (hook(message, session_id) if wants_session else hook(message)) or {}
        except Exception:
            log.exception("context from %s failed", p.id)
            found = {}
        finally:
            CURRENT.reset(token)
        if found.get("text"):
            parts.append(found["text"])
            sources.extend(found.get("sources", []))
    return "\n\n".join(parts)[:CONTEXT_CHARS], sources


def attached_context(ids: list[int] | None, message: str) -> tuple[str, list[dict]]:
    """What the files attached to this message say (plugins with an @p.attached hook)."""
    if not ids:
        return "", []
    for p in plugins.enabled():
        hook = p.hooks.get("attached")
        if hook:
            try:
                found = hook(ids, message) or {}
            except Exception:
                log.exception("attachments from %s failed", p.id)
                found = {}
            return found.get("text", ""), found.get("sources", [])
    return "", []


def _build_context(session_id: str, message: str, role: str, attachments: list[int] | None = None,
                   mode: str = "voice", instructions: str = "") -> tuple[list[dict], list[dict]]:
    # Top 5 facts that match the message; if none match, the 5 newest.
    facts = memory.recall(message, k=5) or memory.recall("", k=5)
    text = system_prompt(facts, mode)
    if instructions:
        text += "\n\nInstructions for this section (from the user's Settings, follow them):\n" + instructions[:4000]
    if brains.prompt_for(role):
        text += "\n\n" + brains.prompt_for(role)
    lessons = memory.lessons_for(message)
    if lessons:
        text += "\n\nLessons from the user's earlier corrections (follow them):\n" + "\n".join(f"- {x}" for x in lessons)
    background, sources = plugin_context(message, session_id)
    if background:
        text += ("\n\nPassages from the user's own library that may help. Use them if they answer the question, "
                 "and name the book or file you used:\n" + background)
    files, file_sources = attached_context(attachments, message)
    if files:
        text += ("\n\nThe user attached these files to this message. Answer from them, and say which file "
                 "(and page or slide) you used:\n" + files)
        sources = file_sources + sources
    history = memory.last_messages(HISTORY - 1, session_id)
    return [{"role": "system", "content": text}, *history, {"role": "user", "content": message}], sources


PASTED = "[Pasted problem"


def with_pasted_problem(message: str, problem_text: str | None, site: str | None, language: str | None) -> str:
    if not problem_text or not problem_text.strip():
        return message
    return (f"{message}\n\n{PASTED} | site: {site or 'leetcode'} | language: {language or 'python'}]\n"
            f"{problem_text.strip()[:6000]}")


def chat(message: str, session_id: str = "default", llm=None, problem_text: str | None = None,
         site: str | None = None, language: str | None = None, brain: str | None = None,
         plugins: list[str] | None = None, attachments: list[int] | None = None, mode: str = "voice",
         instructions: str = "") -> ChatResponse:
    """mode="chat" is the Chat page: longer markdown answers (text), the first sentences as spoken.
    instructions: a custom section's own instructions (Settings > Sections)."""
    _kill.clear()  # a new message is a fresh start
    guard = llm_mod.new_guard()
    _guards[session_id] = guard
    token = llm_mod.GUARD.set(guard)
    try:
        return _chat(message, session_id, llm, problem_text, site, language, brain, plugins, attachments, mode,
                     instructions, guard)
    finally:
        llm_mod.GUARD.reset(token)
        if _guards.get(session_id) is guard:
            _guards.pop(session_id, None)


def _chat(message, session_id, llm, problem_text, site, language, brain, plugins, attachments, mode, instructions,
          guard) -> ChatResponse:
    from airtac import providers
    providers.LAST.clear()  # filled by whichever brain tier answers this message
    providers.TURN.clear()  # this message's tokens
    message = message.strip()
    CURRENT_SESSION.set(session_id)   # so session-memory observers know which chat this is
    refusal = safety.check_request(f"{message}\n{problem_text or ''}")
    if refusal:
        asked = memory.add_message(session_id, "user", message)
        response = _finish(session_id, refusal, [], [], mode=mode)
        response.user_message_id = asked
        return response
    full = with_pasted_problem(message, problem_text, site, language)
    app_id = session_id[4:] if session_id.startswith("app:") else None
    role = brains.route(full, app_id)
    messages, sources = _build_context(session_id, full, role, attachments, mode, instructions)
    first_line = (problem_text or "").strip().splitlines()[0][:80] if problem_text and problem_text.strip() else ""
    asked = memory.add_message(session_id, "user", message + (f" (pasted problem: {first_line})" if first_line else ""))
    names = select_tools(full, app_id, only=plugins) if brains.uses_tools(role) else []
    response = _loop(session_id, messages, llm or brains.get(role, prefer=brain or ""), names, role, sources, mode, guard)
    response.user_message_id = asked
    if not response.pending:
        _observe(message, response)
    return response


def _observe(message: str, response: ChatResponse) -> None:
    """Let plugins notice things in what you said (a date to put on the schedule). What they did
    is added to the reply, so you always hear about it, with an Undo button on the page."""
    for p in plugins.enabled():
        hook = p.hooks.get("observe")
        if not hook:
            continue
        token = CURRENT.set(p.id)
        try:
            found = hook(message, [s.tool for s in response.steps])
        except Exception:
            log.exception("observer of %s failed", p.id)
            found = None
        finally:
            CURRENT.reset(token)
        if found:
            response.noticed.append({"plugin": p.id, **found})
            if found.get("say"):
                response.spoken = clean_spoken(f"{response.spoken} {found['say']}")
            if found.get("screen"):
                response.screen = (response.screen + "\n\n" + found["screen"]).strip()[:SCREEN_LIMIT]


def _loop(session_id: str, messages: list[dict], llm, names: list[str], role: str = "general",
          sources: list[dict] | None = None, mode: str = "voice", guard=None) -> ChatResponse:
    steps: list[Step] = []
    screens: list[str] = []
    deadline = time.monotonic() + TIME_BUDGET
    tool_list = openai_schemas(names)
    sink = STREAM.get()
    max_tokens = CHAT_TOKENS if mode == "chat" else 600

    def done(spoken: str) -> ChatResponse:
        return _finish(session_id, spoken, steps, screens, role, sources, mode)

    def trouble(spoken: str, problem: str) -> ChatResponse:
        """A readable message instead of a hang, and the cloud brains it can be tried on (a button on the page)."""
        from airtac import providers
        response = done(spoken)
        response.problem = problem
        response.fallback_brains = fallback_brains(skip=providers.ASKED.get("provider", "")) if problem != "stopped" else []
        return response

    for _ in range(MAX_STEPS):
        if _kill.is_set() or (guard is not None and guard.cancel.is_set()):
            return trouble("Stopped.", "stopped")
        if time.monotonic() > deadline:
            return trouble("That took too long, so I stopped. Please try a simpler request.", "timeout")
        try:
            reply = llm.chat(messages, tool_list, max_tokens) if mode == "chat" else llm.chat(messages, tool_list)
        except Cancelled:
            return trouble("Stopped.", "stopped")
        except llm_mod.TooSlow as e:
            return trouble(str(e), "timeout")
        except LLMUnavailable as e:
            return trouble(str(e), "brain_error")
        except Exception:
            log.exception("LLM call failed")
            return trouble("I couldn't reach the language model just now. Please try again.", "brain_error")

        if reply.tool_call is None:
            return done(reply.text)

        call = reply.tool_call
        if call.name not in names:
            # Only tools offered for this question may run: a model (or text it read on a web page) naming
            # another tool, say one you switched off in the chat's side panel, gets a plain "no".
            log.warning("model asked for %s, which wasn't offered", call.name)
            return done("I can't use that tool for this. Turn its plugin on in the chat's side panel, or ask in its app.")
        tool = REGISTRY.get(call.name)
        if tool is not None and tool.risk in CONFIRM_RISKS:
            return _hold_for_confirmation(session_id, call, tool, steps, screens, role, mode)

        if sink is not None:
            sink.step(call.name, "running")
        # A tool may ask a brain itself (Reverse engineering writes a plan): Stop still cancels it, but it gets the
        # turn's budget rather than one chat answer's 90 seconds.
        tool_token = llm_mod.GUARD.set(llm_mod.Guard(guard.cancel, guard.first_token, TIME_BUDGET, guard.stuck)) if guard else None
        try:
            result = run_and_log(call.name, call.args)
        finally:
            if tool_token is not None:
                llm_mod.GUARD.reset(tool_token)
        steps.append(Step(tool=call.name, args=call.args, status="ok" if result.ok else "error"))
        if sink is not None:
            sink.step(call.name, steps[-1].status)
        screens.append(result.screen)
        messages.append({"role": "assistant", "content": "", "tool_calls": [{
            "id": call.id, "type": "function",
            "function": {"name": call.name, "arguments": json.dumps(call.args)}}]})
        messages.append({"role": "tool", "tool_call_id": call.id,
                         "content": json.dumps({"spoken": result.spoken, "screen": result.screen})})

    return done("I hit my step limit, so I stopped here. Details are on screen.")


def _hold_for_confirmation(session_id, call, tool, steps, screens, role="general", mode="voice") -> ChatResponse:
    """Actions that change things never run straight away: park them and ask."""
    summary = tool.summarize(call.args) if tool.summarize else f"run {call.name}"
    pending = Pending(id=uuid.uuid4().hex[:12], tool=call.name, args=call.args, summary=summary)
    if len(_pending) >= MAX_PENDING:
        _pending.pop(next(iter(_pending)))
    steps.append(Step(tool=call.name, args=call.args, status="pending"))
    _pending[pending.id] = {"pending": pending, "session_id": session_id, "steps": steps, "screens": screens,
                            "at": time.monotonic(), "mode": mode}
    response = _finish(session_id, f"Should I {summary}? Please confirm or cancel.", steps, screens, role, mode=mode)
    response.pending = pending
    return response


def confirm(pending_id: str, approve: bool) -> ChatResponse:
    entry = _pending.pop(pending_id, None)  # each pending action can be answered only once
    if entry is None or time.monotonic() - entry["at"] > PENDING_SECONDS:
        return ChatResponse(spoken="That request has expired or was already handled. Please ask again.")
    pending, session_id = entry["pending"], entry["session_id"]
    steps, screens = entry["steps"], entry["screens"]
    mode = entry.get("mode", "voice")
    if not approve:
        steps[-1].status = "cancelled"
        return _finish(session_id, "Okay, I cancelled that.", steps, screens, mode=mode)
    result = run_and_log(pending.tool, pending.args)
    steps[-1].status = "ok" if result.ok else "error"
    return _finish(session_id, result.spoken, steps, screens + [result.screen], mode=mode)
