"""Private speech on this laptop: faster-whisper hears you, Piper speaks.

"browser" (the default) leaves speech to the page: simple, but Chrome and Edge send your audio
to their cloud to recognise it. "local" keeps it here. Both engines run on the CPU:
  - faster-whisper (MIT), model base.en: about 150 MB, a few seconds per sentence on an i3.
  - Piper (GPL-3.0 since 2025, installed separately as piper-tts): a voice is a ~60 MB .onnx file.
Install with:  powershell -File scripts\\plugins.ps1 voice
"""
import importlib.util
import io
import logging
import wave
from pathlib import Path

from airtac import config

log = logging.getLogger("airtac.voice")
_whisper = None
_piper = None


def status() -> dict:
    stt_ok = config.STT != "local" or importlib.util.find_spec("faster_whisper") is not None
    voice = Path(config.PIPER_VOICE) if config.PIPER_VOICE else None
    if voice and not voice.is_absolute():
        voice = config.ROOT / voice
    tts_ok = config.TTS != "local" or (importlib.util.find_spec("piper") is not None and voice is not None and voice.exists())
    reasons = []
    if not stt_ok:
        reasons.append("pip install faster-whisper")
    if not tts_ok:
        reasons.append("install piper-tts and set [voice] piper_voice to a .onnx voice file")
    return {"stt": config.STT, "tts": config.TTS, "stt_ready": stt_ok, "tts_ready": tts_ok, "reason": "; ".join(reasons)}


def transcribe(audio: bytes) -> str:
    """Speech to text with faster-whisper (int8 on the CPU). audio: webm/ogg/wav bytes from the page."""
    global _whisper
    if _whisper is None:
        from faster_whisper import WhisperModel  # imported here: big, and optional
        _whisper = WhisperModel(config.WHISPER_MODEL, device="cpu", compute_type="int8",
                                download_root=str(config.MODELS_DIR / "whisper"))
    segments, _info = _whisper.transcribe(io.BytesIO(audio), language=config.LANG.split("-")[0], beam_size=1,
                                          vad_filter=True)
    return " ".join(s.text.strip() for s in segments).strip()


def synthesize(text: str) -> bytes:
    """Text to speech with Piper. Returns WAV bytes."""
    global _piper
    if _piper is None:
        from piper import PiperVoice  # imported here: optional
        path = Path(config.PIPER_VOICE)
        _piper = PiperVoice.load(str(path if path.is_absolute() else config.ROOT / path))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        _piper.synthesize_wav(text, wav)
    return buf.getvalue()
