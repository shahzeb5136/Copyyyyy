"""Identity from Azure App Service / Container Apps built-in authentication ("Easy Auth").

The platform authenticates the user with Entra ID *before* the request reaches
the container and injects headers describing the signed-in principal. The app
never handles tokens or passwords itself.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field

from fastapi import HTTPException, Request

from .settings import get_settings


@dataclass
class Principal:
    name: str = ""
    id: str = ""
    provider: str = ""
    authenticated: bool = False
    claims: dict[str, str] = field(default_factory=dict)

    @property
    def display_name(self) -> str:
        return self.claims.get("name") or self.name or "anonymous"

    def as_dict(self) -> dict:
        return {"name": self.display_name, "email": self.name, "id": self.id, "provider": self.provider,
                "authenticated": self.authenticated}


def principal_from_headers(headers) -> Principal:
    name = headers.get("x-ms-client-principal-name", "") or ""
    pid = headers.get("x-ms-client-principal-id", "") or ""
    idp = headers.get("x-ms-client-principal-idp", "") or ""
    claims: dict[str, str] = {}
    raw = headers.get("x-ms-client-principal")
    if raw:
        try:
            doc = json.loads(base64.b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8"))
            for c in doc.get("claims", []):
                typ, val = str(c.get("typ", "")), str(c.get("val", ""))
                if typ and typ not in claims:
                    claims[typ.rsplit("/", 1)[-1]] = val
            idp = idp or str(doc.get("auth_typ", ""))
        except Exception:  # noqa: BLE001 - a malformed header must not break the request
            pass
    return Principal(name=name, id=pid, provider=idp, authenticated=bool(name or pid), claims=claims)


def current_user(request: Request) -> Principal:
    principal = principal_from_headers(request.headers)
    if get_settings().require_easy_auth and not principal.authenticated:
        raise HTTPException(status_code=401, detail="Sign-in required.")
    return principal
