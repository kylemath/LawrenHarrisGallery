"""Shared polite HTTP helpers for index fetchers."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests

UA = "LaurenHarrisIndexBot/1.0 (research; metadata-only; contact: local)"
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": UA, "Accept": "*/*"})

_last_req: dict[str, float] = {}
_robots: dict[str, Optional[RobotFileParser]] = {}
MIN_INTERVAL = 1.05  # >=1 req/sec per host


def _host(url: str) -> str:
    return urlparse(url).netloc.lower()


def allowed(url: str) -> bool:
    host = _host(url)
    path = urlparse(url).path or "/"
    # Wikimedia explicitly Allows EntityData dumps; stdlib robotparser mishandles
    # the "Allow: /wiki/Special:EntityData/*." rule for .json URLs.
    if host in {"www.wikidata.org", "wikidata.org"} and path.startswith(
        "/wiki/Special:EntityData/"
    ):
        return True
    if host not in _robots:
        rp = RobotFileParser()
        robots_url = f"{urlparse(url).scheme}://{host}/robots.txt"
        try:
            _polite_wait(host)
            r = SESSION.get(robots_url, timeout=20)
            if r.status_code == 200:
                rp.parse(r.text.splitlines())
                _robots[host] = rp
            else:
                _robots[host] = None
        except Exception:
            _robots[host] = None
    rp = _robots[host]
    if rp is None:
        return True
    try:
        return rp.can_fetch(UA, url)
    except Exception:
        return True


def _polite_wait(host: str) -> None:
    now = time.time()
    last = _last_req.get(host, 0.0)
    delay = MIN_INTERVAL - (now - last)
    if delay > 0:
        time.sleep(delay)
    _last_req[host] = time.time()


def get(
    url: str,
    *,
    params: Optional[dict] = None,
    timeout: int = 60,
    as_json: bool = False,
    allow_redirects: bool = True,
) -> Any:
    if not allowed(url):
        raise PermissionError(f"robots.txt disallows: {url}")
    _polite_wait(_host(url))
    r = SESSION.get(url, params=params, timeout=timeout, allow_redirects=allow_redirects)
    r.raise_for_status()
    if as_json:
        return r.json()
    return r.text


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def save_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
