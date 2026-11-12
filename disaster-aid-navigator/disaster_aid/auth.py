"""Auth0 sign-in and roles.

Who is an admin comes only from the roles claim that the Auth0 post-login
Action copies out of ``app_metadata.roles``. Usernames and emails are never
used to decide a role.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode, urlparse

ADMIN = "admin"
STANDARD = "standard"

REQUIRED_ENV = ("AUTH0_DOMAIN", "AUTH0_CLIENT_ID", "AUTH0_CLIENT_SECRET",
                "AUTH0_ROLES_CLAIM", "SESSION_SECRET")


def role_from_claims(claims: dict, claim_name: str) -> str | None:
    """'admin', 'standard', or None when the user has neither role.

    Admin wins when both are present. Anything that is not a list of strings
    counts as no role, so a broken Action locks people out instead of letting
    them in.
    """
    roles = claims.get(claim_name)
    if not isinstance(roles, list):
        return None
    names = {r.strip().lower() for r in roles if isinstance(r, str)}
    if ADMIN in names:
        return ADMIN
    if STANDARD in names:
        return STANDARD
    return None


@dataclass(frozen=True)
class AuthSettings:
    domain: str
    client_id: str
    client_secret: str
    roles_claim: str
    session_secret: str
    base_url: str
    session_hours: float = 1.0

    @property
    def https(self) -> bool:
        return self.base_url.startswith("https://")

    @property
    def origin(self) -> str:
        u = urlparse(self.base_url)
        return f"{u.scheme}://{u.netloc}"

    def logout_url(self) -> str:
        query = urlencode({"client_id": self.client_id, "returnTo": self.base_url + "/"})
        return f"https://{self.domain}/v2/logout?{query}"

    @classmethod
    def from_env(cls, default_base_url: str) -> "AuthSettings":
        missing = [name for name in REQUIRED_ENV if not os.environ.get(name, "").strip()]
        if missing:
            raise SystemExit("Sign-in is not set up. Add these to disaster-aid-navigator/.env: "
                             + ", ".join(missing))
        secret = os.environ["SESSION_SECRET"].strip()
        if len(secret) < 32:
            raise SystemExit("SESSION_SECRET must be at least 32 characters.")
        domain = os.environ["AUTH0_DOMAIN"].strip().removeprefix("https://").rstrip("/")
        base = os.environ.get("APP_BASE_URL", "").strip().rstrip("/") or default_base_url
        try:
            hours = float(os.environ.get("SESSION_HOURS", "").strip() or 1)
        except ValueError:
            raise SystemExit("SESSION_HOURS must be a number of hours, for example 1.") from None
        if not 0 < hours <= 24:
            raise SystemExit("SESSION_HOURS must be more than 0 and at most 24.")
        return cls(domain=domain, client_id=os.environ["AUTH0_CLIENT_ID"].strip(),
                   client_secret=os.environ["AUTH0_CLIENT_SECRET"].strip(),
                   roles_claim=os.environ["AUTH0_ROLES_CLAIM"].strip(),
                   session_secret=secret, base_url=base, session_hours=hours)


class Auth0Login:
    """OpenID Connect authorization-code sign-in with Auth0 (via Authlib).

    Authlib checks the ID token's signature, issuer, audience, expiry and nonce.
    """

    def __init__(self, settings: AuthSettings):
        from authlib.integrations.starlette_client import OAuth

        oauth = OAuth()
        oauth.register(
            name="auth0",
            client_id=settings.client_id,
            client_secret=settings.client_secret,
            server_metadata_url=f"https://{settings.domain}/.well-known/openid-configuration",
            client_kwargs={"scope": "openid profile email"},
        )
        self._client = oauth.auth0

    async def start(self, request, redirect_uri: str):
        return await self._client.authorize_redirect(request, redirect_uri)

    async def finish(self, request) -> dict:
        token = await self._client.authorize_access_token(request)
        return dict(token.get("userinfo") or {})


def load_env_file(path: Path) -> None:
    """Read KEY=value lines into os.environ. Real environment variables win."""
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        else:  # like the shell: in an unquoted value, " #" starts a comment
            value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
        if key and value and key not in os.environ:
            os.environ[key] = value
