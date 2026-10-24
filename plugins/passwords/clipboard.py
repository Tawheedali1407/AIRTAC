"""Copy a user name or password to the Windows clipboard, and clear it again after 20 seconds.

Why AIRTAC does this itself instead of the web page: a browser tab may only change the clipboard while
it has focus. You copy, switch to Chrome to paste, and the AIRTAC tab can no longer clear it. AIRTAC on
the PC can. As a bonus the password never even reaches the page.

The copy is also marked so Windows keeps it out of clipboard history (Win+V) and cloud clipboard sync,
the same way password managers do it. It's cleared after 20 seconds only if it is still on the clipboard,
so something you copied since then is left alone.

Windows only (ctypes, no package). Elsewhere `available()` is False and the page copies instead.
"""
import ctypes
import sys
import threading
import time

CLEAR_AFTER = 20
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
_timer: threading.Timer | None = None


def available() -> bool:
    return sys.platform == "win32"


def _api():
    from ctypes import wintypes as w
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.OpenClipboard.argtypes, user32.OpenClipboard.restype = [w.HWND], w.BOOL
    user32.CloseClipboard.argtypes, user32.CloseClipboard.restype = [], w.BOOL
    user32.EmptyClipboard.argtypes, user32.EmptyClipboard.restype = [], w.BOOL
    user32.SetClipboardData.argtypes, user32.SetClipboardData.restype = [w.UINT, w.HANDLE], w.HANDLE
    user32.GetClipboardData.argtypes, user32.GetClipboardData.restype = [w.UINT], w.HANDLE
    user32.RegisterClipboardFormatW.argtypes, user32.RegisterClipboardFormatW.restype = [w.LPCWSTR], w.UINT
    kernel32.GlobalAlloc.argtypes, kernel32.GlobalAlloc.restype = [w.UINT, ctypes.c_size_t], w.HGLOBAL
    kernel32.GlobalLock.argtypes, kernel32.GlobalLock.restype = [w.HGLOBAL], ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes, kernel32.GlobalUnlock.restype = [w.HGLOBAL], w.BOOL
    return user32, kernel32


def _open(user32) -> None:
    for _ in range(20):                 # another program may hold the clipboard for a moment
        if user32.OpenClipboard(None):
            return
        time.sleep(0.05)
    raise OSError("The clipboard is busy.")


def _global(kernel32, data: bytes):
    handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    if not handle:
        raise OSError("Out of memory for the clipboard.")
    ptr = kernel32.GlobalLock(handle)
    ctypes.memmove(ptr, data, len(data))
    kernel32.GlobalUnlock(handle)
    return handle


def _set(text: str) -> None:
    user32, kernel32 = _api()
    _open(user32)
    try:
        user32.EmptyClipboard()
        user32.SetClipboardData(CF_UNICODETEXT, _global(kernel32, (text + "\0").encode("utf-16-le")))
        zero = (0).to_bytes(4, "little")  # a DWORD 0: "no" to clipboard history and to cloud sync
        for name in ("ExcludeClipboardContentFromMonitorProcessing", "CanIncludeInClipboardHistory", "CanUploadToCloudClipboard"):
            fmt = user32.RegisterClipboardFormatW(name)
            if fmt:
                user32.SetClipboardData(fmt, _global(kernel32, zero))
    finally:
        user32.CloseClipboard()


def _get() -> str:
    user32, kernel32 = _api()
    _open(user32)
    try:
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return ""
        ptr = kernel32.GlobalLock(handle)
        try:
            return ctypes.wstring_at(ptr) if ptr else ""
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def _clear_if(text: str) -> None:
    try:
        if _get() == text:
            user32, _ = _api()
            _open(user32)
            try:
                user32.EmptyClipboard()
            finally:
                user32.CloseClipboard()
    except OSError:
        pass


def copy(text: str, clear_after: int = CLEAR_AFTER) -> bool:
    """Put text on the clipboard and clear it after clear_after seconds. False if this PC can't
    (not Windows, or the clipboard stayed busy): the page copies it instead."""
    global _timer
    if not available():
        return False
    try:
        _set(text)
    except OSError:
        return False
    if _timer:
        _timer.cancel()
    _timer = threading.Timer(clear_after, _clear_if, args=(text,))
    _timer.daemon = True
    _timer.start()
    return True
