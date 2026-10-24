"""HTTP API for AIRTAC Advance, and the web page at "/".

Run:  uvicorn airtac.main:app --reload
Plugins add their own routes under /api/plugins/<id>/ (see plugins/schedule for an example).
"""
import hashlib
import logging
import socket
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from airtac import __version__, agent, auth, brains, briefing, config, connections, llm, logsafe, memory, pairing, plugin_api, plugins, voice, work
from airtac.models import (ChatRequest, ChatResponse, ChatStreamRequest, ConfirmRequest, ConnectionSave, FeedbackRequest, LoginRequest, SkillRequest,
                           SpeakRequest, WorkConfirm, WorkRequest)
from airtac.plugin_api import CURRENT, local_only
from airtac.scheduler import SCHEDULER

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
for _noisy in ("httpx", "httpcore", "fontTools"):  # httpx prints full URLs (which can carry keys); fontTools is chatty
    logging.getLogger(_noisy).setLevel(logging.WARNING)
logsafe.install()   # hides anything that looks like a key in every log line (again at start-up, for uvicorn's handlers)
log = logging.getLogger("airtac.main")

plugins.load_all()
from airtac import guest  # noqa: E402  (the public copy's hourly reset)


@asynccontextmanager
async def lifespan(_app):
    logsafe.install()
    try:
        auth.service_token()       # data/airtac1.token: AIRTAC 1 reads it to use the shared brains
    except OSError:
        log.warning("couldn't write data/airtac1.token")
    plugin_api.guard_http()      # plugins may only reach the sites their manifest lists, even with httpx directly
    try:
        connections.load_saved()   # keys saved on the Connections page (from the vault)
    except Exception:
        log.exception("couldn't read the saved keys; .env still works")
    if config.SCHEDULER:
        SCHEDULER.start()
        for plug in plugins.enabled():
            hook = plug.hooks.get("startup")
            if hook:
                try:
                    hook()
                except Exception:
                    log.exception("start-up of %s failed", plug.id)
    pairing.start_if_on(app)   # phone access on Wi-Fi (HTTPS), only if you switched it on
    if config.GUEST:
        guest.start()
    yield
    pairing.stop_listener()
    SCHEDULER.stop()


# No API docs pages: they'd list every route to anyone who reaches the address.
app = FastAPI(title="AIRTAC Advance", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


from fastapi.exceptions import RequestValidationError  # noqa: E402


@app.exception_handler(RequestValidationError)
async def _invalid(_request: Request, exc: RequestValidationError):
    """A request that doesn't fit: say which field and why, but never echo what was typed. FastAPI's
    default answer repeats the input, which could be a password or a key."""
    fields = [{"field": ".".join(str(x) for x in e.get("loc", ())[1:]), "problem": e.get("msg", "invalid")}
              for e in exc.errors()]
    return JSONResponse({"detail": "Some of what was sent doesn't fit. Check: "
                         + ", ".join(f["field"] or "the request" for f in fields), "fields": fields}, status_code=422)


def _host_name(header: str) -> str:
    header = header.strip().lower()
    if header.startswith("["):
        return header.split("]")[0] + "]"
    return header.split(":")[0]


# Pages and files the passcode screen itself needs, before you're signed in.
OPEN_PATHS = ("/login.html", "/login.js", "/api/login", "/style.css", "/favicon.svg", "/logo.svg", "/manifest.webmanifest",
              "/api/pair/finish", "/download/airtac.apk")
# The Wi-Fi listener (paired phones) serves only the API, the app's page and the APK download.

# Security headers. The page's Content Security Policy runs only AIRTAC's own scripts (plus the one
# inline line in index.html that switches the start-up off, allowed by its hash), so even if some text
# slipped into the page as HTML, it couldn't run code or send your data elsewhere.
INLINE_SCRIPT_HASH = "sha256-Uw8evlY0hKKFktuXgHY/JlX3Hzm+ytlXLtJg8i+Fwt8="
PAGE_CSP = ("default-src 'self'; script-src 'self' '" + INLINE_SCRIPT_HASH + "'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; media-src 'self' data: blob:; connect-src 'self'; font-src 'self'; "
            "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
SECURITY_HEADERS = {"X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY",
                    "Cross-Origin-Opener-Policy": "same-origin",
                    "Permissions-Policy": "microphone=(self), camera=(), geolocation=(), payment=()"}


class RateLimiter:
    """Limits for other devices (your phone): N requests an hour each, and a daily cap for all of them,
    so a leaked passcode can't run up your cloud brains. This PC has no limit."""

    def __init__(self, per_hour: int, per_day: int):
        self.per_hour, self.per_day = per_hour, per_day
        self.hits: dict = {}
        self.day, self.day_count = "", 0

    def check(self, who: str, now: float | None = None) -> str | None:
        import time
        from collections import deque
        now = time.time() if now is None else now
        today = time.strftime("%Y-%m-%d", time.gmtime(now))
        if today != self.day:
            self.day, self.day_count = today, 0
        if self.day_count >= self.per_day:
            return "AIRTAC has reached today's limit for other devices. It resets at midnight (UTC)."
        if len(self.hits) > 5000:   # forget quiet devices, so memory can't grow forever
            for key in [k for k, q in self.hits.items() if not q or now - q[-1] > 3600]:
                del self.hits[key]
        recent = self.hits.setdefault(who, deque())
        while recent and now - recent[0] > 3600:
            recent.popleft()
        if len(recent) >= self.per_hour:
            return f"That's {self.per_hour} requests this hour from this device. Please try again later."
        recent.append(now)
        self.day_count += 1
        return None


limiter = RateLimiter(int(config.section("server").get("remote_per_hour", 120)),
                      int(config.section("server").get("remote_per_day", 800)))
GUEST_BLOCKED = ("/api/upload", "/api/security/scan", "/api/restart", "/api/voice/stt")
LIMITED = ("/api/chat", "/api/confirm", "/api/work", "/api/feedback", "/api/login")


@app.middleware("http")
async def trusted_host_and_passcode(request: Request, call_next):
    # 1. Blocks DNS rebinding: a web page whose address secretly points at 127.0.0.1 can't use your
    #    browser to talk to your assistant. Add your laptop's Tailscale name to [server] trusted_hosts
    #    to use AIRTAC from your phone (and read docs/SECURITY.md first).
    host = _host_name(request.headers.get("host", ""))
    lan = pairing.is_lan_request(request)
    if not config.host_trusted(host) and not (lan and pairing.lan_host_ok(host)):
        return JSONResponse({"detail": "AIRTAC doesn't know this host name. Add it to [server] trusted_hosts."},
                            status_code=403)
    # 2. A change sent from another website is refused: your browser names the page that sent a request
    #    (Origin), and a page elsewhere must never add to your schedule, change keys or start a job.
    origin = request.headers.get("origin")
    if request.method not in ("GET", "HEAD", "OPTIONS") and origin and origin != "null" \
            and not config.host_trusted(_host_name(origin.split("//", 1)[-1].split("/")[0])):
        return JSONResponse({"detail": "Requests from other websites aren't allowed."}, status_code=403)
    # 3. Any address other than this computer needs the passcode (a session cookie after /api/login).
    path = request.url.path
    device = pairing.device_from(request)          # a paired phone (Authorization: Bearer, or the web view's cookie)
    #    Guest mode (the public try-it copy) lets visitors in without one; they still never count as this PC.
    if config.GUEST and path in GUEST_BLOCKED:
        return JSONResponse({"detail": "This is switched off in the public copy of AIRTAC."}, status_code=403)
    if not config.GUEST and not auth.is_local_request(request) and not path.startswith("/icons/") and path not in OPEN_PATHS \
            and not device and not auth.valid(request.cookies.get(auth.COOKIE)):
        if path.startswith("/api/") or path.startswith("/files/"):
            return JSONResponse({"detail": "Please sign in with your AIRTAC passcode."}, status_code=401)
        return RedirectResponse("/login.html")
    # 3b. AIRTAC 1's service token works only from this PC and only on its few routes (auth.SERVICE_ROUTES).
    if auth.service_used(request) and not auth.service_ok(request):
        return JSONResponse({"detail": "AIRTAC 1's token isn't accepted here."}, status_code=403)
    service = auth.service_used(request)
    # 4. Every API call carries this install's token (a SameSite=Strict cookie the page got when it loaded).
    #    A paired phone's Bearer header is enough: another website can't send that header without CORS, which
    #    AIRTAC never allows.
    if path.startswith("/api/") and path not in auth.INSTALL_OPEN and auth.install_token_required() \
            and not auth.install_ok(request) and not (device and pairing.bearer_used(request)) and not service:
        return JSONResponse({"detail": "This request didn't come from AIRTAC's own page. Reload the page and try again."},
                            status_code=403)
    # 5. Other devices have a limit on the requests that use a brain.
    if request.method == "POST" and path.startswith(LIMITED) and not auth.is_local_request(request):
        forwarded = [a.strip() for a in request.headers.get("x-forwarded-for", "").split(",") if a.strip()]
        # Behind the public host's proxy the last address is the proxy itself; the visitor is the first one.
        reason = limiter.check((forwarded[0] if config.GUEST and forwarded else forwarded[-1] if forwarded else "")
                               or (request.client.host if request.client else "unknown"))
        if reason:
            return JSONResponse({"detail": reason}, status_code=429)
    response = await call_next(request)
    for name, value in SECURITY_HEADERS.items():
        if not (config.GUEST and name == "X-Frame-Options"):
            response.headers.setdefault(name, value)
    if response.headers.get("content-type", "").startswith("text/html"):
        frames = " ".join(sorted(plugin_api.frame_sources()))   # only sites whose embed check passed
        csp = PAGE_CSP + "; frame-src 'self'" + (f" {frames}" if frames else "")
        if config.GUEST:   # the public copy shows inside its page on huggingface.co
            csp = csp.replace("frame-ancestors 'none'", "frame-ancestors 'self' https://huggingface.co")
        response.headers.setdefault("Content-Security-Policy", csp)
    if not path.startswith(("/api/", "/files/")):
        # The app's own page, script and icons: check for a newer copy every time (a quick 304 when
        # nothing changed), so an update such as a new logo shows up without clearing the browser cache.
        response.headers.setdefault("Cache-Control", "no-cache")
        signed_in = config.GUEST or auth.is_local_request(request) or bool(device) or auth.valid(request.cookies.get(auth.COOKIE))
        if signed_in and auth.install_token_required() and not auth.install_ok(request):   # never to the login page
            _set_install_cookie(response, request)
    return response


def _set_install_cookie(response, request) -> None:
    secure = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
    # Guest mode runs inside huggingface.co's frame, where a Strict cookie isn't sent; the Origin check still applies.
    same_site = "none" if config.GUEST and secure else "strict"
    response.set_cookie(auth.INSTALL_COOKIE, auth.install_token(), max_age=auth.INSTALL_DAYS * 86400, httponly=True,
                        samesite=same_site, secure=secure)


@app.post("/api/login")
def login(body: LoginRequest, request: Request):
    client = request.client.host if request.client else "unknown"
    if not config.PASSCODE:
        raise HTTPException(403, "No passcode is set, so AIRTAC only works on its own computer. Set AIRTAC_PASSCODE in .env.")
    if auth.locked_out(client):
        raise HTTPException(429, "Too many wrong tries. Wait 10 minutes and try again.")
    if not auth.check_passcode(body.passcode, client):
        raise HTTPException(401, "That passcode isn't right.")
    response = JSONResponse({"ok": True})
    secure = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
    response.set_cookie(auth.COOKIE, auth.new_session(), max_age=auth.SESSION_DAYS * 86400, httponly=True,
                        samesite="strict", secure=secure)
    _set_install_cookie(response, request)
    return response


@app.post("/api/logout")
def logout(request: Request):
    auth.end_session(request.cookies.get(auth.COOKIE))
    response = JSONResponse({"ok": True})
    response.delete_cookie(auth.COOKIE)
    return response


# ---------------------------------------------------------------- talking

def _web_build() -> str:
    """A stamp of the page's files as this server started: when it changes, an open or installed app reloads itself
    (Ali, 18 Oct: "the app I installed isn't updating")."""
    web = config.ROOT / "web"
    try:
        newest = max((f.stat().st_mtime_ns, f.name) for f in web.iterdir() if f.is_file())
    except (OSError, ValueError):
        return __version__
    return hashlib.sha256(f"{__version__}:{newest}".encode()).hexdigest()[:12]


WEB_BUILD = _web_build()


@app.get("/api/health")
def health():
    from airtac import deps
    return {"status": "ok", "version": __version__, "build": WEB_BUILD, "offline": config.OFFLINE, "brain": llm.describe(),
            "voice": voice.status(), "user": config.USER_NAME, "missing_packages": [] if config.GUEST else deps.missing(),
            "guest": config.GUEST, "guest_reset_minutes": config.GUEST_RESET_MINUTES if config.GUEST else 0}


@app.post("/api/chat", response_model=ChatResponse)
def chat(body: ChatRequest):
    instructions, section_plugins, docs = _section_setup(body.section)
    attachments = ((body.attachments or []) + [d for d in docs if d not in (body.attachments or [])])[:10] or None
    return agent.chat(body.message, body.session_id, problem_text=body.problem_text, site=body.site,
                      language=body.language, brain=body.brain,
                      plugins=body.plugins if body.plugins is not None else section_plugins,
                      attachments=attachments, mode=body.mode, instructions=instructions)


# ---------------------------------------------------------------- the Chat page (types out live, like ChatGPT)

def _section_setup(section: str | None) -> tuple[str, list[str] | None, list[int]]:
    """A section from Settings > Sections: its instructions, the plugins (tools) it may use, its documents."""
    if not section:
        return "", None, []
    try:
        from plugins.sections import get_section
    except ImportError:
        return "", None, []
    found = get_section(section)
    if not found:
        raise HTTPException(404, "That section doesn't exist any more. Pick another in the chat's menu.")
    return found.get("prompt", ""), (found.get("plugins") or None), list(found.get("docs") or [])


@app.post("/api/chat/stream")
def chat_stream(body: ChatStreamRequest):
    """Newline-delimited JSON events: start (a model call begins: clear the live text), token (a piece of the answer),
    step (a tool runs or finished), done (the full reply, as /api/chat returns it) or error. Regenerate and Edit
    replace the last question (or an earlier one) and everything after it, only when you press them."""
    import contextvars
    import json as _json
    import queue
    import threading

    from fastapi.responses import StreamingResponse

    sid, text = body.session_id, body.message.strip()
    if body.regenerate:
        last = memory.last_question(sid)
        if not last:
            raise HTTPException(400, "There's no question in this chat to answer again.")
        text = last["content"]
        memory.cut_thread(sid, last["id"])
    elif body.edit_from:
        if not memory.question(sid, body.edit_from):
            raise HTTPException(404, "That message isn't in this chat any more.")
        if not text:
            raise HTTPException(422, "Type the new message.")
        memory.cut_thread(sid, body.edit_from)
    elif not text:
        raise HTTPException(422, "Type a message.")
    instructions, section_plugins, section_docs = _section_setup(body.section)
    attachments = ((body.attachments or []) + [d for d in section_docs if d not in (body.attachments or [])])[:10] or None
    events: queue.Queue = queue.Queue()

    class Sink:
        def start(self):
            events.put({"type": "start"})

        def token(self, piece: str):
            events.put({"type": "token", "text": piece})

        def step(self, tool: str, status: str):
            events.put({"type": "step", "tool": tool, "status": status})

    def work():
        llm.STREAM.set(Sink())
        try:
            reply = agent.chat(text, sid, problem_text=body.problem_text, site=body.site, language=body.language,
                               brain=body.brain, plugins=body.plugins if body.plugins is not None else section_plugins,
                               attachments=attachments, mode=body.mode, instructions=instructions)
            events.put({"type": "done", "reply": reply.model_dump()})
        except Exception:
            log.exception("chat stream failed")
            events.put({"type": "error", "text": "Something went wrong while answering. The details are in data\\logs\\server.log."})
        finally:
            events.put(None)

    ctx = contextvars.copy_context()
    threading.Thread(target=ctx.run, args=(work,), daemon=True, name="airtac-chat").start()

    def lines():
        # A "wait" line every second while nothing else comes: the page shows "Thinking… 12 s", and a closed page is
        # noticed (the write fails), which cancels the model call instead of leaving Ollama busy.
        import time as _time
        began, finished = _time.monotonic(), False
        try:
            while True:
                try:
                    item = events.get(timeout=1.0)
                except queue.Empty:
                    yield _json.dumps({"type": "wait", "seconds": round(_time.monotonic() - began)}) + "\n"
                    continue
                if item is None:
                    finished = True
                    return
                yield _json.dumps(item) + "\n"
        finally:
            if not finished:
                agent.kill(sid)

    return StreamingResponse(lines(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


class BrainAnswer(BaseModel):
    messages: list[dict] = Field(min_length=1, max_length=60)
    max_tokens: int = Field(default=900, ge=16, le=4000)


@app.post("/api/brain/answer")
def brain_answer(body: BrainAnswer, request: Request):
    """AIRTAC 1's shared brains: its own model couldn't answer, so the cloud tiers here answer (keys stay here).
    Only AIRTAC 1's service token from this PC reaches this (auth.SERVICE_ROUTES); no tools, text only."""
    from airtac import providers
    from airtac.llm import LLMUnavailable, QuotaExceeded
    if not auth.service_ok(request) and auth.install_token_required():
        raise HTTPException(403, "Only AIRTAC 1 may use this.")
    messages = [{"role": m.get("role") if m.get("role") in ("system", "user", "assistant") else "user",
                 "content": str(m.get("content") or "")[:20000]} for m in body.messages]
    tiers = [t for t in providers.order() if t != "airtac1"]
    for name in tiers:
        if providers.state(name).resting_until > __import__("time").time():
            continue
        try:
            reply = providers.build(name).chat(messages, [], body.max_tokens)
        except QuotaExceeded as e:
            providers.rest(name, e.retry_after or 600, str(e))
            continue
        except LLMUnavailable as e:
            providers.rest(name, 120, str(e))
            continue
        return {"text": reply.text, "tier": name, "label": providers.PROVIDERS[name].label, "model": providers.model_for(name)}
    raise HTTPException(503, providers.all_failed_text(tiers) if tiers else
                        "No cloud brain is set up in AIRTAC Advance (APIs and keys), or it's offline.")


class ChatRename(BaseModel):
    title: str = Field(min_length=1, max_length=80)


@app.get("/api/conversations")
def conversations(limit: int = 200):
    """The Chat page's list: your chats, newest first (app pages' own chats are left out)."""
    from airtac import cleanup
    return {"chats": [c for c in cleanup.chats(limit) if not c["id"].startswith("app:")]}


@app.get("/api/conversations/{sid}")
def conversation(sid: str):
    if len(sid) > 64:
        raise HTTPException(404, "No such chat.")
    return {"id": sid, "title": memory.kv_get("chat_title", sid), "messages": memory.thread(sid)}


@app.post("/api/conversations/{sid}/rename")
def conversation_rename(sid: str, body: ChatRename):
    if len(sid) > 64:
        raise HTTPException(404, "No such chat.")
    title = " ".join(body.title.split())
    memory.kv_set("chat_title", sid, title)
    return {"id": sid, "title": title}


@app.post("/api/upload")
async def upload(request: Request):
    """The paperclip: keep a file and read it (plugins/attachments has the checks)."""
    from pydantic import ValidationError
    from plugins import attachments
    try:
        body = attachments.Upload(**(await request.json()))
    except (ValidationError, ValueError, TypeError):
        raise HTTPException(422, f"Send a file of up to {attachments.MAX_MB} MB with its name.")
    return attachments.upload_route(body)


@app.post("/api/confirm", response_model=ChatResponse)
def confirm(body: ConfirmRequest):
    return agent.confirm(body.pending_id, body.approve)


class KillRequest(BaseModel):
    session_id: str | None = Field(default=None, max_length=64)


@app.post("/api/kill")
def kill(body: KillRequest | None = None):
    """Stop: cancels the model call in progress (that chat's, or every chat's) and the agent's next step."""
    agent.kill(body.session_id if body else None)
    return {"ok": True}


@app.post("/api/restart")
def restart(request: Request):
    """Settings > Restart AIRTAC (this PC only): new code loads without typing commands. A separate hidden
    PowerShell waits 2 s, stops this server and starts it again; the page shows "reconnecting" and recovers."""
    local_only(request)
    import os
    import subprocess  # nosec B404 (a fixed script of AIRTAC's own, no input from the request)
    import sys
    import threading
    if sys.platform != "win32":
        raise HTTPException(501, "Restart from the page works on Windows. Here: stop and start the server yourself.")
    script = config.ROOT / "scripts" / "restart.ps1"
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",  # noqa: S603  # nosec B603 B607
                      "-File", str(script), "-Delay", "2", "-NoWindow"], creationflags=flags, close_fds=True)
    log.info("restart requested from the page")
    threading.Timer(1.0, lambda: os._exit(0)).start()   # answer first, then let the script start a fresh server
    return {"ok": True, "detail": "Restarting. AIRTAC is back in about 10 seconds."}


@app.get("/api/briefing")
def get_briefing(offset: int | None = None):
    """Your day. offset: the page's getTimezoneOffset() in minutes (India is -330)."""
    if offset is None or not -840 <= offset <= 840:
        return briefing.build()
    return briefing.build(datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=offset))


@app.get("/api/notifications")
def notifications(after: int = -1):
    """New notifications since `after`. after=-1 (first call of a page) just returns the latest id."""
    if after < 0:
        return {"last": memory.last_notification_id(), "items": []}
    items = memory.notifications_after(after)
    return {"last": items[-1]["id"] if items else after, "items": items}


# ---------------------------------------------------------------- voice on this laptop

@app.get("/api/voice")
def voice_status():
    return voice.status()


@app.post("/api/voice/stt")
async def speech_to_text(request: Request):
    if config.STT != "local":
        raise HTTPException(409, "Local speech recognition is off. Set [voice] stt = \"local\".")
    audio = await request.body()
    if not audio or len(audio) > 10_000_000:
        raise HTTPException(413, "Send between 1 byte and 10 MB of audio.")
    try:
        return {"text": voice.transcribe(audio)}
    except ImportError:
        raise HTTPException(503, "faster-whisper isn't installed. Run scripts\\plugins.ps1 voice.")


@app.post("/api/voice/tts")
def text_to_speech(body: SpeakRequest):
    if config.TTS != "local":
        raise HTTPException(409, "Local voice is off. Set [voice] tts = \"local\".")
    try:
        return Response(voice.synthesize(body.text), media_type="audio/wav")
    except (ImportError, FileNotFoundError, OSError):
        raise HTTPException(503, "Piper or its voice file isn't installed. Run scripts\\plugins.ps1 voice.")


# ---------------------------------------------------------------- home screen

def _hook_results(name: str, *args) -> list:
    out = []
    for p in plugins.enabled():
        hook = p.hooks.get(name)
        if not hook:
            continue
        token = CURRENT.set(p.id)
        try:
            value = hook(*args)
        except Exception:
            log.exception("%s hook of %s failed", name, p.id)
            value = None
        finally:
            CURRENT.reset(token)
        if value:
            out.append((p, value))
    return out


@app.get("/api/feed")
def feed():
    sections = []
    for _p, value in _hook_results("feed"):
        sections.extend(value)
    return {"sections": sections}


@app.get("/api/progress")
def progress():
    """Your requests this week, plus whatever the plugins add (LeetCode solved, problems saved...)."""
    out = {"week": memory.activity(7), "you": memory.you(), "hero": None, "bars": None, "counts": [], "next_event": None}
    for _p, value in sorted(_hook_results("progress"), key=lambda pv: pv[1].get("order", 50)):
        for key in ("hero", "bars", "next_event"):
            if value.get(key) and not out[key]:
                out[key] = value[key]
        out["counts"].extend(value.get("counts", []))
    return out


@app.get("/api/history")
def history(q: str = "", limit: int = 60):
    return memory.search_messages(q[:100], limit)


class HistoryDelete(BaseModel):
    questions: list[int] = Field(default_factory=list, max_length=500)
    chats: list[str] = Field(default_factory=list, max_length=200)
    everything: str = Field(default="", max_length=10)     # "DELETE" to clear all of History


@app.get("/api/history/chats")
def history_chats(limit: int = 100):
    from airtac import cleanup
    return {"chats": cleanup.chats(limit)}


@app.post("/api/history/delete")
def history_delete(body: HistoryDelete):
    """Delete questions (with their answers), whole chats, or (everything="DELETE") all of History. Only when you press it."""
    from airtac import cleanup
    if body.everything:
        if body.everything != "DELETE":
            raise HTTPException(400, 'Type DELETE to clear all of History.')
        return {"deleted": cleanup.clear_history()}
    return {"deleted": cleanup.delete_questions(body.questions) + cleanup.delete_chats(body.chats)}


@app.get("/api/memory_cache")
def memory_cache():
    from airtac import cleanup
    return {"kinds": cleanup.summary()}


@app.get("/api/memory_cache/{kind}")
def memory_cache_items(kind: str):
    from airtac import cleanup
    try:
        return {"items": cleanup.items(kind)}
    except ValueError as e:
        raise HTTPException(400, str(e))


class CacheClear(BaseModel):
    id: str = Field(default="", max_length=120)     # one item; "" = clear the whole kind


@app.post("/api/memory_cache/{kind}/delete")
def memory_cache_delete(kind: str, body: CacheClear):
    from airtac import cleanup
    try:
        if body.id:
            return {"deleted": int(cleanup.delete_item(kind, body.id))}
        return {"deleted": cleanup.clear(kind)}
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/apps")
def app_list():
    used = memory.usage_by_plugin()
    return [{**a, "uses": used.get(a["id"], 0)} for a in plugins.apps()]


@app.get("/api/apps/{app_id}")
def app_profile(app_id: str):
    p = plugins.get(app_id)
    if p is None or not p.tools:
        raise HTTPException(404, "No app with that name.")
    m = p.manifest
    page = {"id": p.id, "name": p.name, "icon": m.get("icon", "puzzle"), "about": m.get("about", ""),
            "quick": m.get("quick", []), "action": m.get("action", ""), "stats": [], "bars": None, "lists": [],
            "usage": memory.plugin_usage(p.id), "chats": memory.session_messages(f"app:{p.id}"),
            "permissions": m.get("permissions", {})}
    hook = p.hooks.get("dashboard")
    if hook:
        token = CURRENT.set(p.id)
        try:
            hook(page)
        finally:
            CURRENT.reset(token)
    return page


@app.get("/api/plugins")
def plugin_list():
    """Every plugin and its state. A folder added after AIRTAC started shows as "new" (restart to load it),
    so a plugin dropped into the folder is always found."""
    rows = list(plugins.STATUS.values())
    known = {r["id"] for r in rows}
    for path in sorted(config.PLUGINS_DIR.glob("*/manifest.toml")):
        if path.parent.name not in known:
            rows.append({"id": path.parent.name, "name": path.parent.name, "state": "new", "about": "", "order": 999,
                         "permissions": {"network": [], "risk": "local"}, "tools": [], "icon": "puzzle",
                         "reason": "Added after AIRTAC started. Restart AIRTAC to load it."})
    off = set(config.section("plugins").get("disabled", []) or [])
    for r in rows:
        r["switched_off"] = r["id"] in off
    return sorted(rows, key=lambda s: (s.get("order", 50), s["id"]))


class PluginSwitch(BaseModel):
    on: bool


@app.post("/api/plugins/{plugin_id}/switch")
def plugin_switch(plugin_id: str, body: PluginSwitch, request: Request):
    """Turn a plugin on or off in config/airtac.toml ([plugins] disabled). Applies after a restart."""
    local_only(request)
    if not (config.PLUGINS_DIR / plugin_id / "manifest.toml").is_file():
        raise HTTPException(404, "No such plugin.")
    if plugin_id in ("settings", "finder", "attachments") and not body.on:
        raise HTTPException(400, "This one keeps AIRTAC's own pages working, so it stays on.")
    off = [x for x in (config.section("plugins").get("disabled", []) or []) if x != plugin_id]
    if not body.on:
        off.append(plugin_id)
    config.write_setting("plugins", "disabled", sorted(set(off)))
    return {"disabled": sorted(set(off)), "restart": True}


class NewPlugin(BaseModel):
    id: str = Field(max_length=31)
    name: str = Field(default="", max_length=40)
    about: str = Field(default="", max_length=140)
    risk: str = Field(default="local", max_length=10)
    websites: list[str] = Field(default=[], max_length=10)


@app.post("/api/plugins/new")
def plugin_new(body: NewPlugin, request: Request):
    """The "Add plugin" form: makes plugins/<id>/ from the template (scripts/new_plugin.py)."""
    import importlib.util
    local_only(request)
    spec = importlib.util.spec_from_file_location("new_plugin", config.ROOT / "scripts" / "new_plugin.py")
    maker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(maker)
    try:
        folder = maker.make(body.id, body.name, body.about, body.risk, body.websites, root=config.PLUGINS_DIR.parent)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"made": f"plugins/{folder.name}", "files": [f"plugins/{folder.name}/manifest.toml", f"plugins/{folder.name}/__init__.py",
                                                         f"tests/test_plugin_{folder.name}.py"], "restart": True}


@app.get("/api/brains")
def brain_list():
    return brains.status()


@app.get("/api/brains/models")
def brain_models():
    """Models downloaded in Ollama: size, roughly how much memory each needs, and which role uses it."""
    return brains.models()


class ModelPick(BaseModel):
    role: str = Field(max_length=20)
    model: str = Field(default="", max_length=120)


@app.post("/api/brains/models")
def brain_pick(body: ModelPick, request: Request):
    """Pick the model for a role (saved in config/airtac.toml; applies at once)."""
    local_only(request)
    try:
        brains.set_model(body.role, body.model.strip())
    except ValueError as e:
        raise HTTPException(400, str(e))
    return brains.models()


@app.get("/api/brain_choices")
def brain_choices():
    """For the chat header's brain menu: "Automatic" (the failover order), then groups: Local (Ollama),
    AIRTAC 1, Google, Groq, xAI, OpenRouter free models, Nebius, Hugging Face, and any other brain with a key.
    Each group says whether its key is set or needed. Only names and models, never a key."""
    from airtac import providers
    rows = [{"id": n, "label": p.label, "model": providers.model_for(n), "paid": p.paid,
             "logo": f"logos/{p.logo}.svg" if p.logo and (config.WEB_DIR / "logos" / f"{p.logo}.svg").is_file() else ""}
            for n, p in providers.PROVIDERS.items() if p.key_env and providers.has_key(n)]
    return {"choices": rows, "groups": providers.menu(), "order": providers.order(), "local": config.LLM_MODEL,
            "mock": config.LLM_PROVIDER == "mock"}


@app.post("/api/brain_choices/refresh")
def brain_choices_refresh():
    """Ask each brain with a key for its current model list (kept for a day)."""
    from airtac import providers
    for name in providers.PROVIDERS:
        if providers.PROVIDERS[name].key_env and providers.has_key(name):
            providers.live_models(name, refresh=True)
    return brain_choices()


# ---------------------------------------------------------------- getting better: feedback and lessons

# ---------------------------------------------------------------- connections (keys, set up from the app)

def _connection(cid: str) -> connections.Connector:
    connections.all_connectors()          # makes sure brain cards (and plugin ones) are registered
    if cid not in connections.REGISTRY:
        raise HTTPException(404, "No such connection.")
    return connections.REGISTRY[cid]


def _vault_gate(request: Request) -> None:
    """API keys are seen and changed on this PC only (never a phone, even a paired one); once the vault exists, it
    must be unlocked too."""
    local_only(request)
    if connections._vault_store() is not None:
        if not connections.unlocked():
            raise HTTPException(423, "Locked. Unlock the vault with your super password to see your API keys.")


@app.get("/api/connections")
def connections_list(request: Request):
    _vault_gate(request)
    return {"connections": connections.all_connectors(), "store": connections.store_status(),
            "here": auth.is_local_request(request)}


@app.post("/api/connections/{cid}")
def connections_save(cid: str, body: ConnectionSave, request: Request):
    local_only(request)
    _connection(cid)
    try:
        where = connections.save(cid, body.values)
    except connections.Locked as e:
        raise HTTPException(423, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"saved_in": where, "connection": connections.describe(connections.REGISTRY[cid])}


@app.post("/api/connections/{cid}/test")
def connections_test(cid: str, request: Request):
    local_only(request)
    _connection(cid)
    connections.run_test(cid)
    return connections.describe(connections.REGISTRY[cid])


@app.post("/api/connections/{cid}/disconnect")
def connections_disconnect(cid: str, request: Request):
    local_only(request)
    _connection(cid)
    try:
        left = connections.disconnect(cid)
    except connections.Locked as e:
        raise HTTPException(423, str(e))
    return {"still_in_env": left, "connection": connections.describe(connections.REGISTRY[cid])}


class SecretField(BaseModel):
    key: str = Field(max_length=80, pattern=r"^[A-Za-z0-9_]+$")


def _secret(cid: str, key: str, why: str) -> str:
    from plugins.passwords import store
    try:
        return connections.secret_value(cid, key, why)
    except connections.Locked as e:
        raise HTTPException(423, str(e))
    except store.VaultError as e:
        raise HTTPException(429 if "Too many" in str(e) else 400, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/connections/{cid}/reveal")
def connections_reveal(cid: str, body: SecretField, request: Request):
    """Show (eye button): this PC only, vault unlocked, rate-limited, audit-logged. The page hides it after 10 s."""
    local_only(request)
    _connection(cid)
    response = JSONResponse({"value": _secret(cid, body.key, "revealed"), "hide_after": 10})
    response.headers["Cache-Control"] = "no-store"
    return response


@app.post("/api/connections/{cid}/copy")
def connections_copy(cid: str, body: SecretField, request: Request):
    """Copy onto this PC's clipboard (cleared after 20 s, kept out of clipboard history). The value never goes to the page."""
    local_only(request)
    _connection(cid)
    from plugins.passwords import clipboard
    if not clipboard.copy(_secret(cid, body.key, "copied")):
        raise HTTPException(501, "This PC's clipboard isn't available to AIRTAC. Use Show instead.")
    return {"ok": True, "clears_in": clipboard.CLEAR_AFTER}


@app.post("/api/feedback")
def feedback(body: FeedbackRequest):
    result = memory.add_feedback(body.message_id, body.good, body.note)
    if not result["ok"]:
        raise HTTPException(404, "I can't find that answer any more.")
    return result


@app.get("/api/lessons")
def lessons():
    return {"lessons": memory.list_lessons(), "week": memory.feedback_summary(7)}


@app.post("/api/lessons/{lesson_id}")
def lesson_switch(lesson_id: int, active: bool):
    if not memory.set_lesson(lesson_id, active):
        raise HTTPException(404, "No lesson with that number.")
    return {"ok": True}


@app.delete("/api/lessons/{lesson_id}")
def lesson_delete(lesson_id: int):
    if not memory.delete_lesson(lesson_id):
        raise HTTPException(404, "No lesson with that number.")
    return {"ok": True}


# ---------------------------------------------------------------- Work mode

@app.get("/api/work")
def work_list():
    return {"jobs": work.recent(), "examples": work.EXAMPLES, "skills": memory.list_skills()}


@app.post("/api/work")
def work_start(body: WorkRequest):
    if body.skill_id:
        job = work.run_skill(body.skill_id)
        if job is None:
            raise HTTPException(404, "No skill with that number.")
        return work.public(job)
    return work.public(work.start(body.goal))


def _job(job_id: str) -> dict:
    job = work.get(job_id)
    if job is None:
        raise HTTPException(404, "No job with that id.")
    return job


@app.get("/api/work/{job_id}")
def work_get(job_id: str):
    return work.public(_job(job_id))


@app.post("/api/work/{job_id}/stop")
def work_stop(job_id: str):
    _job(job_id)
    return {"stopped": work.stop(job_id)}


@app.post("/api/work/{job_id}/convert/{fmt}")
def work_convert(job_id: str, fmt: str):
    _job(job_id)
    try:
        return {"file": work.convert(job_id, fmt), "format": work.FORMATS.get(fmt, fmt)}
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.post("/api/work/{job_id}/confirm")
def work_confirm(job_id: str, body: WorkConfirm):
    _job(job_id)
    job = work.confirm(job_id, body.approve)
    if job is None:
        raise HTTPException(409, "That job isn't waiting for you.")
    return work.public(job)


@app.post("/api/skills")
def skill_save(body: SkillRequest):
    skill_id = work.save_skill(body.job_id, body.name)
    if skill_id is None:
        raise HTTPException(409, "Only a finished job can become a skill.")
    return {"id": skill_id}


@app.delete("/api/skills/{skill_id}")
def skill_delete(skill_id: int):
    if not memory.delete_skill(skill_id):
        raise HTTPException(404, "No skill with that number.")
    return {"ok": True}


# ---------------------------------------------------------------- files AIRTAC made

FILE_TYPES = {".pdf": "application/pdf", ".png": "image/png", ".docx":
              "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
              ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
              ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".md": "text/markdown"}


@app.get("/api/files")
def list_files():
    if not config.OUTPUTS_DIR.exists():
        return []
    found = [f for f in config.OUTPUTS_DIR.rglob("*") if f.is_file() and f.suffix.lower() in FILE_TYPES]
    found.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    return [{"name": f.name, "size": f.stat().st_size, "plugin": f.relative_to(config.OUTPUTS_DIR).parts[0],
             "url": "/files/" + f.relative_to(config.OUTPUTS_DIR).as_posix()} for f in found[:60]]


@app.get("/files/{path:path}")
def get_file(path: str):
    base = config.OUTPUTS_DIR.resolve()
    target = (base / path).resolve()
    # Only files inside outputs/, of the kinds AIRTAC makes: blocks tricks like "../.env".
    if not target.is_relative_to(base) or target.suffix.lower() not in FILE_TYPES or not target.is_file():
        raise HTTPException(404, "File not found")
    inline = target.suffix.lower() in (".pdf", ".png", ".md")
    return FileResponse(target, media_type=FILE_TYPES[target.suffix.lower()],
                        filename=None if inline else target.name)


# ---------------------------------------------------------------- paired phones (Part G, airtac/pairing.py)

@app.post("/api/pair/start")
def pair_start(request: Request):
    local_only(request)                    # only this PC can show a pairing code
    try:
        info = pairing.start()
    except pairing.PairError as e:
        raise HTTPException(409, str(e))
    lan = pairing.set_lan(True, app)
    return {**info, "lan": lan, "apk": apk_info()}


class PairFinish(BaseModel):
    code: str = Field(min_length=8, max_length=64)
    name: str = Field(default="Phone", max_length=60)
    google_id_token: str = Field(default="", max_length=4096)


@app.post("/api/pair/finish")
def pair_finish(body: PairFinish, request: Request):
    client = request.client.host if request.client else "unknown"
    try:
        return pairing.finish(body.code, body.name, client, body.google_id_token)
    except pairing.PairError as e:
        raise HTTPException(403, str(e))


@app.get("/api/pair/me")
def pair_me(request: Request):
    """The app's "am I still paired?" check (401 from the middleware when the device was removed)."""
    d = pairing.device_from(request)
    return {"device": pairing.public(d) if d else None, "pc": socket.gethostname()[:40], "version": __version__}


@app.get("/api/pair/web")
def pair_web(request: Request):
    """The app's web view opens this once with its Bearer header; it gets cookies and goes to the page."""
    d = pairing.device_from(request)
    if not d or not pairing.bearer_used(request):
        raise HTTPException(401, "Open this from the Airtac Mobile app.")
    token = request.headers["authorization"][7:].strip()
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(pairing.DEVICE_COOKIE, token, max_age=400 * 86400, httponly=True, samesite="strict", secure=True)
    _set_install_cookie(response, request)
    return response


@app.get("/api/devices")
def devices_list(request: Request):
    local_only(request)
    return {"devices": [pairing.public(d) for d in pairing.devices()], "lan": pairing.state(), "apk": apk_info()}


@app.post("/api/devices/{dev_id}/revoke")
def devices_revoke(dev_id: str, request: Request):
    local_only(request)
    if not pairing.revoke(dev_id):
        raise HTTPException(404, "No such device.")
    return {"ok": True}


@app.post("/api/devices/lan")
def devices_lan(body: PluginSwitch, request: Request):
    local_only(request)
    return pairing.set_lan(body.on, app)


def security_status() -> list[dict]:
    """Settings > Security: quick live checks (ok True = fine, False = act, None = for your information)."""
    from airtac import deps
    out = []
    st = pairing.state()
    devices = pairing.devices()
    out.append({"name": "Who can reach AIRTAC", "ok": True if not st["running"] else None,
                "detail": "Only this PC (phone access is off)." if not st["running"] else
                f"This PC, and paired phones on Wi-Fi (HTTPS, pinned certificate, {len(devices)} paired). Turn phone access off when you don't need it."})
    out.append({"name": "Passcode for other devices", "ok": True if config.PASSCODE else None,
                "detail": "Set: a browser on another device must sign in." if config.PASSCODE else
                "Not set: only this PC and paired phones can use AIRTAC (nobody else can sign in)."})
    try:
        from plugins.passwords import store
        has_vault = store.exists()
    except Exception:
        has_vault = False
    out.append({"name": "Password vault", "ok": has_vault, "detail": "Your keys and logins are encrypted (Argon2id + AES-256)." if has_vault
                else "Not created: Passwords > Create vault, so keys aren't kept as plain text."})
    env = (config.ROOT / ".env").exists()
    out.append({"name": "Keys in .env", "ok": not env, "detail": "No .env file." if not env else
                ".env exists (plain text). Move its keys to APIs and keys (the vault), then empty .env."})
    missing = deps.missing()
    out.append({"name": "Packages", "ok": not missing, "detail": "All installed." if not missing else f"Missing: {', '.join(missing)} (repair_airtac.cmd)."})
    out.append({"name": "Websites and other apps", "ok": True, "detail": "Blocked: every request needs this install's token; other sites, "
                "DNS rebinding and framing are refused; AIRTAC 1 refuses web pages too."})
    return out


@app.get("/api/security")
def security(request: Request):
    local_only(request)
    return {"checks": security_status()}


@app.post("/api/security/scan")
def security_scan(request: Request):
    """The full check (scripts/security_check.py: pip-audit, bandit, gitleaks). Fixed command, this PC only."""
    import subprocess  # nosec B404 (fixed command)
    import sys
    local_only(request)
    try:
        r = subprocess.run([sys.executable, str(config.ROOT / "scripts" / "security_check.py")], cwd=str(config.ROOT),  # noqa: S603  # nosec B603
                           capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "The check took over 10 minutes. Run scripts\\security_check.py in a window to see why.")
    lines = [l for l in (r.stdout or "").splitlines() if l.startswith(("PASS", "FAIL", "SKIP")) or "No problems" in l or "problem" in l.lower()]
    return {"ok": r.returncode == 0, "lines": lines[-20:]}


@app.get("/api/devices/qr")
def devices_qr(request: Request):
    """Settings' scanner: a QR code the phone's camera opens, to download the app (or open the web app) over Wi-Fi.
    Needs phone access on Wi-Fi switched on (the HTTPS listener)."""
    local_only(request)
    st, apk = pairing.state(), apk_info()
    if not st["running"] or not st["ips"]:
        return {"ready": False, "lan": st, "apk": apk}
    url = f"https://{st['ips'][0]}:{st['port']}" + ("/download/airtac.apk" if apk["ready"] else "/")
    return {"ready": True, "url": url, "kind": "apk" if apk["ready"] else "web", "qr_svg": pairing.qr_svg(url),
            "fingerprint": st["fingerprint"], "apk": apk}


APK = config.ROOT / "mobile" / "release" / "airtac.apk"


def apk_info() -> dict:
    """The phone app's file, if a build has been put in mobile/release/, with its SHA-256 to check the download."""
    if not APK.is_file():
        return {"ready": False, "path": "mobile/release/airtac.apk"}
    stat = APK.stat()
    key = f"{stat.st_size}:{int(stat.st_mtime)}"
    if _apk_hash.get("key") != key:
        h = hashlib.sha256()
        with APK.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        _apk_hash.update(key=key, sha256=h.hexdigest())
    return {"ready": True, "size": stat.st_size, "sha256": _apk_hash["sha256"], "url": "/download/airtac.apk"}


_apk_hash: dict = {}


@app.get("/download/airtac.apk")
def download_apk():
    if not APK.is_file():
        raise HTTPException(404, "The phone app hasn't been built yet. See docs/MOBILE.md.")
    return FileResponse(APK, media_type="application/vnd.android.package-archive", filename="airtac.apk")


# ---------------------------------------------------------------- plugin routes and the page

for _p in plugins.enabled():
    _hook = _p.hooks.get("routes")
    if _hook:
        _router = APIRouter(prefix=f"/api/plugins/{_p.id}", tags=[_p.id])
        _hook(_router)
        app.include_router(_router)

app.mount("/", StaticFiles(directory=config.WEB_DIR, html=True), name="web")  # last, so /api wins
