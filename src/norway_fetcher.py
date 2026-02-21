"""
norway_fetcher.py
Henter værdata fra met.no Locationforecast 2.0 API for norske frukt/grønt-regioner.
Ingen API-nøkkel nødvendig. Krever User-Agent header med kontaktinfo.
"""

import requests
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# Met.no krever en identifiserende User-Agent
HEADERS = {
    "User-Agent": "KlimarisikoRapport/1.0 (github.com/klister-no/met_report)"
}

# Norske produksjonsregioner med representative koordinater
# Kilde: Statsforvalteren sesongkalender, SSB hagebruksavlinger
NORWAY_REGIONS = [
    {
        "id": "vestfold",
        "name": "Vestfold",
        "lat": 59.22,
        "lon": 10.35,
        "focus": "Grønnsaker, bær, potet (22% av landets grønnsakareal)",
        "key_products": ["Jordbær", "Gulrot", "Løk", "Potet", "Kål"],
        "season_start": "mai",
    },
    {
        "id": "akershus_ostfold",
        "name": "Akershus / Østfold",
        "lat": 59.57,
        "lon": 10.91,
        "focus": "Grønnsaker og potet — Østlandet 75% av alt grønnsakareal",
        "key_products": ["Gulrot", "Løk", "Potet", "Kålrot", "Korn"],
        "season_start": "mai",
    },
    {
        "id": "hedmark_innlandet",
        "name": "Hedmark / Innlandet",
        "lat": 60.79,
        "lon": 11.07,
        "focus": "Potet, gulrot, kål — store flatbygder",
        "key_products": ["Potet", "Gulrot", "Hodekål", "Korn"],
        "season_start": "juni",
    },
    {
        "id": "buskerud",
        "name": "Buskerud",
        "lat": 59.75,
        "lon": 10.20,
        "focus": "Bær, frukt, Numedal/Ringerike",
        "key_products": ["Jordbær", "Bringebær", "Epler", "Plommer"],
        "season_start": "juni",
    },
    {
        "id": "rogaland",
        "name": "Rogaland",
        "lat": 58.97,
        "lon": 5.73,
        "focus": "Tidlig potet og grønnsaker — varm kystjord",
        "key_products": ["Tidligpotet", "Gulrot", "Purre", "Blomkål"],
        "season_start": "april",
    },
    {
        "id": "hardanger",
        "name": "Hardanger",
        "lat": 60.37,
        "lon": 6.53,
        "focus": "40% av all norsk frukt — eple, plomme, kirsebær",
        "key_products": ["Epler", "Plommer", "Kirsebær", "Pærer"],
        "season_start": "juli",
    },
    {
        "id": "sogn",
        "name": "Sogn",
        "lat": 61.22,
        "lon": 7.10,
        "focus": "Fjordfrukt — epler, bær, tidlig grønt",
        "key_products": ["Epler", "Jordbær", "Bringebær", "Plommer", "Kirsebær"],
        "season_start": "juli",
    },
    {
        "id": "frosta_trondelag",
        "name": "Frosta / Trøndelag",
        "lat": 63.58,
        "lon": 10.77,
        "focus": "Frosta: viktig knutepunkt for potet og grønnsaker — kystklima",
        "key_products": ["Potet", "Gulrot", "Løk", "Jordbær"],
        "season_start": "juni",
    },
]

# Klimanormaler (WMO 1991-2020) — månedlig gjennomsnittstemperatur °C
# Kilde: met.no klimaatlas / seNorge
CLIMATE_NORMALS = {
    "vestfold":           {1:-2.5, 2:-2.0, 3:1.5,  4:6.5,  5:12.0, 6:16.0, 7:18.5, 8:17.5, 9:12.5, 10:7.0, 11:2.0, 12:-1.5},
    "akershus_ostfold":   {1:-3.5, 2:-3.0, 3:1.0,  4:6.5,  5:12.0, 6:16.5, 7:19.0, 8:17.5, 9:12.0, 10:6.5, 11:1.5, 12:-2.0},
    "hedmark_innlandet":  {1:-7.0, 2:-6.0, 3:-1.5, 4:5.0,  5:11.5, 6:15.5, 7:17.5, 8:16.5, 9:10.5, 10:5.0, 11:-1.0, 12:-5.0},
    "buskerud":           {1:-4.0, 2:-3.5, 3:0.5,  4:6.0,  5:11.5, 6:15.5, 7:18.0, 8:17.0, 9:11.5, 10:6.0, 11:1.0, 12:-2.5},
    "rogaland":           {1:1.5,  2:1.5,  3:4.0,  4:7.5,  5:12.0, 6:15.0, 7:17.0, 8:17.0, 9:13.5, 10:9.5, 11:5.5, 12:2.5},
    "hardanger":          {1:-1.0, 2:-0.5, 3:2.5,  4:7.0,  5:12.5, 6:16.0, 7:18.0, 8:17.5, 9:13.0, 10:8.0, 11:3.5, 12:0.5},
    "sogn":               {1:-1.5, 2:-1.0, 3:2.0,  4:7.0,  5:12.5, 6:15.5, 7:17.5, 8:17.0, 9:12.5, 10:7.5, 11:3.0, 12:0.0},
    "frosta_trondelag":   {1:-3.5, 2:-3.0, 3:0.5,  4:5.5,  5:11.0, 6:14.5, 7:16.5, 8:16.0, 9:11.0, 10:6.0, 11:1.5, 12:-1.5},
}

# Nedbørsnormaler februar (mm/mnd) for frostrisiko-vurdering
PRECIP_NORMALS_FEB = {
    "vestfold": 45, "akershus_ostfold": 40, "hedmark_innlandet": 35,
    "buskerud": 50, "rogaland": 95, "hardanger": 130,
    "sogn": 110, "frosta_trondelag": 55,
}


def fetch_region(region: dict) -> Optional[dict]:
    """Henter værdata fra met.no for én region."""
    url = (
        f"https://api.met.no/weatherapi/locationforecast/2.0/compact"
        f"?lat={region['lat']}&lon={region['lon']}"
    )
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        return parse_forecast(data, region)
    except Exception as e:
        logger.warning(f"Feil ved henting av {region['name']}: {e}")
        return None


def parse_forecast(data: dict, region: dict) -> dict:
    """Parser met.no-responsen og beregner relevante nøkkeltall."""
    timeseries = data.get("properties", {}).get("timeseries", [])
    if not timeseries:
        return {}

    now = datetime.now(timezone.utc)
    month = now.month

    # Hent klimanormal for denne måneden
    normal_temp = CLIMATE_NORMALS.get(region["id"], {}).get(month, 5.0)

    # --- Nåværende forhold (første tidssteg) ---
    current = timeseries[0]
    instant = current.get("data", {}).get("instant", {}).get("details", {})
    temp_now = instant.get("air_temperature", None)
    wind_now = instant.get("wind_speed", None)
    humidity_now = instant.get("relative_humidity", None)

    # Symbolkode for neste time
    next1h = current.get("data", {}).get("next_1_hours", {})
    symbol = next1h.get("summary", {}).get("symbol_code", "")
    precip_1h = next1h.get("details", {}).get("precipitation_amount", 0)

    # --- 24-timers akkumulert nedbør og min/maks temp ---
    temps_24h = []
    precip_24h = 0.0
    frost_hours = 0

    for ts in timeseries[:24]:
        d = ts.get("data", {})
        t = d.get("instant", {}).get("details", {}).get("air_temperature")
        if t is not None:
            temps_24h.append(t)
            if t <= 0:
                frost_hours += 1
        p = d.get("next_1_hours", {}).get("details", {})
        if p:
            precip_24h += p.get("precipitation_amount", 0)

    temp_min_24h = min(temps_24h) if temps_24h else None
    temp_max_24h = max(temps_24h) if temps_24h else None
    temp_avg_24h = round(sum(temps_24h) / len(temps_24h), 1) if temps_24h else None

    # --- 10-dagers prognose (daglige min/maks) ---
    forecast_days = []
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
        temp = ts.get("data", {}).get("instant", {}).get("details", {}).get("air_temperature")

        if day_key not in day_map:
            day_map[day_key] = {"temps": [], "precip": 0.0, "symbols": []}

        if temp is not None:
            day_map[day_key]["temps"].append(temp)

        # Prøv next_6_hours for daglig nedbør
        p6 = ts.get("data", {}).get("next_6_hours", {}).get("details", {})
        if p6:
            day_map[day_key]["precip"] += p6.get("precipitation_amount", 0)
            sym6 = ts.get("data", {}).get("next_6_hours", {}).get("summary", {}).get("symbol_code", "")
            if sym6:
                day_map[day_key]["symbols"].append(sym6)

    for day_key in sorted(day_map.keys())[:10]:
        d = day_map[day_key]
        if d["temps"]:
            dt = datetime.strptime(day_key, "%Y-%m-%d")
            day_normal = CLIMATE_NORMALS.get(region["id"], {}).get(dt.month, 5.0)
            day_avg = sum(d["temps"]) / len(d["temps"])
            forecast_days.append({
                "date": day_key,
                "date_display": dt.strftime("%-d. %b"),
                "temp_min": round(min(d["temps"]), 1),
                "temp_max": round(max(d["temps"]), 1),
                "temp_avg": round(day_avg, 1),
                "temp_avvik": round(day_avg - day_normal, 1),
                "precip": round(d["precip"], 1),
                "has_frost": min(d["temps"]) <= 0,
                "symbol": d["symbols"][0] if d["symbols"] else "",
            })

    # --- Temperaturavvik fra normal ---
    temp_avvik = round(temp_avg_24h - normal_temp, 1) if temp_avg_24h is not None else None

    # --- Vekstsesongstatus ---
    season_status = assess_season(temp_avg_24h, normal_temp, month, temp_min_24h, region)

    # --- Frostrisiko ---
    frost_risk = assess_frost_risk(temp_min_24h, forecast_days, month)

    return {
        "region": region,
        "temp_now": round(temp_now, 1) if temp_now is not None else None,
        "temp_min_24h": round(temp_min_24h, 1) if temp_min_24h is not None else None,
        "temp_max_24h": round(temp_max_24h, 1) if temp_max_24h is not None else None,
        "temp_avg_24h": temp_avg_24h,
        "temp_normal": normal_temp,
        "temp_avvik": temp_avvik,
        "precip_24h": round(precip_24h, 1),
        "wind_now": round(wind_now, 1) if wind_now is not None else None,
        "frost_hours_24h": frost_hours,
        "symbol": symbol,
        "season_status": season_status,
        "frost_risk": frost_risk,
        "forecast_days": forecast_days,
    }


def assess_season(temp_avg, temp_normal, month, temp_min, region):
    """Vurderer vekstsesongstatus basert på temperatur og tid på året."""
    if temp_avg is None:
        return {"label": "Ukjent", "color": "gray", "emoji": "❓", "detail": "Manglende data"}

    avvik = temp_avg - temp_normal

    # Vintermåneder — frostovervåking viktigst
    if month in [12, 1, 2, 3]:
        if temp_min is not None and temp_min < -15:
            return {"label": "Ekstremfrost", "color": "red", "emoji": "🥶",
                    "detail": f"Ekstrem kulde {temp_min:.1f}°C — risiko for frostskade på lagret frukt/grønt"}
        elif temp_min is not None and temp_min < -10:
            return {"label": "Sterk frost", "color": "orange", "emoji": "❄️",
                    "detail": f"Sterk frost {temp_min:.1f}°C — sjekk frostbeskyttelse"}
        else:
            label = "Vinter – normal" if abs(avvik) < 2 else ("Vinter – mild" if avvik > 2 else "Vinter – kald")
            emoji = "🌨️" if avvik <= 0 else "🌦️"
            return {"label": label, "color": "blue", "emoji": emoji,
                    "detail": f"Avvik fra normal: {avvik:+.1f}°C. Produksjonssesong ikke påbegynt."}

    # Vår — avgjørende for sesongstart
    if month in [4, 5]:
        if avvik > 2:
            return {"label": "Tidlig vår", "color": "green", "emoji": "🌱",
                    "detail": f"{avvik:+.1f}°C over normal — sesong estimert tidlig, gunstig for tidligproduksjon"}
        elif avvik < -2:
            return {"label": "Sen vår", "color": "orange", "emoji": "🐌",
                    "detail": f"{avvik:+.1f}°C under normal — forsinket sesongstart, press på leveranseplan"}
        else:
            return {"label": "Normal vår", "color": "green", "emoji": "🌿",
                    "detail": f"Avvik {avvik:+.1f}°C — normal sesongprogresjon"}

    # Sommer — produksjonssesong
    if month in [6, 7, 8]:
        if avvik > 2:
            return {"label": "Varm sommer", "color": "green", "emoji": "☀️",
                    "detail": f"{avvik:+.1f}°C over normal — god sukkeroppbygging i frukt, høy etterspørsel"}
        elif avvik < -2:
            return {"label": "Kjølig sommer", "color": "orange", "emoji": "🌥️",
                    "detail": f"{avvik:+.1f}°C under normal — redusert sukkerinnhold, forsinket modning"}
        else:
            return {"label": "Normal sommer", "color": "green", "emoji": "🌤️",
                    "detail": f"Avvik {avvik:+.1f}°C — normal modning og produksjon"}

    # Høst — høsting og lagring
    if month in [9, 10, 11]:
        if temp_min is not None and temp_min < 0:
            return {"label": "Tidlig høstfrost", "color": "orange", "emoji": "🍂",
                    "detail": f"Nattefrost {temp_min:.1f}°C — risiko for frostskade på seine kulturer"}
        elif avvik > 2:
            return {"label": "Varm høst", "color": "green", "emoji": "🍁",
                    "detail": f"{avvik:+.1f}°C over normal — forlenget sesong, god lagerkvalitet"}
        else:
            return {"label": "Normal høst", "color": "blue", "emoji": "🍂",
                    "detail": f"Avvik {avvik:+.1f}°C — normal avslutning av sesong"}

    return {"label": "Normal", "color": "gray", "emoji": "🌡️", "detail": f"Avvik: {avvik:+.1f}°C"}


def assess_frost_risk(temp_min_24h, forecast_days, month):
    """Vurderer frostrisiko neste 10 dager."""
    frost_days_ahead = sum(1 for d in forecast_days[:10] if d.get("has_frost"))
    next_frost_day = None
    for d in forecast_days[:10]:
        if d.get("has_frost"):
            next_frost_day = d["date_display"]
            break

    if month in [4, 5, 6]:
        # Vår/forsommer — frostrisiko er kritisk for vekstsesong
        if frost_days_ahead >= 3:
            return {"level": "Høy", "color": "red", "emoji": "🥶",
                    "detail": f"{frost_days_ahead} frostdøgn neste 10 dager. Neste: {next_frost_day}. Kritisk for vekstsesong."}
        elif frost_days_ahead >= 1:
            return {"level": "Moderat", "color": "orange", "emoji": "❄️",
                    "detail": f"{frost_days_ahead} frostdøgn neste 10 dager. Neste: {next_frost_day}."}
        else:
            return {"level": "Lav", "color": "green", "emoji": "✅",
                    "detail": "Ingen forventet frost neste 10 dager."}
    elif month in [9, 10]:
        # Høst — nattefrost ved høsting
        if frost_days_ahead >= 2:
            return {"level": "Moderat", "color": "orange", "emoji": "❄️",
                    "detail": f"{frost_days_ahead} frostdøgn neste 10 dager — vurder høsting av sårbare kulturer."}
        else:
            return {"level": "Lav", "color": "green", "emoji": "✅",
                    "detail": f"{frost_days_ahead} frostdøgn neste 10 dager."}
    else:
        # Vinter — frost er normalt
        return {"level": "Normal vinter", "color": "blue", "emoji": "❄️",
                "detail": f"{frost_days_ahead} frostdøgn neste 10 dager. Normal vinterstatus."}


def fetch_all_norway_regions() -> list:
    """Henter værdata for alle norske produksjonsregioner."""
    results = []
    for region in NORWAY_REGIONS:
        logger.info(f"Henter data for {region['name']}...")
        data = fetch_region(region)
        if data:
            results.append(data)
        else:
            # Fallback med tomme verdier
            results.append({
                "region": region,
                "temp_now": None,
                "temp_min_24h": None,
                "temp_max_24h": None,
                "temp_avg_24h": None,
                "temp_normal": CLIMATE_NORMALS.get(region["id"], {}).get(datetime.now().month, 5.0),
                "temp_avvik": None,
                "precip_24h": None,
                "wind_now": None,
                "frost_hours_24h": 0,
                "symbol": "",
                "season_status": {"label": "Data utilgjengelig", "color": "gray", "emoji": "❓", "detail": "Kunne ikke hente data fra met.no"},
                "frost_risk": {"level": "Ukjent", "color": "gray", "emoji": "❓", "detail": "Ingen data"},
                "forecast_days": [],
            })
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    results = fetch_all_norway_regions()
    for r in results:
        name = r["region"]["name"]
        t = r.get("temp_now")
        avvik = r.get("temp_avvik")
        frost = r.get("frost_risk", {}).get("level")
        print(f"{name}: {t}°C (avvik: {avvik:+.1f}°C), Frost: {frost}")
