"""Data shapes shared across the core: tool results and the JSON API bodies.
Plugins that need their own request bodies define them next to their routes."""
import re
from dataclasses import dataclass

from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

SPOKEN_LIMIT = 300
SCREEN_LIMIT = 1500


@dataclass
class ToolResult:
    spoken: str        # 1 to 3 short sentences, read aloud: no markdown, no URLs
    screen: str = ""   # details shown on the page
    ok: bool = True    # False when a live call failed (the page colours the step red)
    data: dict | None = None  # optional raw results for other code (Work mode reads links and pages)


_URL = re.compile(r"https?://\S+|www\.\S+")


def clean_spoken(text: str, limit: int = SPOKEN_LIMIT) -> str:
    """Make text safe to read aloud: no URLs, no markdown symbols, short."""
    text = _URL.sub("", text)
    text = re.sub(r"[*#`>|_]+", "", text)  # these sound odd when spoken
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        text = text[: limit - 3].rsplit(" ", 1)[0].rstrip(",;:") + "..."
    return text


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    session_id: str = Field(default="default", max_length=64)
    # A problem pasted with the "Paste problem" button (LeetCode / CodeChef / GeeksforGeeks text).
    problem_text: str | None = Field(default=None, max_length=12000)
    site: str | None = Field(default=None, max_length=20)
    language: str | None = Field(default=None, max_length=20)
    # a brain picked in the chat header: "groq", or "groq:<model>", or "ollama:<model>" (checked against the menu's lists)
    brain: str | None = Field(default=None, max_length=160, pattern=r"^[a-z0-9_-]*(:[A-Za-z0-9._/:@+-]{1,120})?$")
    # Plugins picked in the chat's side panel: None = AIRTAC picks from your words; [] = chat only, no tools.
    plugins: list[Annotated[str, StringConstraints(max_length=40, pattern=r"^[a-z0-9_-]+$")]] | None = Field(default=None, max_length=40)
    # Files attached with the paperclip (ids from POST /api/upload).
    attachments: list[int] | None = Field(default=None, max_length=10)
    # "chat": the Chat page (longer markdown answers that type out live); "voice": Home, phone, wake word.
    mode: Literal["voice", "chat"] = "voice"
    # A section made in Settings > Sections (its instructions, tools and documents are used).
    section: str | None = Field(default=None, max_length=40, pattern=r"^[a-z0-9-]*$")


class ChatStreamRequest(ChatRequest):
    """The Chat page. regenerate: answer the last question again. edit_from: the id of an earlier question of this
    chat; it and everything after it are replaced by this message."""
    message: str = Field(default="", max_length=8000)
    mode: Literal["voice", "chat"] = "chat"
    regenerate: bool = False
    edit_from: int | None = Field(default=None, ge=1)


class ConfirmRequest(BaseModel):
    pending_id: str
    approve: bool


class SpeakRequest(BaseModel):
    text: str = Field(min_length=1, max_length=1500)


class Step(BaseModel):
    tool: str
    args: dict
    status: str  # ok | error | pending | cancelled


class Pending(BaseModel):
    id: str
    tool: str
    args: dict
    summary: str


class ChatResponse(BaseModel):
    spoken: str
    screen: str = ""
    steps: list[Step] = []
    pending: Pending | None = None
    message_id: int | None = None   # for the thumbs up / down buttons
    brain: dict | None = None       # which brain answered: {"role", "label", "model"}
    sources: list[dict] = []        # knowledge passages used: {"title", "where"}
    noticed: list[dict] = []        # things plugins did on their own from what you said, each with an undo
    usage: dict | None = None       # tokens: {"turn": {...}, "session": {"input", "output", "total"}}
    text: str = ""                  # the Chat page's full answer (markdown); empty in the voice style
    user_message_id: int | None = None   # the question's id, for the Chat page's Edit button
    problem: str = ""               # "" | "timeout" | "brain_error" | "stopped": the answer is a message, not a reply
    fallback_brains: list[dict] = []   # cloud brains with a key to try instead: {"id", "label"} (never a key)


class FeedbackRequest(BaseModel):
    message_id: int
    good: bool
    note: str = Field(default="", max_length=500)


class WorkRequest(BaseModel):
    goal: str = Field(min_length=3, max_length=1500)
    skill_id: int | None = None


class WorkConfirm(BaseModel):
    approve: bool


class SkillRequest(BaseModel):
    job_id: str
    name: str = Field(default="", max_length=80)


class ConnectionSave(BaseModel):
    values: dict[str, str | None] = Field(default_factory=dict)   # {"GROQ_API_KEY": "gsk_..."}; empty = keep


class LoginRequest(BaseModel):
    passcode: str = Field(min_length=1, max_length=200)
