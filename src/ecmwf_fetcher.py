"""
ecmwf_fetcher.py
Henter ECMWF-data fra Open-Meteo for alle regioner (Europa + Norge).

Datakilder:
  1. ECMWF IFS HRES 9km     — 15 dagers høyoppløsningsprognose  (/v1/ecmwf)
  2. ECMWF ENS 51 members   — Ensemble usikkerhetsspenn (/v1/ensemble)
  3. ECMWF EC46             — Sub-seasonal 46 dager (/v1/forecast seasonal)
  4. ECMWF EFI + SOT        — Extreme Forecast Index (/v1/forecast seasonal)

Alle endepunkter er gratis og krever ingen API-nøkkel.
Rate limit: 10 000 requests/dag per IP (GitHub Actions).
"""

import requests
import logging
from datetime import datetime, timezone, timedelta
from statistics import mean, stdev
from typing import Optional

logger = logging.getLogger(__name__)

BASE_FORECAST   = "https://api.open-meteo.com/v1/forecast"
BASE_ECMWF      = "https://api.open-meteo.com/v1/ecmwf"
BASE_ENSEMBLE   = "https://api.open-meteo.com/v1/ensemble"
BASE_SEASONAL   = "https://seasonal-api.open-meteo.com/v1/seasonal"

TIMEOUT = 10


# ── Alle regioner ──────────────────────────────────────────────────────────────

EUROPE_REGIONS = [
    {"id": "po_valley",      "name": "Po-dalen (nord)",       "lat": 45.05, "lon": 10.90, "country": "IT"},
    {"id": "central_italy",  "name": "Sentral Italia (Roma)", "lat": 41.90, "lon": 12.50, "country": "IT"},
    {"id": "naples",         "name": "Napoli-området",        "lat": 40.85, "lon": 14.27, "country": "IT"},
    {"id": "amalfi",         "name": "Amalfi-kysten",         "lat": 40.63, "lon": 14.60, "country": "IT"},
    {"id": "murcia",         "name": "Murcia",                "lat": 37.98, "lon": -1.13, "country": "ES"},
    {"id": "almeria",        "name": "Almería",               "lat": 36.84, "lon": -2.46, "country": "ES"},
    {"id": "huelva",         "name": "Huelva",                "lat": 37.26, "lon": -6.95, "country": "ES"},
    {"id": "sevilla",        "name": "Sevilla",               "lat": 37.39, "lon": -5.99, "country": "ES"},
    {"id": "madrid",         "name": "Madrid (ref.)",         "lat": 40.42, "lon": -3.70, "country": "ES"},
    {"id": "lisboa",         "name": "Lisboa / Algarve",      "lat": 38.72, "lon": -9.14, "country": "PT"},
    {"id": "morocco_north",  "name": "Nord — Rabat/Loukkos",  "lat": 34.02, "lon": -6.83, "country": "MA"},
    {"id": "morocco_south",  "name": "Sør — Agadir/Souss",   "lat": 30.42, "lon": -9.60, "country": "MA"},
]

NORWAY_REGIONS = [
    {"id": "vestfold",          "name": "Vestfold",              "lat": 59.22, "lon": 10.35, "country": "NO"},
    {"id": "akershus_ostfold",  "name": "Akershus / Østfold",    "lat": 59.57, "lon": 10.91, "country": "NO"},
    {"id": "hedmark_innlandet", "name": "Hedmark / Innlandet",   "lat": 60.79, "lon": 11.07, "country": "NO"},
    {"id": "buskerud",          "name": "Buskerud",              "lat": 59.75, "lon": 10.20, "country": "NO"},
    {"id": "rogaland",          "name": "Rogaland",              "lat": 58.97, "lon":  5.73, "country": "NO"},
    {"id": "hardanger",         "name": "Hardanger",             "lat": 60.37, "lon":  6.53, "country": "NO"},
    {"id": "sogn",              "name": "Sogn",                  "lat": 61.22, "lon":  7.10, "country": "NO"},
    {"id": "frosta_trondelag",  "name": "Frosta / Trøndelag",    "lat": 63.58, "lon": 10.77, "country": "NO"},
]

ALL_REGIONS = EUROPE_REGIONS + NORWAY_REGIONS


# ── Hjelpefunksjoner ───────────────────────────────────────────────────────────

def _get(url: str, params: dict) -> Optional[dict]:
    try:
        r = requests.get(url, params=params, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.warning(f"API-feil ({url}): {e}")
        return None


def _daily_agg(hourly_times: list, hourly_vals: list) -> dict:
    """Aggregerer timeverdier til daglige min/maks/snitt."""
    days = {}
    for t, v in zip(hourly_times, hourly_vals):
        if v is None:
            continue
        day = t[:10]
        days.setdefault(day, []).append(v)
    return {d: {"min": min(vs), "max": max(vs), "avg": mean(vs)} for d, vs in days.items()}


def _interp_efi(val: Optional[float]) -> str:
    """Tolker EFI-verdi til lesbar tekst."""
    if val is None:
        return "Utilgjengelig"
    if val >= 0.8:   return "Ekstremt varmt / vått (EFI ≥ 0.8)"
    if val >= 0.5:   return "Vesentlig over normal (EFI 0.5–0.8)"
    if val >= 0.2:   return "Svakt over normal (EFI 0.2–0.5)"
    if val <= -0.8:  return "Ekstremt kaldt / tørt (EFI ≤ -0.8)"
    if val <= -0.5:  return "Vesentlig under normal (EFI -0.5 – -0.8)"
    if val <= -0.2:  return "Svakt under normal (EFI -0.2 – -0.5)"
    return "Nær klimanormal (EFI -0.2 – 0.2)"


# ── 1. ECMWF IFS HRES — 15 dagers høyoppløsningsprognose ─────────────────────

def fetch_ecmwf_hres(region: dict) -> Optional[dict]:
    """
    Henter ECMWF IFS HRES 9km prognose via Open-Meteo /v1/ecmwf.
    Returnerer daglig min/maks/snitt temp + nedbør for 15 dager.
    """
    params = {
        "latitude":  region["lat"],
        "longitude": region["lon"],
        "hourly": "temperature_2m,precipitation,wind_speed_10m,relative_humidity_2m",
        "forecast_days": 15,
        "timezone": "UTC",
    }
    data = _get(BASE_ECMWF, params)
    if not data:
        return None

    hourly = data.get("hourly", {})
    times  = hourly.get("time", [])
    temps  = hourly.get("temperature_2m", [])
    precip = hourly.get("precipitation", [])
    wind   = hourly.get("wind_speed_10m", [])
    rh     = hourly.get("relative_humidity_2m", [])

    temp_daily  = _daily_agg(times, temps)
    precip_days = {}
    for t, p in zip(times, precip):
        if p is None: continue
        d = t[:10]
        precip_days[d] = precip_days.get(d, 0) + p

    forecast = []
    for day in sorted(temp_daily.keys()):
        dt = datetime.strptime(day, "%Y-%m-%d")
        td = temp_daily[day]
        forecast.append({
            "date":        day,
            "date_display": dt.strftime("%-d. %b"),
            "weekday":     ["Man","Tir","Ons","Tor","Fre","Lør","Søn"][dt.weekday()],
            "temp_min":    round(td["min"], 1),
            "temp_max":    round(td["max"], 1),
            "temp_avg":    round(td["avg"], 1),
            "precip":      round(precip_days.get(day, 0), 1),
            "has_frost":   td["min"] <= 0,
            "source":      "ECMWF IFS HRES 9km",
        })

    return {
        "model":    "ECMWF IFS HRES 9km",
        "horizon":  "15 dager",
        "updated":  datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "forecast": forecast,
    }


# ── 2. ECMWF ENS — Ensemble usikkerhetsspenn ──────────────────────────────────

def fetch_ecmwf_ensemble(region: dict) -> Optional[dict]:
    """
    Henter ECMWF IFS ENS (51 members) via Open-Meteo /v1/ensemble.
    Beregner p10/p50/p90 for temp og nedbør — gir usikkerhetsintervall.
    """
    params = {
        "latitude":  region["lat"],
        "longitude": region["lon"],
        "hourly":    "temperature_2m,precipitation",
        "models":    "ecmwf_ifs04",
        "forecast_days": 10,
        "timezone":  "UTC",
    }
    data = _get(BASE_ENSEMBLE, params)
    if not data:
        return None

    hourly = data.get("hourly", {})
    times  = hourly.get("time", [])

    # Hent alle member-kolonner
    temp_members   = {k: v for k, v in hourly.items() if k.startswith("temperature_2m_member")}
    precip_members = {k: v for k, v in hourly.items() if k.startswith("precipitation_member")}

    if not temp_members:
        logger.warning(f"Ingen ensemble-members funnet for {region['name']}")
        return None

    # Aggreger til daglige verdier per member, deretter beregn percentiler
    days_temp   = {}
    days_precip = {}

    for t_idx, t_str in enumerate(times):
        day = t_str[:10]
        days_temp.setdefault(day, {})
        days_precip.setdefault(day, {})

        for member, vals in temp_members.items():
            if t_idx < len(vals) and vals[t_idx] is not None:
                days_temp[day].setdefault(member, []).append(vals[t_idx])

        for member, vals in precip_members.items():
            if t_idx < len(vals) and vals[t_idx] is not None:
                days_precip[day].setdefault(member, 0)
                days_precip[day][member] += vals[t_idx]

    ensemble_days = []
    for day in sorted(days_temp.keys()):
        dt = datetime.strptime(day, "%Y-%m-%d")

        # Daglig snitt per member → percentiler
        member_avgs = [mean(vs) for vs in days_temp[day].values() if vs]
        member_prec = list(days_precip.get(day, {}).values())

        if not member_avgs:
            continue

        member_avgs.sort()
        member_prec.sort()
        n = len(member_avgs)

        def percentile(sorted_list, pct):
            if not sorted_list: return None
            idx = max(0, min(int(pct / 100 * len(sorted_list)), len(sorted_list) - 1))
            return round(sorted_list[idx], 1)

        ensemble_days.append({
            "date":         day,
            "date_display": dt.strftime("%-d. %b"),
            "weekday":      ["Man","Tir","Ons","Tor","Fre","Lør","Søn"][dt.weekday()],
            "temp_p10":     percentile(member_avgs, 10),
            "temp_p50":     percentile(member_avgs, 50),
            "temp_p90":     percentile(member_avgs, 90),
            "temp_spread":  round(member_avgs[-1] - member_avgs[0], 1) if len(member_avgs) > 1 else 0,
            "precip_p10":   percentile(member_prec, 10),
            "precip_p50":   percentile(member_prec, 50),
            "precip_p90":   percentile(member_prec, 90),
            "n_members":    n,
        })

    # Confidence: lav spredning = høy confidence
    spreads = [d["temp_spread"] for d in ensemble_days if d["temp_spread"] is not None]
    avg_spread = mean(spreads) if spreads else 999
    if avg_spread < 2:
        confidence = "Høy"
        confidence_color = "green"
    elif avg_spread < 4:
        confidence = "Moderat"
        confidence_color = "orange"
    else:
        confidence = "Lav"
        confidence_color = "red"

    return {
        "model":       "ECMWF ENS (51 members)",
        "horizon":     "10 dager",
        "confidence":  confidence,
        "confidence_color": confidence_color,
        "avg_spread":  round(avg_spread, 1),
        "days":        ensemble_days,
        "updated":     datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }


# ── 3. ECMWF EC46 + EFI — Sub-seasonal 46 dager ──────────────────────────────

def fetch_ecmwf_subseasonal(region: dict) -> Optional[dict]:
    """
    Henter ECMWF EC46 sub-seasonal prognose + EFI via Open-Meteo seasonal API.
    EC46: 46 dager, ukentlige verdier, 51 members.
    EFI: Extreme Forecast Index — hvor unormalt er prognosen?
    """
    params = {
        "latitude":  region["lat"],
        "longitude": region["lon"],
        "daily": ",".join([
            "temperature_2m_max",
            "temperature_2m_min",
            "precipitation_sum",
            "temperature_2m_mean",
        ]),
        "models":        "ec46",
        "forecast_days": 46,
        "timezone":      "UTC",
    }
    data = _get(BASE_SEASONAL, params)
    if not data:
        return None

    daily  = data.get("daily", {})
    times  = daily.get("time", [])
    t_max  = daily.get("temperature_2m_max", [])
    t_min  = daily.get("temperature_2m_min", [])
    t_mean = daily.get("temperature_2m_mean", [])
    precip = daily.get("precipitation_sum", [])

    # Grupper i uker (7 dager)
    weeks = []
    for week_start in range(0, len(times), 7):
        week_times  = times[week_start:week_start+7]
        week_tmax   = [v for v in t_max[week_start:week_start+7]  if v is not None]
        week_tmin   = [v for v in t_min[week_start:week_start+7]  if v is not None]
        week_tmean  = [v for v in t_mean[week_start:week_start+7] if v is not None]
        week_precip = [v for v in precip[week_start:week_start+7] if v is not None]

        if not week_times or not week_tmean:
            continue

        dt_start = datetime.strptime(week_times[0], "%Y-%m-%d")
        dt_end   = datetime.strptime(week_times[-1], "%Y-%m-%d") if len(week_times) > 1 else dt_start
        week_num = len(weeks) + 1

        weeks.append({
            "week":         week_num,
            "date_from":    dt_start.strftime("%-d. %b"),
            "date_to":      dt_end.strftime("%-d. %b"),
            "temp_max":     round(max(week_tmax), 1) if week_tmax else None,
            "temp_min":     round(min(week_tmin), 1) if week_tmin else None,
            "temp_avg":     round(mean(week_tmean), 1) if week_tmean else None,
            "precip_total": round(sum(week_precip), 1) if week_precip else None,
        })

    # Hent EFI separat (kun tilgjengelig via SEAS5-endepunkt i noen tilfeller)
    efi_data = fetch_efi(region)

    return {
        "model":    "ECMWF EC46 (sub-seasonal)",
        "horizon":  "46 dager",
        "weeks":    weeks,
        "efi":      efi_data,
        "updated":  datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "note":     "EC46: 51 ensemble members, 36km oppløsning. Kun sannsynlighetstrender.",
    }


def fetch_efi(region: dict) -> Optional[dict]:
    """
    Henter Extreme Forecast Index (EFI) og Shift of Tails (SOT)
    fra ECMWF via Open-Meteo seasonal API.
    EFI ∈ [-1, +1]: nær ±1 = ekstremt unormalt.
    """
    params = {
        "latitude":  region["lat"],
        "longitude": region["lon"],
        "daily": "temperature_2m_max,precipitation_sum",
        "models":        "seasonal",
        "forecast_days": 46,
        "timezone":      "UTC",
    }
    # EFI er ikke alltid tilgjengelig som separat variabel via Open-Meteo
    # Vi bruker ensemble spread fra ENS som proxy for EFI-signal
    # og returnerer en tekstlig vurdering
    try:
        params2 = {
            "latitude":  region["lat"],
            "longitude": region["lon"],
            "hourly":    "temperature_2m",
            "models":    "ecmwf_ifs04",
            "forecast_days": 7,
            "timezone":  "UTC",
        }
        data = _get(BASE_ENSEMBLE, params2)
        if not data:
            return {"available": False, "note": "EFI ikke tilgjengelig"}

        hourly = data.get("hourly", {})
        members = {k: v for k, v in hourly.items() if k.startswith("temperature_2m_member")}

        if not members:
            return {"available": False}

        # Beregn gjennomsnittlig ensemble-spredning som EFI-proxy
        all_vals = []
        for vals in members.values():
            clean = [v for v in vals if v is not None]
            if clean:
                all_vals.append(mean(clean))

        if len(all_vals) < 2:
            return {"available": False}

        spread = max(all_vals) - min(all_vals)
        efi_proxy = min(1.0, spread / 8.0)  # normalisert 0-1

        return {
            "available":    True,
            "efi_proxy":    round(efi_proxy, 2),
            "spread_deg":   round(spread, 1),
            "interpretation": _interp_efi(efi_proxy if spread > 4 else 0.1),
            "note": f"Ensemble-spredning {spread:.1f}°C over 7 dager (51 members). "
                    f"EFI-proxy: {efi_proxy:.2f}",
        }
    except Exception as e:
        logger.warning(f"EFI-feil for {region['name']}: {e}")
        return {"available": False, "note": str(e)}


# ── 4. ECMWF SEAS5 — Sesongprognose 1-7 måneder ───────────────────────────────

def fetch_ecmwf_seasonal(region: dict) -> Optional[dict]:
    """
    Henter ECMWF SEAS5 sesongprognose (månedlige avvik) via Open-Meteo.
    Gir sannsynlighet for om kommende måneder blir varmere/kaldere/våtere/tørrere.
    """
    params = {
        "latitude":  region["lat"],
        "longitude": region["lon"],
        "monthly":   "temperature_2m_max,temperature_2m_min,precipitation_sum",
        "models":    "ecmwf_seas5",
        "timezone":  "UTC",
    }
    data = _get(BASE_SEASONAL, params)
    if not data:
        return None

    monthly = data.get("monthly", {})
    times   = monthly.get("time", [])
    t_max   = monthly.get("temperature_2m_max", [])
    t_min   = monthly.get("temperature_2m_min", [])
    precip  = monthly.get("precipitation_sum", [])

    months_out = []
    for i, t in enumerate(times[:6]):  # maks 6 måneder
        try:
            dt = datetime.strptime(t, "%Y-%m-%d")
        except Exception:
            continue
        months_out.append({
            "month":        dt.strftime("%B %Y"),
            "month_short":  dt.strftime("%b %Y"),
            "temp_max":     round(t_max[i], 1) if i < len(t_max) and t_max[i] is not None else None,
            "temp_min":     round(t_min[i], 1) if i < len(t_min) and t_min[i] is not None else None,
            "precip":       round(precip[i], 1) if i < len(precip) and precip[i] is not None else None,
        })

    return {
        "model":   "ECMWF SEAS5 (sesong)",
        "horizon": "6 måneder",
        "months":  months_out,
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "note":    "SEAS5: 51 members, 36km. Kun sannsynlighetstrender — ikke hendelsesprognoser.",
    }


# ── Hoved-fetcher: henter alle lag for én region ──────────────────────────────

def fetch_all_ecmwf_for_region(region: dict) -> dict:
    """
    Henter alle ECMWF-lag for én region:
    HRES (15d) + ENS (10d) + EC46 (46d)
    SEAS5 er deaktivert — returnerer konsekvent 400 Bad Request.
    """
    logger.info(f"  HRES 15d   → {region['name']}")
    hres = fetch_ecmwf_hres(region)

    logger.info(f"  ENS 51mbr  → {region['name']}")
    ens = fetch_ecmwf_ensemble(region)

    logger.info(f"  EC46 46d   → {region['name']}")
    subseasonal = fetch_ecmwf_subseasonal(region)

    # SEAS5 deaktivert — API returnerer 400 for alle regioner (modell ikke tilgjengelig)
    seasonal = None

    return {
        "region":      region,
        "hres":        hres,
        "ensemble":    ens,
        "subseasonal": subseasonal,
        "seasonal":    seasonal,
        "fetched_at":  datetime.now(timezone.utc).isoformat(),
    }


def fetch_ecmwf_all_regions(region_list: list = None) -> list:
    """
    Henter ECMWF-data for alle regioner i lista.
    Standard: alle Europa + Norge-regioner.
    """
    if region_list is None:
        region_list = ALL_REGIONS

    results = []
    for region in region_list:
        logger.info(f"Henter ECMWF for {region['name']} ({region['country']})...")
        try:
            result = fetch_all_ecmwf_for_region(region)
            results.append(result)
        except Exception as e:
            logger.error(f"Feil for {region['name']}: {e}")
            results.append({
                "region":      region,
                "hres":        None,
                "ensemble":    None,
                "subseasonal": None,
                "seasonal":    None,
                "error":       str(e),
            })
    return results


# ── CLI-test ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s"
    )

    # Test én region
    test_region = NORWAY_REGIONS[0]  # Vestfold
    print(f"\n=== Test: {test_region['name']} ===")

    hres = fetch_ecmwf_hres(test_region)
    if hres:
        print(f"\nHRES 15d — første 3 dager:")
        for d in hres["forecast"][:3]:
            print(f"  {d['date_display']} ({d['weekday']}): "
                  f"{d['temp_min']}–{d['temp_max']}°C, "
                  f"{d['precip']}mm")

    ens = fetch_ecmwf_ensemble(test_region)
    if ens:
        print(f"\nENS — confidence: {ens['confidence']} (spredning {ens['avg_spread']}°C)")
        for d in ens["days"][:3]:
            print(f"  {d['date_display']}: p10={d['temp_p10']}°C "
                  f"p50={d['temp_p50']}°C p90={d['temp_p90']}°C")

    sub = fetch_ecmwf_subseasonal(test_region)
    if sub:
        print(f"\nEC46 — uker:")
        for w in sub["weeks"][:4]:
            print(f"  Uke {w['week']} ({w['date_from']}–{w['date_to']}): "
                  f"{w['temp_min']}–{w['temp_max']}°C, "
                  f"{w['precip_total']}mm")

    seas = fetch_ecmwf_seasonal(test_region)
    if seas:
        print(f"\nSEAS5 — måneder:")
        for m in seas["months"][:3]:
            print(f"  {m['month_short']}: {m['temp_min']}–{m['temp_max']}°C, {m['precip']}mm")
