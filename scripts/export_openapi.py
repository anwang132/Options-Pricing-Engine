"""Write the API's OpenAPI document for frontend type generation.

uv run python scripts/export_openapi.py ui/src/api/openapi.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from options_engine.interfaces.http.app import create_app


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "ui/src/api/openapi.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    spec = create_app(workers=0).openapi()
    out.write_text(json.dumps(spec, indent=1, sort_keys=True) + "\n")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
