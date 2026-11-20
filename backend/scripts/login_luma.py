"""Sign in to Luma once in the agent's Chrome, so the RSVP agent acts as you.

Default (BROWSER=chrome): opens the real Google Chrome app on the agent's own profile
(CHROME_PROFILE_DIR, default ~/.event-agent/chrome) at Luma's sign-in page. Nothing is attached
to the window while you sign in, so Luma's "Verify you are human" check works as in any browser:
tick it yourself, sign in, and leave Chrome open. The agent uses new tabs in that window.

BROWSER=chromium: the old Playwright-bundled browser on BROWSER_PROFILE_DIR. Luma's human
check doesn't clear there, so use it only for tests.

    cd backend && .venv/bin/python scripts/login_luma.py
    .venv/bin/python scripts/login_luma.py --check   # is the agent's Chrome signed in?
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from playwright.sync_api import sync_playwright  # noqa: E402

from app.act import chrome_app  # noqa: E402
from app.act import page_checks as pc  # noqa: E402
from app.act.browser_agent import VIEWPORT  # noqa: E402

SIGN_IN = "https://luma.com/signin"
HOME = "https://luma.com/home"


def luma_cookies(ctx) -> list[str]:
    return [c["name"] for c in ctx.cookies()
            if any(d in c.get("domain", "") for d in ("lu.ma", "luma.com"))]


def _signed_in(page) -> bool:
    page.goto(HOME, wait_until="domcontentloaded", timeout=30000)
    try:
        page.wait_for_load_state("networkidle", timeout=5000)
    except Exception:
        pass
    return pc.signed_in(page.evaluate(pc.SCAN_JS))


# ---------------------------------------------------------------- the real Chrome app


def chrome_login() -> int:
    chrome_app.ensure_running(open_url=SIGN_IN)
    print(f"Chrome is open on Luma's sign-in page (profile {chrome_app.profile_dir()}).")
    print("Tick \"Verify you are human\" yourself, sign in, then leave Chrome open.")
    print("Check it afterwards: .venv/bin/python scripts/login_luma.py --check")
    return 0


def chrome_check() -> int:
    if not chrome_app.is_running():
        print("The agent's Chrome isn't running. Start it and sign in: "
              ".venv/bin/python scripts/login_luma.py")
        return 1
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(chrome_app.cdp_url())
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.new_page()
        try:
            ok = _signed_in(page)
            print(f"{'Signed in' if ok else 'Not signed in'} to Luma in the agent's Chrome "
                  f"(page {page.url}, {len(luma_cookies(ctx))} Luma cookies)")
        finally:
            page.close()
            browser.close()  # disconnects; Chrome keeps running
    return 0 if ok else 1


# ---------------------------------------------------------------- Playwright's Chromium (tests)


def chromium_check(profile: Path) -> int:
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            str(profile), headless=True, viewport={"width": VIEWPORT[0], "height": VIEWPORT[1]})
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        ok = _signed_in(page)
        print(f"{'Signed in' if ok else 'Not signed in'} to Luma (profile {profile}, "
              f"page {page.url})")
        ctx.close()
    return 0 if ok else 1


def chromium_login(profile: Path) -> int:
    print(f"Profile: {profile}")
    print("A Chromium window is opening on Luma's sign-in page. Sign in, then close the window.")
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            str(profile), headless=False, viewport={"width": VIEWPORT[0], "height": VIEWPORT[1]})
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(SIGN_IN, wait_until="domcontentloaded", timeout=60000)
        names: list[str] = []
        while True:
            live = [p for p in ctx.pages if not p.is_closed()]
            if not live:
                break
            try:
                live[0].wait_for_timeout(1000)  # keeps Playwright's events flowing
                names = luma_cookies(ctx)
            except Exception:  # window closed while waiting
                break
        try:
            ctx.close()
        except Exception:
            pass
    print(f"Window closed. Luma cookies saved: {len(names)}.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="is the agent's browser signed in?")
    parser.add_argument("--profile", default=os.getenv("BROWSER_PROFILE_DIR", "./browser_profile"),
                        help="BROWSER=chromium only")
    args = parser.parse_args()
    if os.getenv("BROWSER", "chrome").strip().lower() == "chrome":
        return chrome_check() if args.check else chrome_login()
    profile = Path(args.profile).expanduser().resolve()
    profile.mkdir(parents=True, exist_ok=True)
    return chromium_check(profile) if args.check else chromium_login(profile)


if __name__ == "__main__":
    raise SystemExit(main())
