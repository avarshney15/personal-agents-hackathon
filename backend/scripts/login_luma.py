"""Sign in to Luma once in hosted Chrome, so the RSVP agent acts as you.

Opens a visible Chromium on the agent's profile (BROWSER_PROFILE_DIR, default ./browser_profile)
at https://lu.ma/signin. Sign in, then close the window. Run it from backend/ so the profile path
matches the server's:

    cd backend && .venv/bin/python scripts/login_luma.py
    .venv/bin/python scripts/login_luma.py --check   # headless: is the profile signed in?

Close the server's runs first: Chromium allows one browser per profile at a time.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

from app.act import page_checks as pc  # noqa: E402
from app.act.browser_agent import VIEWPORT  # noqa: E402

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


def luma_cookies(ctx) -> list[str]:
    return [c["name"] for c in ctx.cookies()
            if any(d in c.get("domain", "") for d in ("lu.ma", "luma.com"))]


def check(profile: Path) -> int:
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            str(profile), headless=True, viewport={"width": VIEWPORT[0], "height": VIEWPORT[1]})
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://lu.ma/home", wait_until="domcontentloaded", timeout=30000)
        try:
            page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass
        ok = pc.signed_in(page.evaluate(pc.SCAN_JS))
        print(f"{'Signed in' if ok else 'Not signed in'} to Luma (profile {profile}, "
              f"page {page.url})")
        ctx.close()
    return 0 if ok else 1


def login(profile: Path) -> int:
    print(f"Profile: {profile}")
    print("A Chromium window is opening on Luma's sign-in page. Sign in, then close the window.")
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            str(profile), headless=False, viewport={"width": VIEWPORT[0], "height": VIEWPORT[1]})
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://lu.ma/signin", wait_until="domcontentloaded", timeout=60000)
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
    print(f"Window closed. Luma cookies saved: {len(names)}. "
          "Check with: .venv/bin/python scripts/login_luma.py --check")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="headless check, no window")
    parser.add_argument("--profile", default=os.getenv("BROWSER_PROFILE_DIR", "./browser_profile"))
    args = parser.parse_args()
    profile = Path(args.profile).expanduser().resolve()
    profile.mkdir(parents=True, exist_ok=True)
    return check(profile) if args.check else login(profile)


if __name__ == "__main__":
    raise SystemExit(main())
