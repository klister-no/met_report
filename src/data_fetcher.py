"""
data_fetcher.py
---------------
Fetches weather observations and forecasts for all configured regions.

Primary source:  Open-Meteo (free, ECMWF-based, no API key required)
Secondary source: AEMET OpenData (Spain, requires API key in env)
Fallback:        Previous week's data from local cache

All returned values use metric units (°C, mm).

New in this version:
  - fetch_7day_temp()     : daglig temp siste 7 dager per region
  - fetch_30day_precip()  : daglig nedbør siste 30 dager per region
  Both are called automatically in fetch_all_regions() and stored on each record.
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
REQUEST_TIMEOUT    = 30   # økt fra 20 — archive-api er treg fra GitHub Actions
RETRY_ATTEMPTS     = 2    # redusert fra 3 — sparer tid ved timeout (2x30s vs 3x20s)
RETRY_DELAY        = 3    # sekunder mellom forsøk


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


# ── Open-Meteo — ukesdata (eksisterende) ─────────────────────────────────────

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
        "timezone":           "Europe/Madrid",
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
    """
    Fetches daily forecast for next N days.
    Inkluderer nå også vindkast (windgusts_10m) og vedvarende vind (windspeed_10m)
    for produksjonsrisikovurdering.
    """
    params = {
        "latitude":          lat,
        "longitude":         lon,
        "forecast_days":     days,
        "daily":             ("temperature_2m_max,temperature_2m_min,"
                              "precipitation_sum,precipitation_probability_max,"
                              "windgusts_10m_max,windspeed_10m_max"),
        "wind_speed_unit":   "ms",   # m/s — konsistent med Gibraltar
        "timezone":          "Europe/Madrid",
    }
    data = _request_with_retry(OPEN_METEO_URL, params=params)
    if not data or "daily" not in data:
        return None

    d = data["daily"]

    # Maks vindkast neste 7 dager — brukes i risikoklassifisering
    gusts_7d   = [v for v in (d.get("windgusts_10m_max", []) or [])[:7] if v is not None]
    wind_7d    = [v for v in (d.get("windspeed_10m_max",  []) or [])[:7] if v is not None]

    return {
        "temp_max":            d.get("temperature_2m_max", []),
        "temp_min":            d.get("temperature_2m_min", []),
        "precip_sum":          d.get("precipitation_sum", []),
        "precip_prob_max":     d.get("precipitation_probability_max", []),
        "windgusts_10m_max":   d.get("windgusts_10m_max", []),
        "windspeed_10m_max":   d.get("windspeed_10m_max", []),
        # Aggregerte verdier for enkel bruk i analyzer
        "wind_gust_max_7d_ms":    round(max(gusts_7d), 1) if gusts_7d else None,
        "wind_sustained_max_7d_ms": round(max(wind_7d), 1) if wind_7d else None,
        "dates":               d.get("time", []),
        "source":              "open-meteo-forecast",
    }


# ── Open-Meteo — 7-dagers temperatur ─────────────────────────────────────────

def fetch_7day_temp(lat: float, lon: float) -> list[dict]:
    """
    Henter daglig temperatur (max/min/mean) siste 7 dager fra Open-Meteo archive.
    Returnerer liste med dicts: [{date, temp_max, temp_min, temp_mean}, ...]
    sortert eldst → nyest.

    Brukes i temperatur-tabellen for å vise 7-dagers snitt og avvik.
    Returnerer [] ved feil — aldri None.
    """
    end_date   = date.today() - timedelta(days=1)   # i går (archive har 1d latency)
    start_date = end_date - timedelta(days=6)        # 7 dager totalt

    params = {
        "latitude":   lat,
        "longitude":  lon,
        "start_date": start_date.isoformat(),
        "end_date":   end_date.isoformat(),
        "daily":      "temperature_2m_max,temperature_2m_min,temperature_2m_mean",
        "timezone":   "UTC",
    }
    data = _request_with_retry(OPEN_METEO_HIST, params=params)
    if not data or "daily" not in data:
        logger.warning(f"fetch_7day_temp failed for ({lat},{lon})")
        return []

    d         = data["daily"]
    dates     = d.get("time", [])
    temp_max  = d.get("temperature_2m_max", [])
    temp_min  = d.get("temperature_2m_min", [])
    temp_mean = d.get("temperature_2m_mean", [])

    result = []
    for i, dt in enumerate(dates):
        mx = temp_max[i]  if i < len(temp_max)  else None
        mn = temp_min[i]  if i < len(temp_min)  else None
        me = temp_mean[i] if i < len(temp_mean) else None
        result.append({
            "date":      dt,
            "temp_max":  round(float(mx), 1) if mx is not None else None,
            "temp_min":  round(float(mn), 1) if mn is not None else None,
            "temp_mean": round(float(me), 1) if me is not None else None,
        })
    return result


# ── Open-Meteo — 30-dagers nedbør ────────────────────────────────────────────

def fetch_30day_precip(lat: float, lon: float) -> list[dict]:
    """
    Henter daglig nedbør (mm) siste 30 dager fra Open-Meteo archive.
    Returnerer liste med dicts: [{date, precip_mm}, ...]
    sortert eldst → nyest.

    Brukes i trendlinje-grafen (30 søyler med normallinje).
    Returnerer [] ved feil — aldri None.
    """
    end_date   = date.today() - timedelta(days=1)
    start_date = end_date - timedelta(days=29)   # 30 dager totalt

    params = {
        "latitude":   lat,
        "longitude":  lon,
        "start_date": start_date.isoformat(),
        "end_date":   end_date.isoformat(),
        "daily":      "precipitation_sum",
        "timezone":   "UTC",
    }
    data = _request_with_retry(OPEN_METEO_HIST, params=params)
    if not data or "daily" not in data:
        logger.warning(f"fetch_30day_precip failed for ({lat},{lon})")
        return []

    d      = data["daily"]
    dates  = d.get("time", [])
    precip = d.get("precipitation_sum", [])

    result = []
    for i, dt in enumerate(dates):
        p = precip[i] if i < len(precip) else None
        result.append({
            "date":      dt,
            "precip_mm": round(float(p), 1) if p is not None else 0.0,
        })
    return result



# ── Historiske varianter — relativ til en spesifikk uke ──────────────────────

def fetch_7day_temp_for_week(lat: float, lon: float,
                              week_end: date) -> list[dict]:
    """
    Historisk variant av fetch_7day_temp.
    Henter de 7 dagene som ender på week_end (dvs. selve rapportuken).
    """
    end_date   = week_end
    start_date = end_date - timedelta(days=6)

    params = {
        "latitude":   lat,
        "longitude":  lon,
        "start_date": start_date.isoformat(),
        "end_date":   end_date.isoformat(),
        "daily":      "temperature_2m_max,temperature_2m_min,temperature_2m_mean",
        "timezone":   "UTC",
    }
    data = _request_with_retry(OPEN_METEO_HIST, params=params)
    if not data or "daily" not in data:
        logger.warning(f"fetch_7day_temp_for_week failed for ({lat},{lon}) week_end={week_end}")
        return []

    d = data["daily"]
    result = []
    for i, dt in enumerate(d.get("time", [])):
        mx = d["temperature_2m_max"][i]  if i < len(d.get("temperature_2m_max",  [])) else None
        mn = d["temperature_2m_min"][i]  if i < len(d.get("temperature_2m_min",  [])) else None
        me = d["temperature_2m_mean"][i] if i < len(d.get("temperature_2m_mean", [])) else None
        result.append({
            "date":      dt,
            "temp_max":  round(float(mx), 1) if mx is not None else None,
            "temp_min":  round(float(mn), 1) if mn is not None else None,
            "temp_mean": round(float(me), 1) if me is not None else None,
        })
    return result


def fetch_30day_precip_for_week(lat: float, lon: float,
                                 week_end: date) -> list[dict]:
    """
    Historisk variant av fetch_30day_precip.
    Henter 30 dager som ender på week_end.
    """
    end_date   = week_end
    start_date = end_date - timedelta(days=29)

    params = {
        "latitude":   lat,
        "longitude":  lon,
        "start_date": start_date.isoformat(),
        "end_date":   end_date.isoformat(),
        "daily":      "precipitation_sum",
        "timezone":   "UTC",
    }
    data = _request_with_retry(OPEN_METEO_HIST, params=params)
    if not data or "daily" not in data:
        logger.warning(f"fetch_30day_precip_for_week failed for ({lat},{lon}) week_end={week_end}")
        return []

    d      = data["daily"]
    result = []
    for i, dt in enumerate(d.get("time", [])):
        p = d["precipitation_sum"][i] if i < len(d.get("precipitation_sum", [])) else None
        result.append({
            "date":      dt,
            "precip_mm": round(float(p), 1) if p is not None else 0.0,
        })
    return result


def fetch_open_meteo_forecast_historical(lat: float, lon: float,
                                          week_end: date) -> Optional[dict]:
    """
    Historisk 'forecast': henter arkivdata for de 7 dagene ETTER week_end.
    Brukes i stedet for live forecast når man kjører for en historisk uke.
    Inkluderer vind.
    """
    start = week_end + timedelta(days=1)
    end   = week_end + timedelta(days=7)

    # Kan ikke hente fremtidig arkiv — returner None stille
    if start > date.today() - timedelta(days=1):
        logger.info(f"    Historical post-week is in the future — skipping forecast for ({lat},{lon})")
        return None

    params = {
        "latitude":        lat,
        "longitude":       lon,
        "start_date":      start.isoformat(),
        "end_date":        end.isoformat(),
        "daily":           ("temperature_2m_max,temperature_2m_min,"
                            "precipitation_sum,precipitation_probability_max,"
                            "windgusts_10m_max,windspeed_10m_max"),
        "wind_speed_unit": "ms",
        "timezone":        "Europe/Madrid",
    }
    data = _request_with_retry(OPEN_METEO_HIST, params=params)
    if not data or "daily" not in data:
        return None

    d = data["daily"]
    gusts = [v for v in (d.get("windgusts_10m_max", []) or []) if v is not None]
    winds = [v for v in (d.get("windspeed_10m_max",  []) or []) if v is not None]

    return {
        "temp_max":                 d.get("temperature_2m_max", []),
        "temp_min":                 d.get("temperature_2m_min", []),
        "precip_sum":               d.get("precipitation_sum", []),
        "precip_prob_max":          d.get("precipitation_probability_max", []),
        "windgusts_10m_max":        d.get("windgusts_10m_max", []),
        "windspeed_10m_max":        d.get("windspeed_10m_max", []),
        "wind_gust_max_7d_ms":      round(max(gusts), 1) if gusts else None,
        "wind_sustained_max_7d_ms": round(max(winds), 1) if winds else None,
        "dates":                    d.get("time", []),
        "source":                   "open-meteo-archive-postweek",
    }


# ── AEMET (Spain only) ────────────────────────────────────────────────────────

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

    meta = _request_with_retry(url, headers=headers)
    if not meta or "datos" not in meta:
        return None

    data = _request_with_retry(meta["datos"], headers=headers)
    if not data:
        return None

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


# ── Main fetch orchestrator ───────────────────────────────────────────────────

def fetch_all_regions(config_path: str = "config/regions.yaml",
                      use_cache: bool = True,
                      week_start_override: Optional[date] = None) -> dict[str, dict]:
    """
    Fetches observation data for a given week and 14-day forecast for all regions.

    week_start_override: hvis satt, hentes data for denne uken (historisk modus).
    Historisk modus:
      - Observasjoner hentes fra Open-Meteo arkiv (fungerer for alle datoer)
      - Forecast erstattes med arkivdata for uken etter (week+1..week+2)
      - 7d temp og 30d nedbør hentes relativt til week_end (ikke today)
      - AEMET: kun live — ikke tilgjengelig for historiske datoer
    """
    regions = _load_regions(config_path)

    if week_start_override:
        week_start = week_start_override
        if not isinstance(week_start, date):
            week_start = date.fromisoformat(str(week_start_override))
        historical = True
    else:
        week_start, _ = _get_week_bounds()
        historical = False

    week_end = week_start + timedelta(days=6)
    results  = {}

    mode_str = f"HISTORISK ({week_start})" if historical else f"live ({week_start})"
    logger.info(f"Fetching data — modus: {mode_str}, uke {week_start} – {week_end}")

    for region in regions:
        rid = region["id"]
        logger.info(f"  Fetching: {region['name']}")

        # ── Try cache first ──────────────────────────────────────────────────
        if use_cache:
            cached = _load_cache(rid, week_start)
            if cached:
                lat = region["lat"]
                lon = region["lon"]

                # Valider at observasjoner faktisk inneholder data
                cached_obs = cached.get("observations") or {}
                obs_valid  = bool(cached_obs.get("temp_max") or cached_obs.get("temp_min"))

                if not obs_valid:
                    logger.info(f"    Cache: observasjoner mangler/tomme for {rid} — henter på nytt")
                    new_obs = None
                    if not historical and region.get("aemet_station") and region.get("country") == "Spain":
                        new_obs = fetch_aemet_station(region["aemet_station"], week_start, week_end)
                    if not new_obs:
                        new_obs = fetch_open_meteo_week(lat, lon, week_start, week_end)
                    if new_obs:
                        cached["observations"] = new_obs
                    time.sleep(0.3)
                else:
                    logger.info(f"    Cache hit for {rid}")

                missing_7d  = "temp_7d_daily"    not in cached or not cached["temp_7d_daily"]
                missing_30d = "precip_30d_daily" not in cached or not cached["precip_30d_daily"]
                if missing_7d or missing_30d:
                    if missing_7d:
                        cached["temp_7d_daily"] = (
                            fetch_7day_temp_for_week(lat, lon, week_end) if historical
                            else fetch_7day_temp(lat, lon)
                        )
                        time.sleep(0.3)
                    if missing_30d:
                        cached["precip_30d_daily"] = (
                            fetch_30day_precip_for_week(lat, lon, week_end) if historical
                            else fetch_30day_precip(lat, lon)
                        )
                        time.sleep(0.3)
                # Supplement with wind if missing
                fcast = cached.get("forecast_14d") or {}
                if "wind_gust_max_7d_ms" not in fcast:
                    logger.info(f"    Cache: mangler vinddata — henter forecast på nytt for {rid}")
                    new_fcast = (
                        fetch_open_meteo_forecast_historical(lat, lon, week_end)
                        if historical
                        else fetch_open_meteo_forecast(lat, lon, days=14)
                    )
                    if new_fcast:
                        cached["forecast_14d"] = new_fcast
                    time.sleep(0.3)
                results[rid] = cached
                continue

        lat = region["lat"]
        lon = region["lon"]

        # ── Observations ─────────────────────────────────────────────────────
        obs = None
        if not historical and region.get("aemet_station") and region.get("country") == "Spain":
            obs = fetch_aemet_station(region["aemet_station"], week_start, week_end)
            if obs:
                logger.info(f"    AEMET data retrieved for {rid}")

        if not obs:
            obs = fetch_open_meteo_week(lat, lon, week_start, week_end)
            if obs:
                logger.info(f"    Open-Meteo archive retrieved for {rid}")
            else:
                logger.error(f"    FAILED to fetch observations for {rid}")

        # ── Forecast / post-week arkiv ────────────────────────────────────────
        if historical:
            # For historiske uker: hent arkivdata for uken etter som "forecast"
            fcast = fetch_open_meteo_forecast_historical(lat, lon, week_end)
            if not fcast:
                logger.warning(f"    Historical post-week data unavailable for {rid}")
        else:
            fcast = fetch_open_meteo_forecast(lat, lon, days=14)
            if not fcast:
                logger.error(f"    FAILED to fetch forecast for {rid}")

        # ── 7-dagers temperatur ───────────────────────────────────────────────
        temp_7d = (
            fetch_7day_temp_for_week(lat, lon, week_end) if historical
            else fetch_7day_temp(lat, lon)
        )
        if temp_7d:
            logger.info(f"    7d temp fetched ({len(temp_7d)} days)")
        else:
            logger.warning(f"    7d temp FAILED for {rid}")
        time.sleep(0.2)

        # ── 30-dagers nedbør ─────────────────────────────────────────────────
        precip_30d = (
            fetch_30day_precip_for_week(lat, lon, week_end) if historical
            else fetch_30day_precip(lat, lon)
        )
        if precip_30d:
            logger.info(f"    30d precip fetched ({len(precip_30d)} days)")
        else:
            logger.warning(f"    30d precip FAILED for {rid}")
        time.sleep(0.2)

        record = {
            "region":           region,
            "observations":     obs,
            "forecast_14d":     fcast,
            "temp_7d_daily":    temp_7d,
            "precip_30d_daily": precip_30d,
            "week_start":       week_start.isoformat(),
            "week_end":         week_end.isoformat(),
            "historical":       historical,
        }

        _save_cache(rid, week_start, record)
        results[rid] = record

        time.sleep(0.3)

    logger.info(f"Fetch complete: {len(results)}/{len(regions)} regions")
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s  %(levelname)-8s %(message)s")
    data = fetch_all_regions()
    for rid, rec in data.items():
        obs       = rec.get("observations")
        temp_7d   = rec.get("temp_7d_daily", [])
        precip_30 = rec.get("precip_30d_daily", [])
        print(f"{rid:25s}  obs={'OK' if obs else 'FAIL'}"
              f"  7d={len(temp_7d)}d"
              f"  30d={len(precip_30)}d")
