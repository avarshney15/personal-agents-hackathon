"""The real Google Chrome app as the agent's browser (BROWSER=chrome, the default).

Playwright's bundled Chromium announces itself as automated, so Luma's "Verify you are human"
check never clears there. Instead the agent starts the ordinary Chrome app once, on its own
profile with a local debugging port, and connects to it. You sign in to Luma in that window
yourself, ticking the human check by hand; the agent then works in a new tab of the same window,
where you can watch it. The agent never ticks a human check itself: those hand back to you.

Env: CHROME_PATH (the macOS app by default), CHROME_PROFILE_DIR (~/.event-agent/chrome),
CHROME_DEBUG_PORT (9222).
"""

from __future__ import annotations

import os
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

DEFAULT_CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def chrome_path() -> str:
    return os.getenv("CHROME_PATH", "").strip() or DEFAULT_CHROME


def profile_dir() -> Path:
    # Chrome refuses remote debugging on its default profile, so the agent keeps its own.
    path = Path(os.getenv("CHROME_PROFILE_DIR", "~/.event-agent/chrome")).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def cdp_url() -> str:
    return f"http://127.0.0.1:{int(os.getenv('CHROME_DEBUG_PORT', '9222'))}"


def is_running() -> bool:
    try:
        with urllib.request.urlopen(f"{cdp_url()}/json/version", timeout=1) as resp:
            return resp.status == 200
    except Exception:
        return False


def ensure_running(open_url: Optional[str] = None, timeout: float = 20.0) -> str:
    """Starts the Chrome app on the agent's profile if it isn't up yet; returns the CDP URL.
    With open_url, that page opens in a tab (a new window on first start)."""
    if is_running():
        if open_url:
            target = f"{cdp_url()}/json/new?{urllib.parse.quote(open_url, safe=':/')}"
            urllib.request.urlopen(urllib.request.Request(target, method="PUT"), timeout=5).close()
        return cdp_url()

    exe = chrome_path()
    if not Path(exe).exists():
        raise RuntimeError(f"Google Chrome isn't at {exe}. Install it or set CHROME_PATH.")
    port = urllib.parse.urlparse(cdp_url()).port
    args = [exe, f"--remote-debugging-port={port}", f"--user-data-dir={profile_dir()}",
            "--no-first-run", "--no-default-browser-check", "--window-size=1300,940"]
    if open_url:
        args.append(open_url)
    subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)  # keeps running after the server or script exits
    deadline = time.monotonic() + timeout
    while not is_running():
        if time.monotonic() > deadline:
            raise RuntimeError("Chrome started, but its debugging port never answered. "
                               f"Is another app using port {port}?")
        time.sleep(0.3)
    return cdp_url()
