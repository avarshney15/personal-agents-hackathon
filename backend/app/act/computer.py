"""Runs Claude's computer toolset actions (computer_toolset_20260801) on a Playwright page.

Each tool_use block from the toolset names its member (screenshot, left_click, type, ...) and
carries only that member's input. Coordinates are pixels of the screenshots we send, which are
the page viewport at device scale factor 1, so they map 1:1 to Playwright mouse coordinates.
"""

from __future__ import annotations

import base64
import re
import time
from typing import Any, Callable, Optional

from playwright.sync_api import Page

# xdotool-style names Claude uses -> Playwright key names.
_KEYS = {
    "return": "Enter", "enter": "Enter", "kp_enter": "Enter",
    "esc": "Escape", "escape": "Escape",
    "backspace": "Backspace", "back_space": "Backspace",
    "tab": "Tab", "space": "Space",
    "delete": "Delete", "del": "Delete", "insert": "Insert",
    "up": "ArrowUp", "down": "ArrowDown", "left": "ArrowLeft", "right": "ArrowRight",
    "arrowup": "ArrowUp", "arrowdown": "ArrowDown", "arrowleft": "ArrowLeft", "arrowright": "ArrowRight",
    "page_up": "PageUp", "pageup": "PageUp", "prior": "PageUp",
    "page_down": "PageDown", "pagedown": "PageDown", "next": "PageDown",
    "home": "Home", "end": "End",
    "ctrl": "Control", "control": "Control", "control_l": "Control", "control_r": "Control",
    "alt": "Alt", "option": "Alt", "alt_l": "Alt", "alt_r": "Alt",
    "shift": "Shift", "shift_l": "Shift", "shift_r": "Shift",
    "super": "Meta", "super_l": "Meta", "super_r": "Meta", "meta": "Meta", "cmd": "Meta",
    "command": "Meta", "win": "Meta",
}

# A run of 13-19 digits (spaces or dashes allowed) looks like a card number. Never typed.
_CARD = re.compile(r"(?:\d[ -]?){13,19}")


class StopRun(Exception):
    """Raised by an action that must end the run and hand back to the person."""


def to_playwright_key(name: str) -> str:
    """'ctrl+shift+Tab' -> 'Control+Shift+Tab'; 'Return' -> 'Enter'."""
    parts = []
    for raw in name.split("+"):
        part = raw.strip()
        if not part:
            continue
        low = part.lower()
        if low in _KEYS:
            parts.append(_KEYS[low])
        elif re.fullmatch(r"f\d{1,2}", low):
            parts.append(low.upper())
        else:
            parts.append(part)
    return "+".join(parts)


def _modifiers(text: Optional[str]) -> list[str]:
    return [to_playwright_key(p) for p in (text or "").split("+") if p.strip()]


class ComputerExecutor:
    """Executes toolset members on whichever page is current; returns tool_result content."""

    def __init__(self, get_page: Callable[[], Page], width: int, height: int,
                 on_frame: Callable[[bytes], None]) -> None:
        self.get_page = get_page
        self.width = width
        self.height = height
        self.on_frame = on_frame
        self.cursor = (width // 2, height // 2)
        self.typed: list[str] = []  # every string typed, for the told-host check

    # ------------------------------------------------------------ screenshots

    def screenshot(self) -> bytes:
        # scale="css": one image pixel per page pixel, so Claude's coordinates match the mouse
        # even on a Retina screen, where the real Chrome app renders at 2x.
        png = self.get_page().screenshot(type="png", timeout=15000, scale="css")
        self.on_frame(png)
        return png

    @staticmethod
    def image_block(png: bytes) -> dict:
        return {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                            "data": base64.b64encode(png).decode()}}

    # ------------------------------------------------------------ helpers

    def _xy(self, coordinate: Any) -> tuple[int, int]:
        if coordinate is None:
            return self.cursor
        x, y = int(round(coordinate[0])), int(round(coordinate[1]))
        x = max(0, min(self.width - 1, x))
        y = max(0, min(self.height - 1, y))
        return x, y

    def _settle(self) -> None:
        page = self.get_page()
        try:
            page.wait_for_load_state("domcontentloaded", timeout=3000)
        except Exception:
            pass
        page.wait_for_timeout(400)

    def _with_mods(self, text: Optional[str], fn: Callable[[], None]) -> None:
        keyboard = self.get_page().keyboard
        mods = _modifiers(text)
        for m in mods:
            keyboard.down(m)
        try:
            fn()
        finally:
            for m in reversed(mods):
                keyboard.up(m)

    # ------------------------------------------------------------ members

    def run(self, name: str, inp: dict) -> list[dict] | str:
        """Runs one member. Returns content for its tool_result (image blocks or 'OK' text)."""
        inp = inp or {}
        page = self.get_page()
        mouse, keyboard = page.mouse, page.keyboard

        if name == "screenshot":
            return [self.image_block(self.screenshot())]

        if name == "zoom":
            x0, y0, x1, y1 = (int(v) for v in inp["region"])
            x0, x1 = sorted((max(0, x0), min(self.width, x1)))
            y0, y1 = sorted((max(0, y0), min(self.height, y1)))
            png = page.screenshot(type="png", clip={"x": x0, "y": y0, "width": max(1, x1 - x0),
                                                    "height": max(1, y1 - y0)})
            return [self.image_block(png)]

        if name in ("left_click", "right_click", "middle_click", "double_click", "triple_click"):
            x, y = self._xy(inp.get("coordinate"))
            button = {"right_click": "right", "middle_click": "middle"}.get(name, "left")
            count = {"double_click": 2, "triple_click": 3}.get(name, 1)
            self._with_mods(inp.get("text"),
                            lambda: mouse.click(x, y, button=button, click_count=count))
            self.cursor = (x, y)
            self._settle()
            return "OK"

        if name == "left_click_drag":
            sx, sy = self._xy(inp.get("start_coordinate"))
            ex, ey = self._xy(inp.get("coordinate"))

            def drag() -> None:
                mouse.move(sx, sy)
                mouse.down()
                mouse.move(ex, ey, steps=12)
                mouse.up()

            self._with_mods(inp.get("text"), drag)
            self.cursor = (ex, ey)
            self._settle()
            return "OK"

        if name == "mouse_move":
            x, y = self._xy(inp.get("coordinate"))
            mouse.move(x, y)
            self.cursor = (x, y)
            return "OK"

        if name == "left_mouse_down":
            mouse.down()
            return "OK"

        if name == "left_mouse_up":
            mouse.up()
            self._settle()
            return "OK"

        if name == "cursor_position":
            return f"X={self.cursor[0]}, Y={self.cursor[1]}"

        if name == "scroll":
            x, y = self._xy(inp.get("coordinate"))
            amount = int(inp.get("scroll_amount") or 3) * 100
            dx, dy = {"up": (0, -amount), "down": (0, amount), "left": (-amount, 0),
                      "right": (amount, 0)}[inp.get("scroll_direction", "down")]

            def wheel() -> None:
                mouse.move(x, y)
                mouse.wheel(dx, dy)

            self._with_mods(inp.get("text"), wheel)
            self.cursor = (x, y)
            page.wait_for_timeout(300)
            return "OK"

        if name == "type":
            text = str(inp.get("text", ""))
            if _CARD.search(text):
                raise StopRun("The page asked for card details. The agent never pays, so finish "
                              "this one yourself on Luma.")
            keyboard.type(text, delay=8)
            self.typed.append(text)
            return "OK"

        if name == "key":
            key = to_playwright_key(str(inp.get("text", "")))
            for _ in range(max(1, min(100, int(inp.get("repeat") or 1)))):
                keyboard.press(key)
            self._settle()
            return "OK"

        if name == "hold_key":
            keys = _modifiers(inp.get("text"))
            for k in keys:
                keyboard.down(k)
            time.sleep(min(float(inp.get("duration") or 1), 10))
            for k in reversed(keys):
                keyboard.up(k)
            return "OK"

        if name == "wait":
            time.sleep(min(float(inp.get("duration") or 1), 10))
            return "OK"

        raise ValueError(f"Unknown computer action: {name}")
