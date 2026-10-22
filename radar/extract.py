"""Pull structured data out of listing pages: embedded state JSON, Next.js payloads, schema.org."""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Iterator

_decoder = json.JSONDecoder(strict=False)
_NEXT_FLIGHT = re.compile(r'self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)')
_JSON_LD = re.compile(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.S | re.I)


def next_flight(html: str) -> str:
    """The React Server Components payload a Next.js (app router) page streams inline."""
    parts = []
    for chunk in _NEXT_FLIGHT.findall(html):
        try:
            parts.append(json.loads(chunk))
        except ValueError:
            pass
    return "".join(parts)


def json_values_after(text: str, pattern: str) -> Iterator[Any]:
    """Decode each JSON value that directly follows a regex match, e.g. `window.STATE = {...}`."""
    for m in re.finditer(pattern, text):
        start = m.end()
        while start < len(text) and text[start].isspace():
            start += 1
        try:
            value, _ = _decoder.raw_decode(text, start)
        except ValueError:
            continue
        yield value


def json_ld(html: str) -> list[dict]:
    """Every schema.org object on the page, with lists and @graph containers flattened."""
    items: list[dict] = []
    for block in _JSON_LD.findall(html):
        try:
            data = _decoder.decode(block.strip())
        except ValueError:
            continue  # sites sometimes ship broken JSON-LD; the rest of the page is still usable
        stack = [data]
        while stack:
            x = stack.pop()
            if isinstance(x, list):
                stack.extend(x)
            elif isinstance(x, dict):
                graph = x.get("@graph")
                if graph is not None:
                    stack.extend(graph if isinstance(graph, list) else [graph])
                items.append(x)
    return items


def find_dicts(obj: Any, predicate: Callable[[dict], bool]) -> Iterator[dict]:
    stack = [obj]
    while stack:
        x = stack.pop()
        if isinstance(x, dict):
            if predicate(x):
                yield x
            stack.extend(x.values())
        elif isinstance(x, list):
            stack.extend(x)
