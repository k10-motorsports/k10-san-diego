"""Fetch road geometry from Overpass (drivable networks, named roads) with mirror fallback."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
# Tried in order per attempt — overpass-api.de rate-limits bursty use (429); kumi tolerates more.
OVERPASS_MIRRORS = [OVERPASS_URL, "https://overpass.kumi.systems/api/interpreter"]
USER_AGENT = "prodrive-ac-builder/0.1 (https://github.com/k10-motorsports/prodrive-ac-builder)"
DRIVABLE = r'^(motorway|trunk|primary|secondary|tertiary|unclassified|residential|living_street|service)(_link)?$'


def _post(query: str, *, retries: int = 4) -> dict:
    body = urllib.parse.urlencode({"data": query}).encode()
    last: Exception | None = None
    for attempt in range(retries):
        for url in OVERPASS_MIRRORS:
            req = urllib.request.Request(url, data=body,
                                         headers={"User-Agent": USER_AGENT,
                                                  "Accept": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=150) as resp:
                    return json.loads(resp.read().decode())
            except Exception as e:  # noqa: BLE001 — 429/504/timeouts all retry the same way
                last = e
        wait = 20 * (attempt + 1) if isinstance(last, urllib.error.HTTPError) and last.code == 429 \
            else 3 * (attempt + 1)
        time.sleep(wait)
    raise last  # type: ignore[misc]


def _ways(payload: dict) -> list[dict]:
    ways = []
    for el in payload.get("elements", []):
        if el.get("type") != "way":
            continue
        geom = [(g["lon"], g["lat"]) for g in el.get("geometry") or []]
        if len(geom) >= 2:
            ways.append({"name": el.get("tags", {}).get("name"),
                         "highway": el.get("tags", {}).get("highway"), "geom": geom})
    return ways


def fetch_drivable(bbox: tuple[float, float, float, float], *, retries: int = 4) -> list[dict]:
    """Return ways as ``{"name", "highway", "geom":[(lon,lat)...]}`` within bbox (s,w,n,e)."""
    s, w, n, e = bbox
    return _ways(_post(f'[out:json][timeout:90];way["highway"~"{DRIVABLE}"]'
                       f'({s},{w},{n},{e});out tags geom;', retries=retries))


def fetch_named(bbox: tuple[float, float, float, float], names: list[str], *,
                retries: int = 4) -> list[dict]:
    """Return highway ways whose name matches any of ``names`` (regex OR) within bbox (s,w,n,e)."""
    s, w, n, e = bbox
    rx = "|".join(names)
    return _ways(_post(f'[out:json][timeout:90];way["highway"]["name"~"{rx}"]'
                       f'({s},{w},{n},{e});out tags geom;', retries=retries))
