"""Export the OpenAPI document (includes the dashboard spec schema) for front-end type generation.

Usage: python scripts/export_openapi.py ../frontend/src/api/openapi.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pydantic.json_schema import models_json_schema  # noqa: E402

from app.api.schemas import InferResult  # noqa: E402
from app.inference.ask import AskResult  # noqa: E402
from app.main import create_app  # noqa: E402
from app.settings import Settings  # noqa: E402

# Job results are typed ``Any`` in the API; publish their schemas so the front end can generate types for them.
EXTRA_MODELS = [InferResult, AskResult]


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "openapi.json")
    app = create_app(Settings(llm_provider="mock", retention_hours=0, _env_file=None))
    doc = app.openapi()
    _, extra = models_json_schema([(m, "serialization") for m in EXTRA_MODELS],
                                  ref_template="#/components/schemas/{model}")
    schemas = doc.setdefault("components", {}).setdefault("schemas", {})
    for name, schema in extra.get("$defs", {}).items():
        schemas.setdefault(name, schema)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {out} ({len(doc.get('components', {}).get('schemas', {}))} schemas)")


if __name__ == "__main__":
    main()
