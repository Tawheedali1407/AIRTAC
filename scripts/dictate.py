"""AIRTAC dictation, like Wispr Flow (Ali, 18 Oct): hold a key anywhere in Windows, speak, let go, and the words are
typed where your cursor is. Speech to text runs with faster-whisper and the Whisper model on this PC (models\\whisper),
so your voice never leaves it.

Started by AIRTAC (Settings > Dictation, or at login when it's switched on); stops when the file
data\\dictation.stop appears. Settings come from data\\dictation.json (written by plugins/dictation):
    {"key": "right ctrl", "language": "en", "clean": true, "model": "base.en"}

Needs (requirements.txt): sounddevice (the microphone), keyboard (the hold-to-talk key and typing), faster-whisper.
"""
import json
import os
import re
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SETTINGS = DATA / "dictation.json"
STATE = DATA / "dictation.state"
STOP = DATA / "dictation.stop"
RATE = 16000
MAX_SECONDS = 120          # one hold, at most two minutes
MIN_SECONDS = 0.35         # a tap is not speech

FILLERS = re.compile(r"\b(um+|uh+|erm+|hmm+|uh-huh)\b[,.]?\s*", re.IGNORECASE)


def settings() -> dict:
    try:
        found = json.loads(SETTINGS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        found = {}
    return {"key": found.get("key") or "right ctrl", "language": found.get("language") or "en",
            "clean": found.get("clean", True), "model": found.get("model") or "base.en"}


def tidy(text: str, clean: bool = True) -> str:
    """Whisper's text, tidied the way Wispr Flow does the basics: no "um"/"uh", no doubled spaces, a capital first
    letter. Nothing is reworded."""
    text = " ".join(text.split())
    if clean:
        text = FILLERS.sub("", text)
        text = re.sub(r"\b(\w+)( \1\b)+", r"\1", text, flags=re.IGNORECASE)   # "the the" -> "the"
        text = " ".join(text.split()).strip(" ,")
        if text:
            text = text[0].upper() + text[1:]
    return text


def write_state(**fields) -> None:
    try:
        old = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    except (OSError, ValueError):
        old = {}
    old.update(fields, pid=os.getpid(), at=time.time())
    try:
        STATE.write_text(json.dumps(old), encoding="utf-8")
    except OSError:
        pass


def beep(freq: int) -> None:
    try:
        import winsound
        winsound.Beep(freq, 70)
    except Exception:
        pass


class Dictation:
    def __init__(self, cfg: dict):
        import numpy as np
        import sounddevice as sd
        from faster_whisper import WhisperModel
        self.np, self.sd, self.cfg = np, sd, cfg
        self.model = WhisperModel(cfg["model"], device="cpu", compute_type="int8",
                                  download_root=str(ROOT / "models" / "whisper"))
        self.frames: list = []
        self.recording = False
        self.lock = threading.Lock()
        self.stream = sd.InputStream(samplerate=RATE, channels=1, dtype="float32", callback=self._audio)
        self.stream.start()

    def _audio(self, data, _frames, _time, _status):
        if self.recording and len(self.frames) * len(data) < RATE * MAX_SECONDS:
            self.frames.append(data.copy())

    def start(self):
        if self.recording:
            return
        self.frames = []
        self.recording = True
        beep(880)
        write_state(status="listening")

    def stop(self):
        if not self.recording:
            return
        self.recording = False
        beep(660)
        frames, self.frames = self.frames, []
        threading.Thread(target=self._type, args=(frames,), daemon=True).start()

    def _type(self, frames):
        import keyboard
        if not frames:
            return
        audio = self.np.concatenate(frames)[:, 0]
        if len(audio) < RATE * MIN_SECONDS:
            write_state(status="ready")
            return
        with self.lock:
            write_state(status="writing")
            lang = self.cfg["language"] if not self.cfg["model"].endswith(".en") else "en"
            segments, _ = self.model.transcribe(audio, language=lang, beam_size=1, vad_filter=True)
            text = tidy(" ".join(s.text for s in segments), self.cfg["clean"])
        if text:
            keyboard.write(text + " ", delay=0)
        write_state(status="ready", last_chars=len(text), last_at=time.time())


def main() -> int:
    DATA.mkdir(exist_ok=True)
    STOP.unlink(missing_ok=True)
    cfg = settings()
    try:
        import keyboard
        d = Dictation(cfg)
    except Exception as e:      # a missing package or no microphone: say why on the Settings page
        write_state(status="error", error=f"{type(e).__name__}: {e}"[:300])
        return 1
    key = cfg["key"]
    keyboard.on_press_key(key, lambda _e: d.start(), suppress=False)
    keyboard.on_release_key(key, lambda _e: d.stop(), suppress=False)
    write_state(status="ready", key=key, error="")
    while not STOP.exists():
        time.sleep(0.5)
    STOP.unlink(missing_ok=True)
    keyboard.unhook_all()
    d.stream.stop()
    write_state(status="stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
