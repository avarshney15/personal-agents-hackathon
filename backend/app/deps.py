"""Shared FastAPI dependencies. Owned by Part 5.

    from app.deps import current_user
    @router.get("/api/week")
    def week(user: User = Depends(current_user)): ...

Current user, in order: cookie `ea_user`, header `X-User-Id`, else the demo user.
"""

from __future__ import annotations

from typing import Optional

from fastapi import Request

from app.contracts import DEMO_USER_ID, User
from app.demo import demo_user
from app.store import get_store

USER_COOKIE = "ea_user"


def resolve_user(cookie_id: Optional[str] = None, header_id: Optional[str] = None) -> User:
    """Unknown ids are created on first sight (X-User-Id ones as "Eval user <id>")."""
    store = get_store()
    if cookie_id:
        user = store.get_user(cookie_id)
        if user:
            return user
        is_email = "@" in cookie_id
        return store.ensure_user(User(id=cookie_id, name=cookie_id.split("@")[0] if is_email else
                                      f"User {cookie_id}", email=cookie_id if is_email else None))
    if header_id and header_id != DEMO_USER_ID:
        return store.ensure_user(User(id=header_id, name=f"Eval user {header_id}"))
    return store.ensure_user(demo_user())


def current_user(request: Request) -> User:
    return resolve_user(request.cookies.get(USER_COOKIE), request.headers.get("x-user-id"))
