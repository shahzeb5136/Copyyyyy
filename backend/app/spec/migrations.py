"""Explicit handling of spec versions. Unknown versions are rejected, never guessed."""

from __future__ import annotations

from collections.abc import Callable

from ..errors import SpecError
from .models import SPEC_VERSION, DashboardSpec

# "0.9" -> function(dict) -> dict. Add an entry here whenever SPEC_VERSION changes.
_MIGRATIONS: dict[str, Callable[[dict], dict]] = {}


def load_spec(data: dict) -> DashboardSpec:
    if not isinstance(data, dict):
        raise SpecError("The dashboard file must contain a JSON object.")
    version = str(data.get("spec_version", "")).strip()
    if not version:
        raise SpecError("The dashboard file has no 'spec_version'. Is this a dashboard file saved by this app?")
    while version != SPEC_VERSION:
        migrate = _MIGRATIONS.get(version)
        if migrate is None:
            raise SpecError(f"Unsupported dashboard spec version '{version}' (this app understands {SPEC_VERSION}).")
        data = migrate(data)
        version = str(data.get("spec_version", ""))
    try:
        return DashboardSpec.model_validate(data)
    except Exception as exc:  # pydantic ValidationError
        raise SpecError(f"The dashboard file is not valid: {_short(exc)}") from exc


def _short(exc: Exception) -> str:
    msg = str(exc)
    return msg if len(msg) < 800 else msg[:800] + " ..."
