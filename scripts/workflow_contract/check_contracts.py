"""Offline method/path inventory comparison; does not execute application code."""

import argparse
import json
from pathlib import Path
import re
import sys

if __package__:
    from .android_routes import extract_routes as android_routes
    from .backend_routes import extract_routes as backend_routes
else:
    from android_routes import extract_routes as android_routes
    from backend_routes import extract_routes as backend_routes


LIMITATIONS = (
    "Static method/path inventory only; matches do not establish full API compatibility.",
    "Backend scans main*.py/routes*.py literal @app HTTP decorators; dynamic paths, "
    "routers, aliases and runtime registration/conditions are not resolved. Routes "
    "from alternative backend entry points are combined, not proven to coexist.",
    "Android scans literal Retrofit verb annotations in ApiService.kt only; "
    "constants, interpolation, @HTTP and @Url are unsupported.",
    "Matching ignores placeholder names only; base URLs, deployment prefixes, "
    "parameter types, query/body schemas, authentication and responses are not validated.",
)


def route_key(row: dict) -> tuple[str, str]:
    """Preserve literals and slash structure, replacing named placeholders."""
    return row["method"], re.sub(r"\{[^{}\/]+\}", "{}", row["path"])


def audit(root: Path) -> dict:
    root = Path(root)
    if not root.is_dir():
        raise NotADirectoryError(f"Repository directory not found: {root}")
    backend = backend_routes(root)
    android = android_routes(root)
    keys = {route_key(row) for row in backend}
    matched, missing = [], []
    for row in android:
        (matched if route_key(row) in keys else missing).append(row)
    return {
        "backend_count": len(backend),
        "android_count": len(android),
        "matched": matched,
        "missing": missing,
        "limitations": list(LIMITATIONS),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path, help="Repository root")
    parser.add_argument("--strict", action="store_true", help="Exit 1 on missing routes")
    args = parser.parse_args(argv)
    try:
        result = audit(args.repo)
    except Exception as exc:
        print(f"Contract audit failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if args.strict and result["missing"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
