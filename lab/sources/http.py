from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class SourceError(Exception):
    pass


def get_json(url: str, timeout: float = 20) -> tuple[object, dict]:
    """GET a JSON document. Errors never include the URL, because some URLs carry API keys."""
    try:
        req = Request(url, headers={"Accept": "application/json"})
        with urlopen(req, timeout=timeout) as resp:
            body = resp.read(20_000_001)
            if len(body) > 20_000_000:
                raise SourceError("Response too large")
            return json.loads(body), {k.lower(): v for k, v in resp.headers.items()}
    except HTTPError as e:
        raise SourceError(f"HTTP {e.code}") from None
    except (URLError, TimeoutError, OSError, ValueError) as e:
        raise SourceError(type(e).__name__) from None
