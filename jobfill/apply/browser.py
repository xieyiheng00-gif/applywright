"""A visible Chromium that outlives each jobfill command.

`apply fill` connects over the Chrome DevTools Protocol, fills, and disconnects; the window stays
open so the candidate can watch, correct and submit, and the next command picks up the same tab.
The profile directory keeps cookies, so a login done once by the candidate is reused.

Chromium picks its own debugging port (``--remote-debugging-port=0``): any fixed port can fall inside
a block Windows reserves for Hyper-V / WSL2 / Docker, and those blocks move on reboot. The port is read
from the ``DevToolsActivePort`` file Chromium writes in the profile, and kept in
``data/apply/browser.json`` so every later command (the agent's or the candidate's terminal) finds the
same window. ``JOBFILL_CDP_PORT`` still forces a fixed port.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
import urllib.request
from contextlib import contextmanager
from pathlib import Path

LAUNCH_TIMEOUT = 20.0


def endpoint(port: int) -> str:
    return f"http://127.0.0.1:{port}"


def alive(port: int | None) -> bool:
    if not port:
        return False
    try:
        with urllib.request.urlopen(endpoint(port) + "/json/version", timeout=1):
            return True
    except Exception:
        return False


def _state_path(data_dir: Path) -> Path:
    return Path(data_dir) / "apply" / "browser.json"


def _read_state(data_dir: Path) -> dict:
    try:
        return json.loads(_state_path(data_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _fixed_port() -> int | None:
    value = os.environ.get("JOBFILL_CDP_PORT")
    return int(value) if value else None


def reserved_by_windows(port: int) -> bool:
    """Is ``port`` inside a range WinNAT reserved (Hyper-V, WSL2, Docker Desktop, Windows Sandbox)?"""
    if os.name != "nt":
        return False
    try:
        out = subprocess.run(["netsh", "int", "ipv4", "show", "excludedportrange", "protocol=tcp"],
                             capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return any(int(a) <= port <= int(b) for a, b in re.findall(r"^\s*(\d+)\s+(\d+)", out, re.M))


def _reserved_message(port: int) -> str:
    return (f"port {port} is reserved by Windows (see `netsh int ipv4 show excludedportrange protocol=tcp`); "
            "unset JOBFILL_CDP_PORT or pick another")


def _kill(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    else:
        try:
            os.killpg(pid, 15)
        except OSError:
            pass


def chrome_path() -> str:
    if os.environ.get("JOBFILL_CHROME"):
        return os.environ["JOBFILL_CHROME"]
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        return p.chromium.executable_path


def ensure(data_dir: Path, headless: bool = False, timeout: float = LAUNCH_TIMEOUT) -> str:
    """Return the CDP endpoint of the shared browser, starting it if needed."""
    data_dir = Path(data_dir)
    fixed = _fixed_port()
    port = fixed or _read_state(data_dir).get("port")
    if alive(port):
        return endpoint(port)
    if fixed and reserved_by_windows(fixed):
        raise RuntimeError(_reserved_message(fixed))

    profile = Path(os.environ.get("JOBFILL_BROWSER_PROFILE", data_dir / "browser-profile"))
    profile.mkdir(parents=True, exist_ok=True)
    active = profile / "DevToolsActivePort"
    active.unlink(missing_ok=True)   # a stale copy would point at a port nobody listens on
    args = [chrome_path(), f"--remote-debugging-port={fixed or 0}", f"--user-data-dir={profile}",
            "--no-first-run", "--no-default-browser-check", "--disable-features=Translate", "about:blank"]
    if headless:
        args.insert(1, "--headless=new")
    kwargs = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
              "close_fds": True}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(args, **kwargs)

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        code = proc.poll()
        if code is not None:
            # Usually another Chromium still holds this profile and took the launch over.
            raise RuntimeError(f"Chromium exited right away (exit code {code}): another jobfill window probably "
                               "still holds the browser profile. Run `python -m jobfill apply close` and try again.")
        try:
            port = int(active.read_text(encoding="utf-8").split()[0])
        except (OSError, ValueError, IndexError):
            port = None
        if port and alive(port):
            state = _state_path(data_dir)
            state.parent.mkdir(parents=True, exist_ok=True)
            state.write_text(json.dumps({"pid": proc.pid, "port": port, "profile": str(profile)}), encoding="utf-8")
            return endpoint(port)
        time.sleep(0.25)

    # Never leave a Chromium without a control port running: it would keep the profile locked.
    _kill(proc.pid)
    if fixed:
        why = _reserved_message(fixed) if reserved_by_windows(fixed) else \
            f"port {fixed} could not be opened (is something else using it?); unset JOBFILL_CDP_PORT or pick another"
        raise RuntimeError(f"Chromium did not start its debugging port: {why}. The window was closed.")
    raise RuntimeError(f"Chromium did not report a debugging port within {timeout:g} seconds, so jobfill closed it. "
                       "Try again; if it keeps failing, run `python -m jobfill apply close` first.")


def close(data_dir: Path) -> bool:
    state = _state_path(data_dir)
    if not state.exists():
        return False
    pid = _read_state(data_dir).get("pid")
    state.unlink()
    if not pid:
        return False
    _kill(pid)
    return True


@contextmanager
def session(data_dir: Path, headless: bool = False):
    """Yield the shared browser context; disconnecting leaves the window open."""
    from playwright.sync_api import sync_playwright
    ep = ensure(data_dir, headless)
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(ep)
        yield browser.contexts[0] if browser.contexts else browser.new_context()


TAG_PREFIX = "jobfill-app-"   # window.name of each application's tab: jobfill-app-<id> (runner.start)


def tagged_page(context, tag: str):
    """The tab of this application, or None if it was closed (never opens one)."""
    for page in context.pages:
        try:
            if page.evaluate("() => window.name") == tag:
                return page
        except Exception:
            continue
    return None


def find_page(context, tag: str, url: str):
    """The tab used for this application: tagged earlier, else an untagged tab on the same site (one the
    candidate opened), else a new tab. A tab tagged for another application is never taken: every Ashby
    or Greenhouse job shares one host, and filling that tab would put this application's answers into
    another company's form."""
    from urllib.parse import urlparse
    host = urlparse(url).netloc
    same_site = None
    for page in context.pages:
        try:
            name = page.evaluate("() => window.name")
        except Exception:
            continue
        if name == tag:
            return page, False
        if str(name or "").startswith(TAG_PREFIX):
            continue
        if urlparse(page.url).netloc == host:
            same_site = page
    if same_site is not None:
        same_site.evaluate("(t) => { window.name = t; }", tag)
        return same_site, False
    blank = next((p for p in context.pages if p.url in ("about:blank", "chrome://newtab/")), None)
    page = blank or context.new_page()
    return page, True
