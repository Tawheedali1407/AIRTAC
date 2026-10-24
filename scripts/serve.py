"""Runs the AIRTAC server and keeps a log you can read afterwards.
    .venv\\Scripts\\python.exe scripts\\serve.py [--port 8765] [--host 127.0.0.1]

Why not plain `uvicorn airtac.main:app`: the start script used to send only stderr to data/server.log
and overwrote it on every start, so when AIRTAC stopped there was no trace of why. Here every line
(AIRTAC's and uvicorn's, including a port clash or a crash) goes to data/logs/server.log, appended and
rotated at 1 MB with 3 old copies kept, plus a line when the server starts and stops.
"""
import argparse
import logging
import logging.handlers
import os
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
LOG_DIR = ROOT / "data" / "logs"
FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def setup_logging() -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(LOG_DIR / "server.log", maxBytes=1_000_000, backupCount=3,
                                                   encoding="utf-8")
    handler.setFormatter(logging.Formatter(FORMAT))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter(FORMAT))
    root.addHandler(console)
    return logging.getLogger("airtac.serve")


class _ClosedByBrowser(logging.Filter):
    """Windows' asyncio logs a full traceback (WinError 10054) every time the browser closes a connection
    first, e.g. when the AIRTAC window is closed or a page reloads mid-request. Harmless, but it filled
    server.log with hundreds of ERROR lines (115 on 20 Oct alone) and hid the real errors. Drop just those."""

    def filter(self, record: logging.LogRecord) -> bool:
        exc = record.exc_info[1] if record.exc_info else None
        return not (isinstance(exc, ConnectionResetError) and "_call_connection_lost" in record.getMessage())


def install_reset_filter() -> None:
    logging.getLogger("asyncio").addFilter(_ClosedByBrowser())


def config_port() -> int:
    try:
        with open(ROOT / "config" / "airtac.toml", "rb") as f:
            return int(tomllib.load(f).get("server", {}).get("port", 8765))
    except (OSError, tomllib.TOMLDecodeError, ValueError):
        return 8765   # a broken file is reported below, when the app loads it


def main() -> int:
    ap = argparse.ArgumentParser(description="Run the AIRTAC server with a log file.")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=0)
    args = ap.parse_args()
    port = args.port or config_port()
    log = setup_logging()
    log.info("starting AIRTAC on http://%s:%d (pid %d, Python %s)", args.host, port, os.getpid(), sys.version.split()[0])

    os.chdir(ROOT)
    try:
        from airtac.main import app
    except tomllib.TOMLDecodeError as e:
        log.error("config/airtac.toml has a mistake, so AIRTAC can't start: %s", e)
        return 2
    except Exception:
        log.exception("AIRTAC couldn't load")
        return 2

    install_reset_filter()
    import uvicorn
    # log_config=None: uvicorn's own loggers pass their lines to the handlers above instead of their own.
    server = uvicorn.Server(uvicorn.Config(app, host=args.host, port=port, log_config=None))
    try:
        server.run()
    except SystemExit as e:   # uvicorn exits this way when it can't bind the port
        log.error("AIRTAC stopped (exit %s). If the port is busy, another AIRTAC may already be running: "
                  "run scripts\\stop.ps1 or start_airtac.bat.", e.code)
        return 1
    except Exception:
        log.exception("AIRTAC crashed")
        return 1
    # uvicorn returns normally after a failed bind too; started is only set once it was listening.
    if not server.started:
        log.error("AIRTAC didn't start: port %d is probably in use. See the lines above.", port)
        return 1
    log.info("AIRTAC stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
