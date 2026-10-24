"""Part H: the server log rotates (1 MB, 3 old copies), so data\\logs never fills the disk."""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import serve  # noqa: E402


def test_browser_closing_a_connection_is_not_logged_as_an_error():
    f = serve._ClosedByBrowser()
    try:
        raise ConnectionResetError(10054, "An existing connection was forcibly closed by the remote host")
    except ConnectionResetError:
        noise = logging.LogRecord("asyncio", logging.ERROR, "", 0,
                                  "Exception in callback _ProactorBasePipeTransport._call_connection_lost()",
                                  None, sys.exc_info())
    real = logging.LogRecord("asyncio", logging.ERROR, "", 0, "Task exception was never retrieved", None, None)
    assert not f.filter(noise) and f.filter(real)


def test_server_log_rotates(tmp_path, monkeypatch):
    logs = tmp_path / "logs"
    monkeypatch.setattr(serve, "LOG_DIR", logs)
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        log = serve.setup_logging()
        line = "x" * 1000
        for _ in range(4500):                     # about 4.5 MB
            log.info(line)
        files = sorted(p.name for p in logs.iterdir())
        assert files == ["server.log", "server.log.1", "server.log.2", "server.log.3"]
        assert all((logs / f).stat().st_size <= 1_000_000 + 2000 for f in files)
    finally:
        for h in list(root.handlers):
            if h not in before:
                root.removeHandler(h)
                h.close()
