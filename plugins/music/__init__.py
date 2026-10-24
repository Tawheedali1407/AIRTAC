"""Music: play songs from your own Music folder, in the page (so it works on your phone too).
No Spotify, no account: the files you already have. Formats your browser can play: MP3, M4A,
OGG, WAV, FLAC (most browsers)."""
import hashlib
import random
import re
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse

from airtac.plugin_api import Tool, ToolResult, after, plugin, schema

p = plugin(__name__)
TYPES = {".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".ogg": "audio/ogg", ".wav": "audio/wav", ".flac": "audio/flac",
         ".opus": "audio/ogg"}
_index: dict[str, Path] = {}


def folder() -> Path:
    chosen = p.setting("folder", "")
    return Path(chosen).expanduser() if chosen else Path.home() / "Music"


def scan(limit: int = 5000) -> dict[str, Path]:
    """Song id -> file. The id is a hash of the path, so a link can't name any other file."""
    _index.clear()
    root = folder()
    if root.exists():
        for f in root.rglob("*"):
            if f.suffix.lower() in TYPES and f.is_file():
                _index[hashlib.sha1(str(f).encode(), usedforsecurity=False).hexdigest()[:16]] = f
                if len(_index) >= limit:
                    break
    return _index


def find(query: str) -> list[tuple[str, Path]]:
    songs = scan() if not _index else _index
    words = [w for w in re.findall(r"[a-z0-9]+", (query or "").lower()) if w not in ("play", "some", "song", "songs", "music", "me", "a", "the", "my")]
    if not words:
        return list(songs.items())
    scored = sorted(((sum(w in str(f).lower() for w in words), sid, f) for sid, f in songs.items()), reverse=True)
    return [(sid, f) for score, sid, f in scored if score > 0]


def play_music(query: str = "") -> ToolResult:
    found = find(query)
    if not found:
        where = folder()
        return ToolResult(f"I didn't find {'that' if query else 'any music'} in your Music folder.",
                          f"Looked in {where}. Set another folder under [plugins.music] in config/airtac.toml.", ok=False)
    sid, f = found[0] if query else random.choice(found)
    return ToolResult(f"Playing {f.stem}.", f"Now playing: {f.stem}\nAudio: /api/plugins/music/play/{sid}",
                      data={"audio": f"/api/plugins/music/play/{sid}", "title": f.stem})


p.add(Tool("play_music", "Play a song from the user's own Music folder by name, artist or mood words.",
           schema({"query": {"type": "string", "description": "Song, artist or words in the file name; empty for any"}}),
           "local", play_music))


@p.routes
def api(router) -> None:
    @router.get("/play/{song_id}")
    def play(song_id: str):
        f = _index.get(song_id) or scan().get(song_id)
        if f is None or not f.is_file():
            raise HTTPException(404, "That song isn't in your Music folder any more.")
        return FileResponse(f, media_type=TYPES[f.suffix.lower()])


@p.dashboard
def page(pg: dict) -> None:
    songs = scan()
    pg["stats"] = [{"label": "Songs found", "value": len(songs)}]
    pg["lists"] = [{"title": f"In {folder()}", "empty": "No songs found. Set the folder in config/airtac.toml.",
                    "items": [{"title": f.stem, "meta": f.suffix[1:].upper()} for f in list(songs.values())[:12]]}]


p.mock_rule(27, ("play", "music", "song"), "play_music", lambda t, m: {"query": after(t, r"\bplay\b")})
