"""Static inventory of literal HTTP decorators in backend main*/routes* files.

Only @app.get/post/put/patch/delete/head/options string-literal paths are
supported. Dynamic expressions, aliases, routers, add_api_route registrations,
route replacement and runtime conditionals are not resolved. Consequently this
inventory does not establish runtime completeness or full API compatibility.
Application modules are parsed, never imported or executed.
"""

import ast
from pathlib import Path
import tokenize


_METHODS = frozenset({"get", "post", "put", "patch", "delete", "head", "options"})


def extract_routes(root: Path) -> list[dict]:
    """Return four-field records ordered by method, path, source, then line.

    Scan backend recursively for main*.py and routes*.py. Unsupported route
    patterns are omitted; syntax, decoding and filesystem errors propagate so
    callers cannot mistake unreadable inputs for a successful empty inventory.
    Paths lacking a leading slash receive one; parameter names stay unchanged.
    """
    root = Path(root)
    backend = root / "backend"
    if not backend.is_dir():
        raise FileNotFoundError(f"Backend directory not found: {backend}")
    sources = sorted(
        set(backend.rglob("main*.py")) | set(backend.rglob("routes*.py"))
    )
    rows = []
    for source in sources:
        with tokenize.open(source) as stream:
            tree = ast.parse(stream.read(), filename=str(source))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call):
                    continue
                func = decorator.func
                if not (
                    isinstance(func, ast.Attribute)
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "app"
                    and func.attr in _METHODS
                ):
                    continue
                path_node = decorator.args[0] if decorator.args else next(
                    (kw.value for kw in decorator.keywords if kw.arg == "path"),
                    None,
                )
                if not (
                    isinstance(path_node, ast.Constant)
                    and isinstance(path_node.value, str)
                ):
                    continue
                path = path_node.value
                rows.append({
                    "method": func.attr.upper(),
                    "path": path if path.startswith("/") else "/" + path,
                    "source": source.relative_to(root).as_posix(),
                    "line": decorator.lineno,
                })
    return sorted(rows, key=lambda row: (
        row["method"], row["path"], row["source"], row["line"]
    ))
