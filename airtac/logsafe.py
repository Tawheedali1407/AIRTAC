"""Keep secrets out of log lines.

Code is written never to log a key, but a third-party library (or an exception message) can still
print one, for example a URL with ?api_key=... in it. This filter sits on every log handler and
replaces anything that looks like a secret with [hidden] before the line is written:
- the values of the keys saved in AIRTAC right now (config.SAVED and the brain keys in the environment);
- the usual key shapes (sk-..., gsk_..., AIza..., hf_..., ghp_..., "Bearer ...", api_key=...).
install() is called once at start-up (airtac/main.py), after scripts/serve.py has added its file handler.
"""
import logging
import os
import re

HIDDEN = "[hidden]"
PATTERNS = [
    re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"(?i)([?&](?:api[_-]?key|key|token|access_token|apikey|sig|signature|code_verifier)=)[^&\s\"']+"),
    re.compile(r"(?i)((?:api[_-]?key|secret|password|passcode|token)[\"']?\s*[:=]\s*[\"']?)[^\s\"',}]{6,}"),
    re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{16,}"),            # OpenAI, Anthropic (sk-ant-), DeepSeek, Together
    re.compile(r"\b(?:gsk|hf|ghp|gho|ghu|ghs|github_pat|xai|tvly|ctx7sk|ctx7op|sb_secret|sb_publishable)[-_][A-Za-z0-9_-]{10,}"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}"),                       # Google API keys
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),                  # AWS access key ids
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),   # JWTs
]


def _known_values() -> list[str]:
    """The secrets AIRTAC holds right now, longest first (so a long key is hidden whole)."""
    from airtac import config
    values = {v for v in config.SAVED.values() if isinstance(v, str) and len(v) >= 8}
    for name, value in os.environ.items():
        if value and len(value) >= 8 and name.endswith(("_KEY", "_TOKEN", "_SECRET", "PASSCODE")):
            values.add(value)
    return sorted(values, key=len, reverse=True)


def redact(text: str) -> str:
    if not text:
        return text
    for value in _known_values():
        if value in text:
            text = text.replace(value, HIDDEN)
    for pattern in PATTERNS:
        text = pattern.sub(lambda m: (m.group(1) + (" " if m.group(1).lower() in ("bearer", "basic") else "") + HIDDEN)
                           if m.groups() else HIDDEN, text)
    return text


def _clean_arg(a):
    if isinstance(a, (int, float)) or a is None:
        return a
    text = str(a)
    clean = redact(text)
    return clean if clean != text or isinstance(a, str) else a


class RedactFilter(logging.Filter):
    """Cleans the message and each argument separately, so formatters that read record.args
    (uvicorn's access log does) keep working."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(_clean_arg(a) for a in record.args)
        elif isinstance(record.args, dict):
            record.args = {k: _clean_arg(v) for k, v in record.args.items()}
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = redact(record.exc_text)
        return True


_FILTER = RedactFilter()


def install() -> None:
    """Put the filter on every handler (root and named loggers), and quieten the libraries that print URLs."""
    for name in ("httpx", "httpcore", "urllib3", "hpack"):
        logging.getLogger(name).setLevel(logging.WARNING)
    loggers = [logging.getLogger()] + [l for l in logging.Logger.manager.loggerDict.values() if isinstance(l, logging.Logger)]
    for logger in loggers:
        for handler in logger.handlers:
            if _FILTER not in handler.filters:
                handler.addFilter(_FILTER)
