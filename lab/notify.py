"""Optional phone alerts through ntfy (free app). Off unless NTFY_TOPIC is set."""

from __future__ import annotations

import logging
from urllib.request import Request, urlopen

log = logging.getLogger("lab.notify")


def push(settings, title: str, body: str, priority: str = "default") -> bool:
    if not settings.ntfy_topic:
        return False
    headers = {"Title": title.encode("ascii", "ignore").decode(), "Priority": priority, "Tags": "moneybag"}
    if settings.public_url:
        headers["Click"] = settings.public_url
    try:
        req = Request(f"{settings.ntfy_server.rstrip('/')}/{settings.ntfy_topic}", data=body.encode("utf-8"), headers=headers, method="POST")
        with urlopen(req, timeout=10):
            return True
    except OSError as e:
        log.warning("ntfy push failed: %s", type(e).__name__)
        return False
