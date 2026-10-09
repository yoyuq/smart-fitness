"""Static Retrofit method/path inventory; never loads application code.

Only literal verb annotations in ApiService.kt are inventoried. Constants,
interpolated strings, @HTTP, @Url and dynamic registration are unsupported;
this is not a claim of runtime coverage or full API compatibility.
"""

from pathlib import Path
import re


_SOURCE = Path("android_app/app/src/main/java/com/smartfitness/app/api/ApiService.kt")
_VERBS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
_TOKEN = re.compile(r'\w+|[^\s]', re.UNICODE)
_ESCAPES = {"t": "\t", "b": "\b", "n": "\n", "r": "\r",
            "'": "'", '"': '"', "\\": "\\", "$": "$"}


def _literal(raw: str) -> str | None:
    """Decode Kotlin string escapes, rejecting interpolation and invalid escapes."""
    if raw.startswith('"""'):
        value = raw[3:-3]
        return None if "$" in value else value
    value = []
    i = 1
    while i < len(raw) - 1:
        char = raw[i]
        if char == "$":
            return None
        if char == "\\":
            i += 1
            char = raw[i]
            if char == "u":
                digits = raw[i + 1:i + 5]
                if not re.fullmatch(r"[0-9a-fA-F]{4}", digits):
                    return None
                value.append(chr(int(digits, 16)))
                i += 5
                continue
            if char not in _ESCAPES:
                return None
            char = _ESCAPES[char]
        value.append(char)
        i += 1
    return "".join(value)


def _tokens(text: str) -> list[tuple[str, str, int]]:
    """Skip Kotlin comments (including nested blocks), retaining source lines."""
    result = []
    i, line = 0, 1
    while i < len(text):
        start, start_line = i, line
        if text.startswith("//", i):
            end = text.find("\n", i)
            i = len(text) if end == -1 else end
        elif text.startswith("/*", i):
            i += 2
            depth = 1
            while i < len(text) and depth:
                if text.startswith("/*", i):
                    depth += 1
                    i += 2
                elif text.startswith("*/", i):
                    depth -= 1
                    i += 2
                else:
                    i += 1
        elif text[i] in "\"'":
            delimiter = '"""' if text.startswith('"""', i) else text[i]
            i += len(delimiter)
            while i < len(text):
                if text.startswith(delimiter, i):
                    i += len(delimiter)
                    result.append(("string" if delimiter != "'" else "char",
                                   text[start:i], start_line))
                    break
                if text[i] == "\\" and delimiter != '"""':
                    i += 2
                else:
                    i += 1
        elif text[i].isspace():
            i += 1
        else:
            match = _TOKEN.match(text, i)
            assert match is not None
            i = match.end()
            result.append(("code", match.group(), start_line))
        line += text.count("\n", start, i)
    return result


def extract_routes(root: Path) -> list[dict]:
    """Return deterministic route-record rows; missing/unreadable input raises."""
    tokens = _tokens((Path(root) / _SOURCE).read_text(encoding="utf-8-sig"))
    rows = []
    for index, token in enumerate(tokens):
        if token[:2] != ("code", "@"):
            continue
        cursor = index + 1
        name = []
        while cursor < len(tokens) and tokens[cursor][0] == "code":
            part = tokens[cursor][1]
            if part != "." and not part.isidentifier():
                break
            name.append(part)
            cursor += 1
        annotation = "".join(name)
        method = annotation.removeprefix("retrofit2.http.")
        if method not in _VERBS or cursor >= len(tokens) or tokens[cursor][1] != "(":
            continue
        cursor += 1
        if [t[1] for t in tokens[cursor:cursor + 2]] == ["value", "="]:
            cursor += 2
        if cursor >= len(tokens) or tokens[cursor][0] != "string":
            continue
        path = _literal(tokens[cursor][1])
        cursor += 1
        if cursor < len(tokens) and tokens[cursor][1] == ",":
            cursor += 1
        if path is None or cursor >= len(tokens) or tokens[cursor][1] != ")":
            continue
        rows.append({"method": method, "path": path if path.startswith("/") else "/" + path,
                     "source": _SOURCE.as_posix(), "line": token[2]})
    return sorted(rows, key=lambda row: (row["method"], row["path"], row["source"], row["line"]))
