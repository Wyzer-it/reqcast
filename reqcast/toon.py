"""Minimal TOON (Token-Oriented Object Notation) encode/decode for the one
shape `--polish` needs: a flat array of same-shaped string records (id,
body), no nesting.

Hand-written rather than depending on a package: the official Python
package (`toon-format` on PyPI) is a namespace reservation with no real
implementation as of 2026-09. The quoting/escaping rules here match the
real TOON v2.1 spec exactly (checked against the reference
`@toon-format/toon` implementation already vendored elsewhere in this
monorepo, at `gap2close-UI/agents-server/utils/toon-serializer.ts`), just
narrowed to flat string fields - this is intentionally not a general TOON
parser.

Why TOON here specifically, and not for reqcast's actual output: see
https://wyzer.it/blog/Data-Format-Selection-for-Multi-Agent-LLM-Systems-An-Empirical-Analysis-of-Token-Efficiency
A batch of `{id, body}` records is exactly TOON's sweet spot - a flat,
uniform array, the same shape as the article's own `hikes[N]{...}` example.
ReqIF itself stays XML: it's a deep object graph (specification ->
hierarchy -> spec-object -> attribute values), and the same article found
TOON "fails completely on deeply nested structures."

    requirements[2]{id,body}:
      REQ-001,The system shall do the first thing.
      REQ-002,"A value with a comma, needs quotes."
"""
from __future__ import annotations

import re

DELIMITER = ","
_NUMERIC_RE = re.compile(r"^-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?$")
_LEADING_ZERO_RE = re.compile(r"^0\d+$")
_HEADER_RE = re.compile(r"^(.+?)\[(\d+)\]\{(.+)\}:$")


def _looks_numeric(value: str) -> bool:
    return bool(_NUMERIC_RE.match(value)) or bool(_LEADING_ZERO_RE.match(value))


def _is_safe_unquoted(value: str) -> bool:
    if not value or value != value.strip():
        return False
    if value.lower() in ("true", "false", "null"):
        return False
    if _looks_numeric(value):
        return False
    if ":" in value or '"' in value or "\\" in value:
        return False
    if any(c in value for c in "[]{}"):
        return False
    if any(c in value for c in "\n\r\t"):
        return False
    if DELIMITER in value:
        return False
    if value.startswith("-"):
        return False
    return True


def _escape(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )


def _unescape(value: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(value):
        c = value[i]
        if c == "\\":
            if i + 1 >= len(value):
                raise ValueError("Invalid escape sequence: backslash at end of string")
            nxt = value[i + 1]
            if nxt == "n":
                out.append("\n")
            elif nxt == "t":
                out.append("\t")
            elif nxt == "r":
                out.append("\r")
            elif nxt == "\\":
                out.append("\\")
            elif nxt == '"':
                out.append('"')
            else:
                raise ValueError(f"Invalid escape sequence: \\{nxt}")
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _encode_value(value: str) -> str:
    if _is_safe_unquoted(value):
        return value
    return f'"{_escape(value)}"'


def estimate_tokens(text: str) -> int:
    """Rough, dependency-free token estimate (~4 chars/token for English
    prose) - good enough for sizing `--polish` batches, not for billing."""
    return max(1, len(text) // 4)


def encode_records(key: str, fields: list[str], rows: list[dict[str, str]]) -> str:
    """Encodes a flat array of same-shaped string-only records as TOON."""
    header = f"{key}[{len(rows)}]{{{','.join(fields)}}}:"
    lines = [header]
    for row in rows:
        lines.append("  " + ",".join(_encode_value(row[f]) for f in fields))
    return "\n".join(lines)


def _split_row(line: str) -> list[str]:
    """Splits one row on unquoted commas. A quoted field's own commas (and
    escaped quotes/backslashes within it) don't count as separators - only
    a delimiter reached outside of a quoted span ends a field."""
    values: list[str] = []
    i, n = 0, len(line)
    while True:
        if i < n and line[i] == '"':
            j = i + 1
            buf: list[str] = []
            while j < n:
                c = line[j]
                if c == "\\" and j + 1 < n:
                    buf.append(c)
                    buf.append(line[j + 1])
                    j += 2
                    continue
                if c == '"':
                    j += 1
                    break
                buf.append(c)
                j += 1
            values.append(_unescape("".join(buf)))
            i = j
            if i < n and line[i] == DELIMITER:
                i += 1
                continue
            break
        j = line.find(DELIMITER, i)
        if j == -1:
            values.append(line[i:])
            break
        values.append(line[i:j])
        i = j + 1
    return values


def decode_records(text: str) -> tuple[str, list[str], list[dict[str, str]]]:
    """Decodes a single flat `key[N]{f1,f2,...}:` block into
    (key, fields, rows). Raises ValueError on anything else - by design
    this only understands the one shape reqcast ever sends or expects
    back, not general TOON (nested objects, non-uniform arrays, etc.)."""
    lines = [ln for ln in text.splitlines() if ln.strip() != ""]
    if not lines:
        raise ValueError("Empty TOON response")
    header = lines[0].strip()
    m = _HEADER_RE.match(header)
    if not m:
        raise ValueError(f"Not a recognized TOON array-of-objects header: {header!r}")
    key = m.group(1)
    count = int(m.group(2))
    fields = [f.strip() for f in m.group(3).split(DELIMITER)]

    rows: list[dict[str, str]] = []
    for line in lines[1 : 1 + count]:
        values = _split_row(line.strip())
        if len(values) != len(fields):
            raise ValueError(f"Row has {len(values)} values, expected {len(fields)}: {line!r}")
        rows.append(dict(zip(fields, values)))
    if len(rows) != count:
        raise ValueError(f"Header declares {count} rows, found {len(rows)}")
    return key, fields, rows
