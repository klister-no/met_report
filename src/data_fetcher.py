"""
data_fetcher.py
---------------
Fetches weather observations and forecasts for all configured regions.

Primary source:  Open-Meteo (free, ECMWF-based, no API key required)
Secondary source: AEMET OpenData (Spain, requires API key in env)
Fallback:        Previous week's data from local cache

All returned values use metric units (°C, mm).
"""

import os
import json
import logging
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import requests
import yaml

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
OPEN_METEO_URL     = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_HIST    = "https://archive-api.open-meteo.com/v1/archive"
AEMET_BASE_URL     = "https://opendata.aemet.es/openapi/api"
CACHE_DIR          = Path("data/cache")
REQUEST_TIMEOUT    = 20   # seconds
RETRY_ATTEMPTS     = 3
RETRY_DELAY        = 5    # seconds between retries


def _load_regions(config_path: str = "config/regions.yaml") -> list[dict]:
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    return cfg["regions"]


def _get_week_bounds() -> tuple[date, date]:
    """Returns (monday, sunday) of the most recently completed week."""
    today = date.today()
    last_monday = today - timedelta(days=today.weekday() + 7)
    last_sunday = last_monday + timedelta(days=6)
    return last_monday, last_sunday


def _request_with_retry(url: str, params: dict = None,
                         headers: dict = None) -> Optional[dict]:
    """GET request with retry logic and timeout."""
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            resp = requests.get(url, params=params, headers=headers,
                                timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.HTTPError as e:
            logger.warning(f"HTTP {e.response.status_code} on attempt {attempt}: {url}")
        except requests.exceptions.RequestException as e:
            logger.warning(f"Request error on attempt {attempt}: {e}")
        if attempt < RETRY_ATTEMPTS:
            time.sleep(RETRY_DELAY)
    logger.error(f"All {RETRY_ATTEMPTS} attempts failed for: {url}")
    return None


# ── Open-Meteo ────────────────────────────────────────────────────────────────

def fetch_open_meteo_week(lat: float, lon: float,
                          start: date, end: date) -> Optional[dict]:
    """
    Fetches daily observations for a completed week.
    Returns dict with lists: temp_max, temp_min, precip_sum (all daily).
    """
    params = {
        "latitude":           lat,
        "longitude":          lon,
        "start_date":         start.isoformat(),
        "end_date":           end.isoformat(),
        "daily":              "temperature_2m_max,temperature_2m_min,precipitation_sum",
        "timezone":           "Europe/Madrid",   # consistent reference TZ
        "wind_speed_unit":    "kmh",
    }
    data = _request_with_retry(OPEN_METEO_HIST, params=params)
    if not data or "daily" not in data:
        return None

    d = data["daily"]
    return {
        "temp_max":   d.get("temperature_2m_max", []),
        "temp_min":   d.get("temperature_2m_min", []),
        "precip_sum": d.get("precipitation_sum", []),
        "dates":      d.get("time", []),
        "source":     "open-meteo-archive",
    }


def fetch_open_meteo_forecast(lat: float, lon: float,
                               days: int = 14) -> Optional[dict]:
    """Fetches daily forecast for next N days."""
    params = {
        "latitude":        lat,
        "longitude":       lon,
        "forecast_days":   days,
        "daily":           "temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max",
        "timezone":        "Europe/Madrid",
    }
    data = _request_with_retry(OPEN_METEO_URL, params=params)
    if not data or "daily" not in data:
        return None

    d = data["daily"]
    return {
        "temp_max":          d.get("temperature_2m_max", []),
        "temp_min":          d.get("temperature_2m_min", []),
        "precip_sum":        d.get("precipitation_sum", []),
        "precip_prob_max":   d.get("precipitation_probability_max", []),
        "dates":             d.get("time", []),
        "source":            "open-meteo-forecast",
    }


# ── AEMET (Spain only) ─────────────────────────────────────────────────────────

def fetch_aemet_station(station_id: str, start: date, end: date) -> Optional[dict]:
    """
    Fetches daily climatological data from AEMET for a Spanish station.
    Requires AEMET_API_KEY environment variable.
    Returns None gracefully if key is missing (falls back to Open-Meteo).
    """
    api_key = os.getenv("AEMET_API_KEY")
    if not api_key:
        logger.info("AEMET_API_KEY not set – skipping AEMET fetch")
        return None

    headers = {"api_key": api_key, "Accept": "application/json"}
    url = (f"{AEMET_BASE_URL}/valores/climatologicos/diarios/datos"
           f"/fechaini/{start.strftime('%Y-%m-%dT00:00:00UTC')}"
           f"/fechafin/{end.strftime('%Y-%m-%dT23:59:59UTC')}"
           f"/estacion/{station_id}")

    # AEMET uses a two-step fetch: first get data URL, then fetch data
    meta = _request_with_retry(url, headers=headers)
    if not meta or "datos" not in meta:
        return None

    data = _request_with_retry(meta["datos"], headers=headers)
    if not data:
        return None

    # Parse AEMET response format
    try:
        temps_max  = [float(d["tmax"].replace(",", ".")) for d in data if "tmax" in d]
        temps_min  = [float(d["tmin"].replace(",", ".")) for d in data if "tmin" in d]
        precip     = [float(d["prec"].replace(",", ".")) for d in data
                      if "prec" in d and d["prec"] not in ("Ip", "", None)]
        return {
            "temp_max":   temps_max,
            "temp_min":   temps_min,
            "precip_sum": precip,
            "source":     f"aemet-{station_id}",
        }
    except (KeyError, ValueError) as e:
        logger.warning(f"AEMET parse error for station {station_id}: {e}")
        return None


# ── Cache ─────────────────────────────────────────────────────────────────────

def _cache_path(region_id: str, week_start: date) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"{region_id}_{week_start.isoformat()}.json"


def _load_cache(region_id: str, week_start: date) -> Optional[dict]:
    path = _cache_path(region_id, week_start)
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return None


def _save_cache(region_id: str, week_start: date, data: dict):
    path = _cache_path(region_id, week_start)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)


# ── Main fetch orchestrator ────────────────────────────────────────────────────

def fetch_all_regions(config_path: str = "config/regions.yaml",
                      use_cache: bool = True) -> dict[str, dict]:
    """
    Fetches observation data for the last completed week and
    14-day forecast for all configured regions.

    Returns dict keyed by region id:
    {
      "IT_PO_VALLEY": {
        "region": {...config...},
        "observations": {...},    # last week
        "forecast_14d": {...},    # next 14 days
        "week_start": date,
        "week_end": date,
      },
      ...
    }
    """
    regions    = _load_regions(config_path)
    week_start, week_end = _get_week_bounds()
    results    = {}

    logger.info(f"Fetching data for week {week_start} – {week_end}")

    for region in regions:
        rid = region["id"]
        logger.info(f"  Fetching: {region['name']}")

        # ── Try cache first ──────────────────────────────────────────────────
        if use_cache:
            cached = _load_cache(rid, week_start)
            if cached:
                logger.info(f"    Cache hit for {rid}")
                results[rid] = cached
                continue

        # ── Observations: prefer AEMET for Spanish stations ──────────────────
        obs = None
        if region.get("aemet_station") and region["country"] == "Spain":
            obs = fetch_aemet_station(region["aemet_station"], week_start, week_end)
            if obs:
                logger.info(f"    AEMET data retrieved for {rid}")

        # Fallback to Open-Meteo archive
        if not obs:
            obs = fetch_open_meteo_week(region["lat"], region["lon"],
                                        week_start, week_end)
            if obs:
                logger.info(f"    Open-Meteo archive retrieved for {rid}")
            else:
                logger.error(f"    FAILED to fetch observations for {rid}")

        # ── Forecast ─────────────────────────────────────────────────────────
        fcast = fetch_open_meteo_forecast(region["lat"], region["lon"], days=14)
        if not fcast:
            logger.error(f"    FAILED to fetch forecast for {rid}")

        record = {
            "region":       region,
            "observations": obs,
            "forecast_14d": fcast,
            "week_start":   week_start.isoformat(),
            "week_end":     week_end.isoformat(),
        }

        _save_cache(rid, week_start, record)
        results[rid] = record

        time.sleep(0.3)   # be polite to Open-Meteo free tier

    logger.info(f"Fetch complete: {len(results)}/{len(regions)} regions")
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s  %(levelname)-8s %(message)s")
    data = fetch_all_regions()
    for rid, rec in data.items():
        obs = rec.get("observations")
        print(f"{rid:25s}  obs={'OK' if obs else 'FAIL'}")
