"""
Copernicus EMS Rapid Mapping integration for ATerraTreme.

Uses the public Rapid Mapping API:
  - List:  https://rapidmapping.emergency.copernicus.eu/backend/dashboard-api/public-activations-info/
  - Detail: https://rapidmapping.emergency.copernicus.eu/backend/dashboard-api/public-activations/?code={code}

Focus: earthquake activations relevant to Portugal / Iberia / nearby,
plus generic lookup by EMSR code.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from typing import Any

import requests

logger = logging.getLogger("aterratreme.cems")

CEMS_LIST_URL = (
    "https://rapidmapping.emergency.copernicus.eu/"
    "backend/dashboard-api/public-activations-info/"
)
CEMS_DETAIL_URL = (
    "https://rapidmapping.emergency.copernicus.eu/"
    "backend/dashboard-api/public-activations/"
)

# Countries / regions we care about for the Portuguese context
RELEVANT_COUNTRIES = {
    "portugal",
    "spain",
    "morocco",
    "algeria",
    "france",  # sometimes shared Atlantic/Pyrenees events
}

EARTHQUAKE_CATEGORIES = {"earthquake", "seismic", "ground shaking"}

# Simple in-memory cache (process lifetime)
_cache: dict[str, Any] = {
    "list": None,
    "list_ts": 0.0,
    "details": {},  # code -> (ts, data)
}
CACHE_TTL_LIST = 300      # 5 min
CACHE_TTL_DETAIL = 600    # 10 min


def _session(user_agent: str = "ATerraTreme/1.0 (+https://vost.pt)") -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": user_agent,
        "Accept": "application/json",
    })
    return s


def _parse_wkt_point(wkt: str | None) -> tuple[float | None, float | None]:
    """POINT (lon lat) -> (lon, lat)."""
    if not wkt:
        return None, None
    m = re.search(r"POINT\s*\(\s*([-\d.]+)\s+([-\d.]+)\s*\)", wkt, re.I)
    if not m:
        return None, None
    return float(m.group(1)), float(m.group(2))


def fetch_activations(
    session: requests.Session | None = None,
    limit: int = 50,
    force: bool = False,
) -> list[dict]:
    """Fetch recent public Rapid Mapping activations (cached)."""
    now = time.time()
    if (
        not force
        and _cache["list"] is not None
        and (now - _cache["list_ts"]) < CACHE_TTL_LIST
    ):
        return _cache["list"]

    sess = session or _session()
    results: list[dict] = []
    url = f"{CEMS_LIST_URL}?limit={limit}"

    try:
        while url and len(results) < limit:
            r = sess.get(url, timeout=25)
            r.raise_for_status()
            payload = r.json()
            batch = payload.get("results") or []
            results.extend(batch)
            url = payload.get("next")
            if not batch:
                break
    except Exception as e:
        logger.warning("CEMS list fetch failed: %s", e)
        return _cache["list"] or []

    _cache["list"] = results[:limit]
    _cache["list_ts"] = now
    return _cache["list"]


def fetch_activation_detail(
    code: str,
    session: requests.Session | None = None,
    force: bool = False,
) -> dict | None:
    """Fetch full activation (AOIs, products, stats, download links)."""
    code = (code or "").strip().upper()
    if not code:
        return None

    now = time.time()
    cached = _cache["details"].get(code)
    if (
        not force
        and cached
        and (now - cached[0]) < CACHE_TTL_DETAIL
    ):
        return cached[1]

    sess = session or _session()
    try:
        r = sess.get(CEMS_DETAIL_URL, params={"code": code}, timeout=30)
        r.raise_for_status()
        payload = r.json()
        items = payload.get("results") or []
        detail = items[0] if items else None
    except Exception as e:
        logger.warning("CEMS detail %s failed: %s", code, e)
        return cached[1] if cached else None

    if detail:
        _cache["details"][code] = (now, detail)
    return detail


def _is_earthquake(act: dict) -> bool:
    cat = (act.get("category") or "").lower()
    name = (act.get("name") or "").lower()
    sub = (act.get("subCategory") or "").lower()
    if any(k in cat for k in EARTHQUAKE_CATEGORIES):
        return True
    if "earthquake" in name or "sismo" in name or "seismic" in name:
        return True
    if "ground shaking" in sub:
        return True
    return False


def _touches_relevant_country(act: dict) -> bool:
    countries = act.get("countries") or []
    names: list[str] = []
    for c in countries:
        if isinstance(c, str):
            names.append(c.lower())
        elif isinstance(c, dict):
            names.append((c.get("name") or c.get("short_name") or "").lower())
    return any(n in RELEVANT_COUNTRIES for n in names)


def filter_earthquake_activations(
    activations: list[dict] | None = None,
    only_relevant_geo: bool = False,
    include_open_only: bool = False,
) -> list[dict]:
    """Filter list to earthquake (and optionally Iberia-relevant) activations."""
    acts = activations if activations is not None else fetch_activations()
    out = []
    for a in acts:
        if not _is_earthquake(a):
            continue
        if include_open_only and a.get("closed"):
            continue
        if only_relevant_geo and not _touches_relevant_country(a):
            continue
        lon, lat = _parse_wkt_point(a.get("centroid"))
        out.append({
            "code": a.get("code"),
            "name": a.get("name"),
            "category": a.get("category"),
            "eventTime": a.get("eventTime"),
            "activationTime": a.get("activationTime"),
            "closed": a.get("closed"),
            "countries": a.get("countries"),
            "n_aois": a.get("n_aois"),
            "n_products": a.get("n_products"),
            "gdacsId": a.get("gdacsId"),
            "centroid_lon": lon,
            "centroid_lat": lat,
            "portal_url": f"https://mapping.emergency.copernicus.eu/activations/{a.get('code')}",
        })
    return out


def summarise_damage(detail: dict) -> dict:
    """
    Aggregate simple damage stats from grading products (GRA).
    Returns counts of affected buildings, population estimates, etc.
    """
    summary = {
        "code": detail.get("code"),
        "name": detail.get("name"),
        "closed": detail.get("closed"),
        "reportLink": detail.get("reportLink"),
        "productsPath": detail.get("productsPath"),
        "aois": [],
        "totals": {
            "buildings_affected": 0,
            "population_estimated": 0,
            "products_finished": 0,
            "products_pending": 0,
        },
    }

    for aoi in detail.get("aois") or []:
        aoi_info = {
            "name": aoi.get("name"),
            "number": aoi.get("number"),
            "products": [],
        }
        for p in aoi.get("products") or []:
            status = (p.get("version") or {}).get("statusCode")
            if status == "F":
                summary["totals"]["products_finished"] += 1
            elif status in ("W", "I"):
                summary["totals"]["products_pending"] += 1

            stats = p.get("stats") or {}
            buildings = 0
            pop = 0
            built = stats.get("Built-up") or {}
            for _k, v in built.items():
                if isinstance(v, dict):
                    aff = v.get("affected")
                    if isinstance(aff, (int, float)):
                        buildings += int(aff)
            est = stats.get("Estimated population") or {}
            for _k, v in est.items():
                if isinstance(v, dict):
                    tot = v.get("total")
                    if isinstance(tot, (int, float)):
                        pop += int(tot)

            summary["totals"]["buildings_affected"] += buildings
            summary["totals"]["population_estimated"] += pop

            aoi_info["products"].append({
                "type": p.get("type"),
                "status": status,
                "downloadPath": p.get("downloadPath") or None,
                "buildings_affected": buildings or None,
                "population_in_aoi": pop or None,
                "mapsCount": p.get("mapsCount"),
            })
        summary["aois"].append(aoi_info)

    return summary


def find_matching_activation_for_sismo(
    lat: float,
    lon: float,
    event_time_iso: str | None = None,
    max_distance_km: float = 250.0,
    max_time_hours: float = 72.0,
) -> dict | None:
    """
    Heuristic: among recent earthquake activations, pick one whose centroid
    is near the IPMA epicentre and (optionally) close in time.
    """
    from math import radians, cos, sin, asin, sqrt

    def haversine(lat1, lon1, lat2, lon2):
        r = 6371.0
        dlat = radians(lat2 - lat1)
        dlon = radians(lon2 - lon1)
        a = (
            sin(dlat / 2) ** 2
            + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
        )
        return 2 * r * asin(sqrt(a))

    candidates = filter_earthquake_activations(only_relevant_geo=False)
    best = None
    best_dist = max_distance_km

    event_dt = None
    if event_time_iso:
        try:
            event_dt = datetime.fromisoformat(
                event_time_iso.replace("Z", "+00:00")
            )
        except Exception:
            event_dt = None

    for c in candidates:
        clon, clat = c.get("centroid_lon"), c.get("centroid_lat")
        if clon is None or clat is None:
            continue
        dist = haversine(lat, lon, clat, clon)
        if dist > best_dist:
            continue
        if event_dt and c.get("eventTime"):
            try:
                act_dt = datetime.fromisoformat(
                    c["eventTime"].replace("Z", "+00:00")
                )
                hours = abs((act_dt - event_dt).total_seconds()) / 3600.0
                if hours > max_time_hours:
                    continue
            except Exception:
                pass
        best_dist = dist
        best = {**c, "distance_km": round(dist, 1)}

    return best


# ---------------------------------------------------------------------------
# Optional background poller (call from ATerraTreme monitor thread)
# ---------------------------------------------------------------------------

_seen_codes: set[str] = set()


def poll_new_earthquake_activations(
    session: requests.Session | None = None,
    only_relevant_geo: bool = True,
) -> list[dict]:
    """
    Returns newly seen earthquake activations since process start.
    Safe to call every few minutes from the existing monitor loop.
    """
    acts = filter_earthquake_activations(
        fetch_activations(session=session),
        only_relevant_geo=only_relevant_geo,
    )
    new = []
    for a in acts:
        code = a.get("code")
        if not code or code in _seen_codes:
            continue
        _seen_codes.add(code)
        # first run: seed without treating everything as "new"
        if len(_seen_codes) <= len(acts):
            # still mark as seen; only emit if we already had a baseline
            continue
        new.append(a)
    # After first successful poll, baseline is set; subsequent calls emit deltas
    if not hasattr(poll_new_earthquake_activations, "_primed"):
        poll_new_earthquake_activations._primed = True
        return []
    return new
