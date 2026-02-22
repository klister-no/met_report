"""
norway_fetcher.py
-----------------
Henter værobservasjoner og prognoser for norske frukt/grønt-produksjonsregioner.

Datakilder:
  1. Frost API (met.no) — historiske og sanntids stasjonsobs:
       - air_temperature (min/max/mean) → vekstgraddager, avvik
       - precipitation_amount           → jordmetning, nedbørsavvik
       - soil_temperature               → spiring
       - wind_speed                     → løktørking
       - frost events                   → salat/løk-skade
       - growing_degree_days            → sesongmodell
  2. Locationforecast API (met.no) — 10-dagersprognose (ingen nøkkel)

Regionstruktur: 7 regioner med totalt 35 stasjoner
  - Østfold            (Rakkestad, Sarpsborg, Askim, Rygge, Fredrikstad, Hvaler)
  - Vestfold           (Larvik, Torp, Andebu, Tønsberg/Sem, Færder)
  - Lier / Drammen     (Lier, Drammen, Tranby, Asker)
  - Hedmark / Innlandet (Kongsvinger, Hamar, Elverum, Alvdal)
  - Oppland / Innlandet (Lillehammer, Gjøvik, Fåvang, Vågå)
  - Trøndelag / Frosta  (Levanger, Frosta, Værnes, Verdal, Ørland, Trondheim Voll)
  - Rogaland / Jæren    (Sola, Stavanger, Randaberg, Bryne, Klepp, Nærbø)

Frost API-parametre:
  air_temperature, precipitation_amount, soil_temperature, wind_speed, frost_event
  growing_degree_days er beregnet lokalt fra temperaturdata.
"""

import os
import requests
import logging
from datetime import datetime, timezone, timedelta, date
from typing import Optional

logger = logging.getLogger(__name__)

CET = timezone(timedelta(hours=1))

HEADERS_METNO = {
    "User-Agent": "KlimarisikoRapport/1.0 (github.com/klister-no/met_report)"
}

FROST_BASE    = "https://frost.met.no/observations/v0.jsonld"
FROST_SOURCES = "https://frost.met.no/sources/v0.jsonld"
FORECAST_BASE = "https://api.met.no/weatherapi/locationforecast/2.0/compact"

# ── Frost API-parametre som hentes ─────────────────────────────────────────────
# Format: Frost elementId → intern nøkkel
FROST_ELEMENTS = {
    "air_temperature":                      "air_temperature",
    "sum(precipitation_amount PT1H)":       "precip_1h",
    "wind_speed":                           "wind_speed",
    "soil_temperature":                     "soil_temperature",
    "min(air_temperature P1D)":             "temp_min_day",
    "max(air_temperature P1D)":             "temp_max_day",
    "mean(air_temperature P1D)":            "temp_mean_day",
    "sum(precipitation_amount P1D)":        "precip_day",
}

# Elementstreng for Frost API-kall (24t observasjoner)
FROST_ELEMENTS_24H = ",".join([
    "air_temperature",
    "sum(precipitation_amount PT1H)",
    "wind_speed",
    "soil_temperature",
])

# ── Regiondefinisjoner ────────────────────────────────────────────────────────
NORWAY_REGIONS = [
    {
        "id":   "ostfold",
        "name": "Østfold",
        "lat":  59.35,
        "lon":  11.10,
        "frost_stations": [
            ("SN17150", "Rygge"),
            ("SN17000", "Sarpsborg/Borregaard"),
            ("SN15270", "Rakkestad"),
            ("SN15420", "Askim"),
            ("SN17280", "Fredrikstad"),
            ("SN17800", "Hvaler"),
        ],
        "focus":        "Grønnsaker og korn — kjerneregion for løk, gulrot, kål",
        "key_products": ["Løk", "Gulrot", "Kål", "Potet", "Korn"],
        "season_start": "mai",
        "gdd_base":     5.0,   # Vekstgraddager fra 5°C (grønnsaker)
    },
    {
        "id":   "vestfold",
        "name": "Vestfold",
        "lat":  59.22,
        "lon":  10.35,
        "frost_stations": [
            ("SN27450", "Torp/Sandefjord"),
            ("SN26840", "Larvik"),
            ("SN27270", "Tønsberg/Sem"),
            ("SN27160", "Andebu"),
            ("SN27500", "Færder"),
        ],
        "focus":        "Jordbær, grønnsaker, potet — 22% av landets grønnsakareal",
        "key_products": ["Jordbær", "Gulrot", "Løk", "Potet", "Kål"],
        "season_start": "mai",
        "gdd_base":     5.0,
    },
    {
        "id":   "lier_drammen",
        "name": "Lier / Drammen",
        "lat":  59.80,
        "lon":  10.25,
        "frost_stations": [
            ("SN23160", "Lier"),
            ("SN23420", "Drammen"),
            ("SN23200", "Tranby"),
            ("SN27120", "Asker"),
        ],
        "focus":        "Bær og frukt — Lierbygda er Norges fremste jordbærområde",
        "key_products": ["Jordbær", "Bringebær", "Epler", "Plommer", "Moreller"],
        "season_start": "mai",
        "gdd_base":     5.0,
    },
    {
        "id":   "hedmark_innlandet",
        "name": "Hedmark / Innlandet",
        "lat":  60.70,
        "lon":  11.50,
        "frost_stations": [
            ("SN12550", "Kongsvinger"),
            ("SN11500", "Hamar"),
            ("SN12680", "Elverum"),
            ("SN13420", "Alvdal"),
        ],
        "focus":        "Potet, gulrot, hodekål — Stange og Kongsvinger-flatbygdene",
        "key_products": ["Potet", "Gulrot", "Hodekål", "Korn", "Jordbær"],
        "season_start": "juni",
        "gdd_base":     5.0,
    },
    {
        "id":   "oppland_innlandet",
        "name": "Oppland / Innlandet",
        "lat":  61.10,
        "lon":  10.40,
        "frost_stations": [
            ("SN10380", "Lillehammer"),
            ("SN10550", "Gjøvik"),
            ("SN10800", "Fåvang"),
            ("SN11060", "Vågå"),
        ],
        "focus":        "Potet og grønnsaker i dalstrøkene — kjølig klima, sen sesong",
        "key_products": ["Potet", "Gulrot", "Kålrot", "Hodekål"],
        "season_start": "juni",
        "gdd_base":     5.0,
    },
    {
        "id":   "frosta_trondelag",
        "name": "Frosta / Trøndelag",
        "lat":  63.58,
        "lon":  10.77,
        "frost_stations": [
            ("SN69100", "Frosta"),
            ("SN68860", "Levanger"),
            ("SN68230", "Trondheim/Voll"),
            ("SN68500", "Verdal"),
            ("SN71990", "Ørland"),
        ],
        "focus":        "Frosta: kjerneregion for løk, potet og grønnsaker i Trøndelag",
        "key_products": ["Løk", "Potet", "Gulrot", "Jordbær", "Purre"],
        "season_start": "juni",
        "gdd_base":     5.0,
    },
    {
        "id":   "rogaland_jaeren",
        "name": "Rogaland / Jæren",
        "lat":  58.97,
        "lon":  5.73,
        "frost_stations": [
            ("SN44560", "Sola/Stavanger"),
            ("SN44640", "Stavanger"),
            ("SN44780", "Bryne"),
            ("SN44630", "Klepp"),
            ("SN44820", "Nærbø"),
            ("SN44300", "Randaberg"),
        ],
        "focus":        "Tidligst i Norge — mild kystjord, tidlig potet fra april",
        "key_products": ["Tidligpotet", "Gulrot", "Purre", "Blomkål", "Brokkoli"],
        "season_start": "april",
        "gdd_base":     5.0,
    },
]

# ── Klimanormaler WMO 1991–2020 (månedlig gjennomsnittstemperatur °C) ──────────
CLIMATE_NORMALS = {
    "ostfold":           {1:-3.5, 2:-3.0, 3:1.0,  4:6.5,  5:12.0, 6:16.5, 7:19.0, 8:17.5, 9:12.0, 10:6.5,  11:1.5,  12:-2.0},
    "vestfold":          {1:-2.5, 2:-2.0, 3:1.5,  4:6.5,  5:12.0, 6:16.0, 7:18.5, 8:17.5, 9:12.5, 10:7.0,  11:2.0,  12:-1.5},
    "lier_drammen":      {1:-3.5, 2:-3.0, 3:1.0,  4:6.5,  5:12.0, 6:16.0, 7:18.5, 8:17.5, 9:12.0, 10:6.5,  11:1.5,  12:-2.0},
    "hedmark_innlandet": {1:-7.0, 2:-6.0, 3:-1.5, 4:5.0,  5:11.5, 6:15.5, 7:17.5, 8:16.5, 9:10.5, 10:5.0,  11:-1.0, 12:-5.0},
    "oppland_innlandet": {1:-7.5, 2:-6.5, 3:-2.0, 4:4.5,  5:11.0, 6:15.0, 7:17.0, 8:16.0, 9:10.0, 10:4.5,  11:-1.5, 12:-5.5},
    "frosta_trondelag":  {1:-3.5, 2:-3.0, 3:0.5,  4:5.5,  5:11.0, 6:14.5, 7:16.5, 8:16.0, 9:11.0, 10:6.0,  11:1.5,  12:-1.5},
    "rogaland_jaeren":   {1:1.5,  2:1.5,  3:4.0,  4:7.5,  5:12.0, 6:15.0, 7:17.0, 8:17.0, 9:13.5, 10:9.5,  11:5.5,  12:2.5},
}

# Nedbørsnormaler februar (mm/mnd) WMO 1991–2020
PRECIP_NORMALS = {
    "ostfold":           {1:55,  2:40,  3:45,  4:45,  5:55,  6:65,  7:70,  8:75,  9:70,  10:75,  11:70,  12:60},
    "vestfold":          {1:60,  2:45,  3:50,  4:45,  5:55,  6:65,  7:70,  8:75,  9:70,  10:80,  11:75,  12:65},
    "lier_drammen":      {1:65,  2:50,  3:55,  4:50,  5:60,  6:70,  7:75,  8:80,  9:75,  10:85,  11:80,  12:70},
    "hedmark_innlandet": {1:35,  2:25,  3:30,  4:35,  5:45,  6:60,  7:75,  8:65,  9:55,  10:50,  11:45,  12:35},
    "oppland_innlandet": {1:40,  2:30,  3:35,  4:40,  5:55,  6:70,  7:85,  8:75,  9:65,  10:60,  11:50,  12:40},
    "frosta_trondelag":  {1:60,  2:45,  3:50,  4:45,  5:50,  6:60,  7:75,  8:70,  9:70,  10:80,  11:75,  12:65},
    "rogaland_jaeren":   {1:115, 2:85,  3:90,  4:70,  5:65,  6:70,  7:85,  8:80,  9:100, 10:120, 11:120, 12:120},
}


# ── Frost API — observasjoner ─────────────────────────────────────────────────

def get_frost_api_key() -> Optional[str]:
    return os.environ.get("FROST_API_KEY")


def fetch_frost_observations(region: dict, api_key: str) -> Optional[dict]:
    """
    Henter siste 24-timers observasjoner fra Frost API.
    Prøver alle stasjoner i prioritert rekkefølge.
    Returnerer sammenstilt observasjon med alle tilgjengelige parametre.
    """
    now_utc  = datetime.now(timezone.utc)
    ref_time = (now_utc - timedelta(hours=36)).strftime("%Y-%m-%dT%H:%M:%SZ")
    end_time = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

    for station_id, station_label in region.get("frost_stations", []):
        try:
            resp = requests.get(
                FROST_BASE,
                params={
                    "sources":         station_id,
                    "referencetime":   f"{ref_time}/{end_time}",
                    "elements":        FROST_ELEMENTS_24H,
                    "timeresolutions": "PT1H",
                },
                auth=(api_key, ""),
                timeout=15,
            )
            if resp.status_code in (404, 400):
                logger.debug(f"Stasjon {station_id} ({station_label}) ikke tilgjengelig")
                continue
            if resp.status_code != 200:
                logger.debug(f"Stasjon {station_id} HTTP {resp.status_code}")
                continue

            obs_list = resp.json().get("data", [])
            if not obs_list:
                continue

            # Parse alle observasjoner
            temps, precips, winds, soils = [], [], [], []
            for obs in obs_list:
                for item in obs.get("observations", []):
                    el  = item.get("elementId", "")
                    val = item.get("value")
                    if val is None:
                        continue
                    try:
                        fval = float(val)
                    except (ValueError, TypeError):
                        continue
                    if el == "air_temperature":
                        temps.append(fval)
                    elif "precipitation_amount" in el:
                        precips.append(fval)
                    elif el == "wind_speed":
                        winds.append(fval)
                    elif el == "soil_temperature":
                        soils.append(fval)

            if not temps:
                continue

            temp_avg = sum(temps) / len(temps)
            gdd_base = region.get("gdd_base", 5.0)
            gdd_today = max(0.0, temp_avg - gdd_base)

            logger.info(f"  {region['name']}: stasjon {station_id} ({station_label}) OK "
                        f"— {len(temps)} tempobs, {len(precips)} nedbørsobs")

            return {
                "temp_now":        round(temps[-1], 1),
                "temp_min_24h":    round(min(temps), 1),
                "temp_max_24h":    round(max(temps), 1),
                "temp_avg_24h":    round(temp_avg, 1),
                "precip_24h":      round(sum(precips), 1),
                "wind_now":        round(winds[-1], 1)      if winds else None,
                "wind_max_24h":    round(max(winds), 1)     if winds else None,
                "soil_temp_now":   round(soils[-1], 1)      if soils else None,
                "frost_hours_24h": sum(1 for t in temps if t <= 0),
                "frost_min_temp":  round(min(temps), 1),
                "gdd_today":       round(gdd_today, 1),
                "station_id":      station_id,
                "station_name":    station_label,
                "source":          "frost_api",
                "obs_count":       len(temps),
            }

        except Exception as e:
            logger.debug(f"Feil for {station_id}: {e}")
            continue

    logger.warning(f"  {region['name']}: ingen Frost-stasjoner svarte — bruker prognose")
    return None


def fetch_frost_climate_normals(region: dict, api_key: str) -> Optional[dict]:
    """
    Henter 30-års nedbørsnormal (1991–2020) for regionen fra Frost API.
    Brukes for å beregne nedbørsavvik mot normalen.
    Caches ikke — kjøres en gang per uke er tilstrekkelig.
    """
    month = datetime.now(timezone.utc).month
    station_id = region["frost_stations"][0][0] if region.get("frost_stations") else None
    if not station_id:
        return None

    try:
        resp = requests.get(
            "https://frost.met.no/climatenormals/v0.jsonld",
            params={
                "sources":   station_id,
                "elements":  "mean(air_temperature P1M),sum(precipitation_amount P1M)",
                "period":    "1991/2020",
            },
            auth=(api_key, ""),
            timeout=15,
        )
        if resp.status_code != 200:
            return None

        normals = {}
        for item in resp.json().get("data", []):
            el  = item.get("elementId", "")
            val = item.get("values", [{}])
            if not val:
                continue
            m = item.get("month")
            if m and "air_temperature" in el:
                normals.setdefault("temp", {})[m] = float(val[0].get("value", 0))
            elif m and "precipitation" in el:
                normals.setdefault("precip", {})[m] = float(val[0].get("value", 0))

        return normals if normals else None

    except Exception as e:
        logger.debug(f"Climate normals feilet for {station_id}: {e}")
        return None


# ── Locationforecast — prognose ───────────────────────────────────────────────

def fetch_forecast(region: dict) -> dict:
    """
    Henter 10-dagersprognose fra met.no Locationforecast 2.0.
    Ingen API-nøkkel nødvendig.
    """
    url = f"{FORECAST_BASE}?lat={region['lat']}&lon={region['lon']}"
    try:
        resp = requests.get(url, headers=HEADERS_METNO, timeout=15)
        resp.raise_for_status()
        return parse_forecast(resp.json(), region)
    except Exception as e:
        logger.warning(f"  Prognose feilet for {region['name']}: {e}")
        return {"forecast_days": [], "symbol": "", "fallback_obs": None}


def parse_forecast(data: dict, region: dict) -> dict:
    """Parser Locationforecast-respons til daglige prognoseverdier."""
    timeseries = data.get("properties", {}).get("timeseries", [])
    if not timeseries:
        return {"forecast_days": [], "symbol": "", "fallback_obs": None}

    now   = datetime.now(timezone.utc)
    month = now.month

    current = timeseries[0]
    instant = current.get("data", {}).get("instant", {}).get("details", {})
    temp_now_fc = instant.get("air_temperature")
    wind_now_fc = instant.get("wind_speed")
    symbol = (current.get("data", {})
                      .get("next_1_hours", {})
                      .get("summary", {})
                      .get("symbol_code", ""))

    # Bygg 24t fallback-observasjon fra prognose
    temps_24h, precips_24h, winds_24h = [], [], []
    for ts in timeseries[:24]:
        t = ts.get("data", {}).get("instant", {}).get("details", {}).get("air_temperature")
        if t is not None:
            temps_24h.append(t)
        w = ts.get("data", {}).get("instant", {}).get("details", {}).get("wind_speed")
        if w is not None:
            winds_24h.append(w)
        p1 = ts.get("data", {}).get("next_1_hours", {}).get("details", {})
        if p1:
            precips_24h.append(p1.get("precipitation_amount", 0))

    fallback_obs = None
    if temps_24h:
        temp_avg = sum(temps_24h) / len(temps_24h)
        gdd_base = region.get("gdd_base", 5.0)
        fallback_obs = {
            "temp_now":        round(temp_now_fc, 1) if temp_now_fc is not None else None,
            "temp_min_24h":    round(min(temps_24h), 1),
            "temp_max_24h":    round(max(temps_24h), 1),
            "temp_avg_24h":    round(temp_avg, 1),
            "precip_24h":      round(sum(precips_24h), 1),
            "wind_now":        round(wind_now_fc, 1) if wind_now_fc is not None else None,
            "wind_max_24h":    round(max(winds_24h), 1) if winds_24h else None,
            "soil_temp_now":   None,
            "frost_hours_24h": sum(1 for t in temps_24h if t <= 0),
            "frost_min_temp":  round(min(temps_24h), 1),
            "gdd_today":       round(max(0.0, temp_avg - gdd_base), 1),
            "station_id":      "locationforecast",
            "station_name":    f"Prognose ({region['lat']}°N, {region['lon']}°E)",
            "source":          "locationforecast",
            "obs_count":       len(temps_24h),
        }

    # 10-dagers dagsprognose
    day_map = {}
    for ts in timeseries:
        t_str = ts.get("time", "")
        try:
            t_dt = datetime.fromisoformat(t_str.replace("Z", "+00:00"))
        except Exception:
            continue
        if t_dt < now:
            continue

        day_key = t_dt.strftime("%Y-%m-%d")
        if day_key not in day_map:
            day_map[day_key] = {"temps": [], "precip": 0.0, "symbols": []}

        t = ts.get("data", {}).get("instant", {}).get("details", {}).get("air_temperature")
        if t is not None:
            day_map[day_key]["temps"].append(t)

        p6 = ts.get("data", {}).get("next_6_hours", {})
        if p6:
            day_map[day_key]["precip"] += p6.get("details", {}).get("precipitation_amount", 0)
            sym = p6.get("summary", {}).get("symbol_code", "")
            if sym:
                day_map[day_key]["symbols"].append(sym)

    forecast_days = []
    gdd_cumulative = 0.0
    for day_key in sorted(day_map.keys())[:10]:
        d = day_map[day_key]
        if not d["temps"]:
            continue
        dt         = datetime.strptime(day_key, "%Y-%m-%d")
        day_normal = CLIMATE_NORMALS.get(region["id"], {}).get(dt.month, 5.0)
        day_avg    = sum(d["temps"]) / len(d["temps"])
        gdd_day    = max(0.0, day_avg - region.get("gdd_base", 5.0))
        gdd_cumulative += gdd_day
        forecast_days.append({
            "date":          day_key,
            "date_display":  dt.strftime("%-d. %b"),
            "temp_min":      round(min(d["temps"]), 1),
            "temp_max":      round(max(d["temps"]), 1),
            "temp_avg":      round(day_avg, 1),
            "temp_avvik":    round(day_avg - day_normal, 1),
            "precip":        round(d["precip"], 1),
            "has_frost":     min(d["temps"]) <= 0,
            "symbol":        d["symbols"][0] if d["symbols"] else "",
            "gdd_day":       round(gdd_day, 1),
            "gdd_cumulative": round(gdd_cumulative, 1),
        })

    return {
        "forecast_days": forecast_days,
        "symbol":        symbol,
        "fallback_obs":  fallback_obs,
    }


# ── Risikoanalyse ─────────────────────────────────────────────────────────────

def compute_growing_degree_days(forecast_days: list, gdd_base: float = 5.0) -> dict:
    """
    Beregner akkumulerte vekstgraddager fra prognosedata.
    Brukes i sesongmodellen for å estimere tidspunkt for sådato, spiring, høsting.
    """
    gdd_10d   = sum(d.get("gdd_day", 0) for d in forecast_days)
    frost_days = sum(1 for d in forecast_days if d.get("has_frost"))
    return {
        "gdd_10d_forecast":  round(gdd_10d, 1),
        "frost_days_10d":    frost_days,
        "gdd_base":          gdd_base,
    }


def assess_production_risk(obs: dict, forecast_days: list, month: int,
                            region: dict) -> dict:
    """
    Vurderer produksjonsrisiko basert på:
    - Temperaturavvik vs. klimanormal
    - Frost i vekstsesong
    - Nedbørsintensitet (løktørking, jordmetning)
    - Vindstyrke (løktørking, pollinering)
    - Jordtemperatur (spiring)
    """
    risks = {}

    # Frostrisiko (kritisk april–juni)
    frost_days_ahead = sum(1 for d in forecast_days[:10] if d.get("has_frost"))
    if month in [4, 5, 6] and frost_days_ahead >= 3:
        risks["frost"] = {"level": "Høy",     "detail": f"{frost_days_ahead} frostdøgn neste 10 dager"}
    elif month in [4, 5, 6] and frost_days_ahead >= 1:
        risks["frost"] = {"level": "Moderat", "detail": f"{frost_days_ahead} frostdøgn neste 10 dager"}
    else:
        risks["frost"] = {"level": "Lav",     "detail": f"{frost_days_ahead} frostdøgn neste 10 dager"}

    # Nedbørsrisiko
    precip = obs.get("precip_24h", 0) or 0
    month_normal = PRECIP_NORMALS.get(region["id"], {}).get(month, 60)
    daily_normal = month_normal / 30
    if precip > daily_normal * 5:
        risks["precip"] = {"level": "Høy",     "detail": f"{precip:.1f} mm/dag — kraftig overnedbør"}
    elif precip > daily_normal * 2:
        risks["precip"] = {"level": "Moderat", "detail": f"{precip:.1f} mm/dag — over normalt"}
    else:
        risks["precip"] = {"level": "Lav",     "detail": f"{precip:.1f} mm/dag"}

    # Vindrisiko (relevant for løktørking og pollinering)
    wind_max = obs.get("wind_max_24h")
    if wind_max is not None:
        if wind_max > 15:
            risks["wind"] = {"level": "Høy",     "detail": f"Maks {wind_max} m/s — skadepotensiale"}
        elif wind_max > 8:
            risks["wind"] = {"level": "Moderat", "detail": f"Maks {wind_max} m/s — tørkende"}
        else:
            risks["wind"] = {"level": "Lav",     "detail": f"Maks {wind_max} m/s"}
    else:
        risks["wind"] = {"level": "Ukjent", "detail": "Ingen vinddata"}

    # Jordtemperatur (spiringsterskel: >5°C for de fleste grønnsaker, >8°C for maize)
    soil_temp = obs.get("soil_temp_now")
    if soil_temp is not None:
        if month in [4, 5] and soil_temp < 5:
            risks["soil"] = {"level": "Høy",     "detail": f"Jordtemp {soil_temp}°C — under spiringsterskel"}
        elif month in [4, 5] and soil_temp < 8:
            risks["soil"] = {"level": "Moderat", "detail": f"Jordtemp {soil_temp}°C — marginal for spiring"}
        else:
            risks["soil"] = {"level": "Lav",     "detail": f"Jordtemp {soil_temp}°C"}
    else:
        risks["soil"] = {"level": "Ukjent", "detail": "Ingen jordtemperaturdata"}

    return risks


def assess_season(temp_avg, temp_normal, month, temp_min, region):
    if temp_avg is None:
        return {"label": "Ukjent", "color": "gray", "emoji": "❓", "detail": "Manglende data"}
    avvik = temp_avg - temp_normal

    if month in [12, 1, 2, 3]:
        if temp_min is not None and temp_min < -15:
            return {"label": "Ekstremfrost", "color": "red",    "emoji": "🥶",
                    "detail": f"Ekstrem kulde {temp_min:.1f}°C — risiko for frostskade"}
        if temp_min is not None and temp_min < -10:
            return {"label": "Sterk frost",  "color": "orange", "emoji": "❄️",
                    "detail": f"Sterk frost {temp_min:.1f}°C — sjekk frostbeskyttelse"}
        label = ("Vinter – normal" if abs(avvik) < 2
                 else ("Vinter – mild" if avvik > 2 else "Vinter – kald"))
        return {"label": label, "color": "blue",
                "emoji": "🌨️" if avvik <= 0 else "🌦️",
                "detail": f"Avvik fra normal: {avvik:+.1f}°C. Sesong ikke påbegynt."}

    if month in [4, 5]:
        if avvik > 2:
            return {"label": "Tidlig vår",  "color": "green",  "emoji": "🌱",
                    "detail": f"{avvik:+.1f}°C over normal — tidlig sesongstart"}
        if avvik < -2:
            return {"label": "Sen vår",     "color": "orange", "emoji": "🐌",
                    "detail": f"{avvik:+.1f}°C under normal — forsinket sesong"}
        return {"label": "Normal vår",  "color": "green",  "emoji": "🌿",
                "detail": f"Avvik {avvik:+.1f}°C — normal sesongprogresjon"}

    if month in [6, 7, 8]:
        if avvik > 2:
            return {"label": "Varm sommer", "color": "green",  "emoji": "☀️",
                    "detail": f"{avvik:+.1f}°C over normal — god sukkeroppbygging"}
        if avvik < -2:
            return {"label": "Kjølig sommer","color": "orange","emoji": "🌥️",
                    "detail": f"{avvik:+.1f}°C under normal — forsinket modning"}
        return {"label": "Normal sommer","color": "green",  "emoji": "🌤️",
                "detail": f"Avvik {avvik:+.1f}°C — normal modning"}

    if month in [9, 10, 11]:
        if temp_min is not None and temp_min < 0:
            return {"label": "Høstfrost",   "color": "orange", "emoji": "🍂",
                    "detail": f"Nattefrost {temp_min:.1f}°C — risiko for sene kulturer"}
        if avvik > 2:
            return {"label": "Varm høst",   "color": "green",  "emoji": "🍁",
                    "detail": f"{avvik:+.1f}°C over normal — forlenget sesong"}
        return {"label": "Normal høst",  "color": "blue",   "emoji": "🍂",
                "detail": f"Avvik {avvik:+.1f}°C"}

    return {"label": "Normal", "color": "gray", "emoji": "🌡️",
            "detail": f"Avvik: {avvik:+.1f}°C"}


def assess_frost_risk(temp_min_24h, forecast_days, month):
    frost_days = sum(1 for d in forecast_days[:10] if d.get("has_frost"))
    next_frost = next((d["date_display"] for d in forecast_days[:10] if d.get("has_frost")), None)

    if month in [4, 5, 6]:
        if frost_days >= 3:
            return {"level": "Høy",    "color": "red",    "emoji": "🥶",
                    "detail": f"{frost_days} frostdøgn neste 10 dager. Neste: {next_frost}. Kritisk for vekstsesong."}
        if frost_days >= 1:
            return {"level": "Moderat","color": "orange", "emoji": "❄️",
                    "detail": f"{frost_days} frostdøgn neste 10 dager. Neste: {next_frost}."}
        return {"level": "Lav",    "color": "green",  "emoji": "✅",
                "detail": "Ingen forventet frost neste 10 dager."}

    if month in [9, 10]:
        if frost_days >= 2:
            return {"level": "Moderat","color": "orange", "emoji": "❄️",
                    "detail": f"{frost_days} frostdøgn neste 10 dager — vurder høsting av sårbare kulturer."}
        return {"level": "Lav",    "color": "green",  "emoji": "✅",
                "detail": f"{frost_days} frostdøgn neste 10 dager."}

    return {"level": "Normal vinter","color": "blue", "emoji": "❄️",
            "detail": f"{frost_days} frostdøgn neste 10 dager."}


def temp_color(avvik):
    if avvik is None:  return "#888"
    if avvik >= 2:     return "#e74c3c"
    if avvik >= 0.5:   return "#e67e22"
    if avvik <= -2:    return "#2980b9"
    if avvik <= -0.5:  return "#5dade2"
    return "#27ae60"


# ── Hovedfunksjon ─────────────────────────────────────────────────────────────

def fetch_all_norway_regions() -> list:
    """
    Henter data for alle 7 norske produksjonsregioner.

    For hver region:
      1. Frost API → faktiske stasjonsobs (temp, nedbør, vind, jordtemp)
         Prøver stasjonene i prioritert rekkefølge.
      2. Locationforecast → 10-dagersprognose + vekstgraddager
      3. Fallback til Locationforecast hvis Frost feiler

    Returnerer liste med én dict per region.
    """
    api_key = get_frost_api_key()
    if api_key:
        logger.info("Frost API-nøkkel funnet — henter stasjonsobs")
    else:
        logger.warning("Ingen FROST_API_KEY i miljøet — kun prognosedata")

    now   = datetime.now(timezone.utc)
    month = now.month
    results = []

    for region in NORWAY_REGIONS:
        logger.info(f"Henter {region['name']}...")

        # 1. Frost API-observasjoner
        obs = None
        if api_key:
            obs = fetch_frost_observations(region, api_key)

        # 2. Prognose (alltid)
        fc = fetch_forecast(region)

        # 3. Fallback
        if obs is None:
            obs = fc.get("fallback_obs") or {}

        # Klimanormal og avvik
        normal_temp = CLIMATE_NORMALS.get(region["id"], {}).get(month, 5.0)
        temp_avg    = obs.get("temp_avg_24h")
        temp_min    = obs.get("temp_min_24h")
        temp_avvik  = round(temp_avg - normal_temp, 1) if temp_avg is not None else None

        # Nedbørsavvik
        precip_normal_month = PRECIP_NORMALS.get(region["id"], {}).get(month, 60)
        precip_normal_day   = round(precip_normal_month / 30, 1)
        precip_24h          = obs.get("precip_24h")
        precip_avvik_pct    = (
            round((precip_24h - precip_normal_day) / precip_normal_day * 100, 0)
            if precip_24h is not None and precip_normal_day > 0
            else None
        )

        # Vekstgraddager
        gdd_info = compute_growing_degree_days(
            fc.get("forecast_days", []), region.get("gdd_base", 5.0)
        )

        # Sesong- og risikovurdering
        season_status    = assess_season(temp_avg, normal_temp, month, temp_min, region)
        frost_risk       = assess_frost_risk(temp_min, fc.get("forecast_days", []), month)
        production_risks = assess_production_risk(obs, fc.get("forecast_days", []), month, region)

        results.append({
            # Metadata
            "region":             region,
            "data_source":        obs.get("source", "ukjent"),
            "station_id":         obs.get("station_id", "—"),
            "station_name":       obs.get("station_name", "—"),

            # Temperatur
            "temp_now":           obs.get("temp_now"),
            "temp_min_24h":       obs.get("temp_min_24h"),
            "temp_max_24h":       obs.get("temp_max_24h"),
            "temp_avg_24h":       temp_avg,
            "temp_normal":        normal_temp,
            "temp_avvik":         temp_avvik,

            # Nedbør
            "precip_24h":         precip_24h,
            "precip_normal_day":  precip_normal_day,
            "precip_avvik_pct":   precip_avvik_pct,

            # Vind
            "wind_now":           obs.get("wind_now"),
            "wind_max_24h":       obs.get("wind_max_24h"),

            # Jord og frost
            "soil_temp_now":      obs.get("soil_temp_now"),
            "frost_hours_24h":    obs.get("frost_hours_24h", 0),
            "frost_min_temp":     obs.get("frost_min_temp"),

            # Vekstgraddager
            "gdd_today":          obs.get("gdd_today"),
            "gdd_10d_forecast":   gdd_info["gdd_10d_forecast"],
            "frost_days_10d":     gdd_info["frost_days_10d"],

            # Værsymbol
            "symbol":             fc.get("symbol", ""),

            # Analyse
            "season_status":      season_status,
            "frost_risk":         frost_risk,
            "production_risks":   production_risks,

            # Prognose
            "forecast_days":      fc.get("forecast_days", []),
        })

    return results


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
    )
    results = fetch_all_norway_regions()
    print()
    print(f"{'Region':<22} {'Stasjon':<26} {'Kilde':<14} "
          f"{'Temp':>6} {'Avvik':>7} {'Nedbør':>8} {'Vind':>7} "
          f"{'GDD':>6} {'Frost':>12}")
    print("─" * 110)
    for r in results:
        name    = r["region"]["name"][:20]
        station = r.get("station_name", "—")[:24]
        source  = r.get("data_source", "—")[:12]
        temp    = f"{r['temp_now']}°C"          if r["temp_now"]      is not None else "N/A"
        avvik   = f"{r['temp_avvik']:+.1f}°C"   if r["temp_avvik"]    is not None else "N/A"
        precip  = f"{r['precip_24h']} mm"        if r["precip_24h"]    is not None else "N/A"
        wind    = f"{r['wind_now']} m/s"          if r["wind_now"]      is not None else "N/A"
        gdd     = f"{r['gdd_today']} GDD"         if r["gdd_today"]     is not None else "N/A"
        frost   = r.get("frost_risk", {}).get("level", "—")
        print(f"{name:<22} {station:<26} {source:<14} "
              f"{temp:>6} {avvik:>7} {precip:>8} {wind:>7} {gdd:>6} {frost:>12}")
