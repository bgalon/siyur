"""Session identity + the row-scoping privacy boundary (api/AGENTS.md, PRD §13 #4).

The signed session cookie carries a minimal :class:`SessionUser`. The user's Google
``sub`` is the **stable row-scope key**: every future ``user_*`` query MUST be
filtered to :attr:`SessionUser.sub` of the authenticated subject. The commons
(``site*``) is world-readable to any signed-in user; a missing user-scope is a
data-leak bug, not a style nit. No ``user_*`` tables exist yet — this module
establishes the seam so they can only be reached through :func:`require_user`.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, status
from pydantic import BaseModel

# Key under which the identity is stored in ``request.session``.
SESSION_USER_KEY = "user"


class SessionUser(BaseModel):
    """The signed-in subject, as carried in the session cookie.

    ``sub`` (Google's stable subject id) is the row-scope key for all per-user data.
    """

    sub: str
    email: str | None = None
    name: str | None = None
    picture: str | None = None

    @classmethod
    def from_userinfo(cls, userinfo: dict[str, Any]) -> SessionUser:
        """Build from the OIDC ``userinfo`` claims returned in the token."""
        return cls(
            sub=str(userinfo["sub"]),
            email=userinfo.get("email"),
            name=userinfo.get("name"),
            picture=userinfo.get("picture"),
        )


def current_user_optional(request: Request) -> SessionUser | None:
    """The signed-in user, or ``None`` when the request carries no valid session."""
    data = request.session.get(SESSION_USER_KEY)
    if not data:
        return None
    return SessionUser.model_validate(data)


def require_user(request: Request) -> SessionUser:
    """FastAPI dependency: the authenticated user, or ``401``.

    Every route that reads or writes ``user_*`` data MUST depend on this — its
    return value's ``.sub`` is the mandatory row-scope filter (api/AGENTS.md).
    """
    user = current_user_optional(request)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def login_user(request: Request, user: SessionUser) -> None:
    """Persist the identity into the signed session cookie."""
    request.session[SESSION_USER_KEY] = user.model_dump()


def logout_user(request: Request) -> None:
    """Drop the identity from the session."""
    request.session.pop(SESSION_USER_KEY, None)


# Ready-made dependency alias for route signatures.
CurrentUser = Annotated[SessionUser, Depends(require_user)]
