# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
CSP violation reports: what a log line may carry.

A report is attacker-controlled input (anyone can POST one), so nothing is logged as received:

- Only the fields in ``FIELDS`` survive (the others, such as ``original-policy`` or
  ``script-sample``, are dropped).
- A value is a string or an integer; anything else (an object, a list, a float, a boolean, null)
  is dropped. An integer must be in ``0 .. MAX_INT``.
- A string loses every control character (and the Unicode line and paragraph separators), so it
  cannot start a forged log line; the URL fields lose their query string and fragment (they can
  carry tokens); then it is cut to ``MAX_FIELD_LENGTH`` characters.
- At most ``MAX_REPORTS_PER_REQUEST`` violations are taken from one request.

Both browser formats are read: ``application/csp-report`` (``report-uri``: one object under
``"csp-report"``, hyphenated keys) and ``application/reports+json`` (``report-to``: an array of
reports, only ``"type": "csp-violation"`` entries, camelCase keys in ``"body"``).
"""

from __future__ import annotations

import unicodedata
from typing import Any, Final

MAX_REPORTS_PER_REQUEST: Final[int] = 10
MAX_FIELD_LENGTH: Final[int] = 512
MAX_INT: Final[int] = 2**31 - 1

_URL_FIELDS: Final[frozenset[str]] = frozenset({"document-uri", "blocked-uri", "source-file"})
_INT_FIELDS: Final[frozenset[str]] = frozenset({"status-code", "line-number", "column-number"})
#: Accepted key (either browser format) -> the field that is logged. Every other key is dropped.
_KEYS: Final[dict[str, str]] = {
    "document-uri": "document-uri",
    "documentURL": "document-uri",
    "blocked-uri": "blocked-uri",
    "blockedURL": "blocked-uri",
    "violated-directive": "violated-directive",
    "violatedDirective": "violated-directive",
    "effective-directive": "effective-directive",
    "effectiveDirective": "effective-directive",
    "disposition": "disposition",
    "status-code": "status-code",
    "statusCode": "status-code",
    "source-file": "source-file",
    "sourceFile": "source-file",
    "line-number": "line-number",
    "lineNumber": "line-number",
    "column-number": "column-number",
    "columnNumber": "column-number",
}
FIELDS: Final[tuple[str, ...]] = tuple(dict.fromkeys(_KEYS.values()))


class MalformedReportError(ValueError):
    """The body is JSON but not the shape its content type promises."""


def _clean(value: str) -> str:
    return "".join(
        ch
        for ch in value
        if not unicodedata.category(ch).startswith("C")
        and unicodedata.category(ch) not in {"Zl", "Zp"}
    )


def _sanitise(field: str, value: object) -> str | int | None:
    if field in _INT_FIELDS:
        ok = isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= MAX_INT
        return value if ok else None  # type: ignore[return-value]
    if not isinstance(value, str):
        return None
    # Bound the work before the character loop; the body is capped, this is belt and braces.
    cleaned = _clean(value[: MAX_FIELD_LENGTH * 8])
    if field in _URL_FIELDS:
        cleaned = cleaned.split("#", 1)[0].split("?", 1)[0]
    return cleaned[:MAX_FIELD_LENGTH]


def _violation(raw: object) -> dict[str, str | int]:
    if not isinstance(raw, dict):
        return {}
    fields: dict[str, str | int] = {}
    for key, value in raw.items():
        field = _KEYS.get(key) if isinstance(key, str) else None
        if field is not None and (clean := _sanitise(field, value)) is not None:
            fields[field] = clean
    return {name: fields[name] for name in FIELDS if name in fields}


def violations(payload: Any, media_type: str) -> list[dict[str, str | int]]:  # noqa: ANN401
    """The sanitised, non-empty violations in a parsed report body (at most the per-request cap).

    Raises ``MalformedReportError`` when the body is not the shape its content type promises.
    """
    if media_type == "application/csp-report":
        if not isinstance(payload, dict) or not isinstance(payload.get("csp-report"), dict):
            msg = "expected an object with a csp-report object"
            raise MalformedReportError(msg)
        raws: list[object] = [payload["csp-report"]]
    else:
        if not isinstance(payload, list):
            msg = "expected an array of reports"
            raise MalformedReportError(msg)
        raws = [
            r.get("body")
            for r in payload
            if isinstance(r, dict) and r.get("type") == "csp-violation"
        ]
    taken = (_violation(raw) for raw in raws[:MAX_REPORTS_PER_REQUEST])
    return [v for v in taken if v]
