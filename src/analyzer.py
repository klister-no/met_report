"""
analyzer.py
-----------
Calculates temperature anomalies, precipitation anomalies,
risk levels and 4-week trend from fetched data + historical records.

New in this version:
  - 7-day temperature trend: temp_7d_avg, temp_7d_anomaly, temp_7d_daily
  - 30-day precipitation history: precip_30d_daily, precip_30d_total_mm,
    precip_30d_normal_mm, precip_30d_anomaly_pct
  Both come from data_fetcher.py and are passed through to generate_html.py.
"""

import json
import logging
import calendar
import statistics
from datetime import date
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

HISTORY_DIR = Path("data/history")
HISTORY_DIR.mkdir(parents=True, exist_ok=True)

STATUS_WARMER = "🟢"
STATUS_NORMAL = "🟡"
STATUS_COOLER = "🔵"

RISK_LOW      = "Lav"
RISK_MODERATE = "Moderat"
RISK_HIGH     = "Høy"

TREND_UP      = "↑"
TREND_DOWN    = "↓"
TREND_STABLE  = "→"


# ── Klimanormaler for 30-dagers nedbørsberegning ─────────────────────────────
# mm/mnd WMO 1991–2020. Brukes til å beregne forventet akkumulert
# nedbør for nøyaktig 30-dagers vindu (tar hensyn til månedsskifte).

PRECIP_MONTHLY_NORMALS_MM = {
    # ── Italia ──────────────────────────────────────────────────────────────
    "IT_PO_VALLEY":        [ 50,  48,  55,  68,  80,  65,  45,  55,  75,  90,  80,  55],
    "IT_CENTRAL":          [ 65,  60,  65,  70,  75,  50,  25,  30,  65,  95, 100,  80],
    "IT_NAPLES_AMALFI":    [ 90,  80,  75,  65,  55,  30,  15,  20,  60, 110, 115, 100],
    # ── Spania – Murcia (to separate soner fra 2026-03-10) ──────────────────
    # Gammel: "ES_MURCIA" (Murcia by, 7178I) — erstattet av disse to:
    "ES_MURCIA_LORCA":     [ 22,  20,  25,  28,  25,  12,   4,   6,  22,  42,  35,  25],
    "ES_MURCIA_CAMPO":     [ 25,  22,  28,  30,  25,  10,   3,   5,  22,  40,  32,  24],
    # ── Spania – Valencia (tre soner fra 2026-03-10) ─────────────────────────
    "ES_VALENCIA_NORTE":   [ 35,  30,  35,  38,  42,  22,  10,  15,  58,  75,  55,  40],
    "ES_CASTELLON":        [ 40,  35,  40,  42,  50,  28,  12,  18,  65,  80,  60,  45],
    "ES_VALENCIA_SUR":     [ 45,  38,  42,  45,  48,  25,  10,  18,  75,  95,  65,  48],
    # ── Spania – Andalucía ──────────────────────────────────────────────────
    "ES_ALMERIA":          [ 20,  18,  22,  25,  18,   8,   2,   5,  20,  35,  30,  22],
    "ES_HUELVA":           [ 65,  55,  48,  40,  28,   8,   2,   3,  25,  65,  75,  70],
    "ES_SEVILLA":          [ 60,  52,  45,  38,  25,   6,   1,   2,  22,  60,  70,  65],
    "ES_MADRID_REF":       [ 38,  35,  42,  48,  52,  28,  12,  10,  30,  55,  50,  42],
    # ── Portugal ────────────────────────────────────────────────────────────
    "PT_LISBON_ALGARVE":   [ 90,  75,  65,  48,  38,  12,   3,   4,  30,  85, 100,  95],
    # ── Marokko ─────────────────────────────────────────────────────────────
    "MA_NORTH_RABAT":      [ 70,  60,  55,  40,  22,   5,   1,   2,  18,  55,  75,  72],
    "MA_SOUTH_AGADIR":     [ 30,  25,  22,  15,   8,   2,   0,   0,   8,  22,  30,  28],
}


def _load_thresholds(config_path: str = "config/regions.yaml") -> dict:
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    return cfg.get("risk_thresholds", {})


def _month_index(week_start: str) -> int:
    """Returns 0-based month index from ISO date string."""
    return date.fromisoformat(week_start).month - 1


def _safe_mean(values: list) -> Optional[float]:
    """Mean of non-None values, or None if insufficient data."""
    clean = [v for v in (values or []) if v is not None]
    return round(statistics.mean(clean), 1) if len(clean) >= 3 else None


def _safe_sum(values: list) -> Optional[float]:
    clean = [v for v in (values or []) if v is not None]
    return round(sum(clean), 1) if clean else None


# ── 7-dagers temperaturberegning ─────────────────────────────────────────────

def compute_7d_temp_avg(temp_7d_daily: list) -> Optional[float]:
    """7-dagers gjennomsnittstemperatur fra daglige mean-verdier."""
    means = [d["temp_mean"] for d in temp_7d_daily if d.get("temp_mean") is not None]
    if not means:
        return None
    return round(sum(means) / len(means), 1)


def compute_7d_temp_anomaly(temp_7d_avg: Optional[float],
                             normal_temp: float) -> Optional[float]:
    """Avvik fra klimanormal for 7-dagers snitt."""
    if temp_7d_avg is None:
        return None
    return round(temp_7d_avg - normal_temp, 1)


# ── 30-dagers nedbørsberegning ────────────────────────────────────────────────

def get_30d_normal_mm(region_id: str) -> float:
    """
    Beregner forventet akkumulert nedbør for de siste 30 dagene,
    basert på WMO 1991–2020 månedsnormaler.
    Tar hensyn til at 30-dagers vindu kan spenne to måneder.
    """
    today      = date.today()
    start_date = today.replace(day=today.day) - __import__('datetime').timedelta(days=29)
    normals    = PRECIP_MONTHLY_NORMALS_MM.get(region_id)

    if not normals:
        return 50.0   # generisk fallback

    total = 0.0
    for i in range(30):
        d = start_date + __import__('datetime').timedelta(days=i)
        month_norm      = normals[d.month - 1]
        days_in_month   = calendar.monthrange(d.year, d.month)[1]
        total          += month_norm / days_in_month

    return round(total, 1)


def compute_30d_precip_anomaly_pct(total_mm: float,
                                    normal_mm: float) -> Optional[float]:
    """Prosentavvik for 30-dagers akkumulert nedbør vs. normal."""
    if normal_mm <= 0:
        return None
    return round((total_mm - normal_mm) / normal_mm * 100, 0)


# ── Single region analysis ────────────────────────────────────────────────────

def analyze_region(record: dict, thresholds: dict) -> dict:
    """
    Produces a structured analysis dict for one region and one week.

    Returns:
    {
      "region_id":    str,
      "region_name":  str,
      "week_start":   str,
      "week_end":     str,

      # Dagens observasjon
      "temp_day_actual":    float | None,
      "temp_day_normal":    float | None,
      "temp_day_anomaly":   float | None,
      "temp_night_actual":  float | None,
      "temp_night_normal":  float | None,
      "temp_night_anomaly": float | None,
      "temp_status":        str,            # 🟢 🟡 🔵

      # 7-dagers temperaturtrend (ny)
      "temp_7d_avg":        float | None,
      "temp_7d_anomaly":    float | None,
      "temp_7d_daily":      list[dict],     # [{date, temp_max, temp_min, temp_mean}]

      # Ukesnedbør
      "precip_actual_mm":   float | None,
      "precip_normal_mm":   float | None,
      "precip_anomaly_mm":  float | None,
      "precip_anomaly_pct": float | None,

      # 30-dagers nedbørstrend (ny)
      "precip_30d_daily":       list[dict],  # [{date, precip_mm}]
      "precip_30d_total_mm":    float | None,
      "precip_30d_normal_mm":   float,
      "precip_30d_anomaly_pct": float | None,

      "frost_risk":         bool,

      "risk_temp":       str,
      "risk_precip":     str,
      "risk_transport":  str,
      "risk_production": str,
      "risk_total":      str,

      "forecast_temp_direction": str,
      "forecast_precip_note":    str,
    }
    """
    region     = record["region"]
    rid        = region["id"]
    obs        = record.get("observations") or {}
    fcast      = record.get("forecast_14d") or {}
    week_start = record["week_start"]
    week_end   = record["week_end"]
    month_idx  = _month_index(week_start)

    normals              = region["normals"]
    normal_day           = normals["temp_day"][month_idx]
    normal_night         = normals["temp_night"][month_idx]
    normal_precip        = normals["precip_mm"][month_idx]
    normal_precip_week   = round(normal_precip * (7 / 30), 1)

    # ── Observed temperatures ─────────────────────────────────────────────────
    actual_day   = _safe_mean(obs.get("temp_max", []))
    actual_night = _safe_mean(obs.get("temp_min", []))
    anom_day     = round(actual_day   - normal_day,   1) if actual_day   is not None else None
    anom_night   = round(actual_night - normal_night, 1) if actual_night is not None else None

    thr = thresholds.get("temperature", {})
    lo, hi = thr.get("low", [-1.5, 1.5])
    if anom_day is None:
        temp_status = STATUS_NORMAL
    elif anom_day > hi:
        temp_status = STATUS_WARMER
    elif anom_day < lo:
        temp_status = STATUS_COOLER
    else:
        temp_status = STATUS_NORMAL

    # ── 7-dagers temperaturtrend ──────────────────────────────────────────────
    temp_7d_daily  = record.get("temp_7d_daily") or []
    temp_7d_avg    = compute_7d_temp_avg(temp_7d_daily)
    temp_7d_anomaly = compute_7d_temp_anomaly(temp_7d_avg, normal_day)

    # ── Precipitation (ukesdata) ───────────────────────────────────────────────
    actual_precip   = _safe_sum(obs.get("precip_sum", []))
    precip_anom_mm  = round(actual_precip - normal_precip_week, 1) if actual_precip is not None else None
    precip_anom_pct = (round((actual_precip / normal_precip_week - 1) * 100, 0)
                       if actual_precip is not None and normal_precip_week > 0 else None)

    # ── 30-dagers nedbørstrend ────────────────────────────────────────────────
    precip_30d_daily  = record.get("precip_30d_daily") or []
    precip_30d_normal = get_30d_normal_mm(rid)
    # Guard: tom liste = API timet ut, ikke faktisk 0mm — returner None, ikke -100%
    if precip_30d_daily:
        precip_30d_total    = round(sum(d.get("precip_mm", 0) or 0 for d in precip_30d_daily), 1)
        precip_30d_anom_pct = compute_30d_precip_anomaly_pct(precip_30d_total, precip_30d_normal)
    else:
        precip_30d_total    = None
        precip_30d_anom_pct = None

    # ── Frost risk ────────────────────────────────────────────────────────────
    frost_thresh = thresholds.get("frost_threshold_c", 2)
    frost_risk   = (actual_night is not None and actual_night < frost_thresh)

    # ── Tørkeindikator: kombinert 30d nedbørsunderskudd + varmeavvik ──────────
    risk_drought = _classify_drought_risk(precip_30d_anom_pct, temp_7d_anomaly,
                                          month=month_idx + 1)  # 1-basert måned

    # ── Vindrisiko (fra forecast) ─────────────────────────────────────────────
    fcast_wind_gusts = fcast.get("wind_gust_max_7d_ms")   # injisert av data_fetcher
    risk_wind        = _classify_wind_risk(fcast_wind_gusts)

    # ── Risk classification ───────────────────────────────────────────────────
    risk_temp   = _classify_temp_risk(anom_day, thr)
    risk_precip = _classify_precip_risk(precip_anom_pct, thresholds.get("precipitation", {}))
    risk_transport, risk_production = _derive_secondary_risks(
        risk_temp, risk_precip, frost_risk, region,
        precip_anom_pct=precip_anom_pct,
        precip_thr=thresholds.get("precipitation", {}),
        risk_drought=risk_drought,
        risk_wind=risk_wind,
    )
    risk_total = _total_risk(risk_temp, risk_precip, risk_transport, risk_production,
                             risk_drought=risk_drought, risk_wind=risk_wind)

    # ── Forecast direction ────────────────────────────────────────────────────
    fcast_temps    = fcast.get("temp_max", [])[:7]
    fcast_day_mean = _safe_mean(fcast_temps)
    if fcast_day_mean is not None:
        fcast_anom = fcast_day_mean - normal_day
        fcast_dir  = TREND_UP if fcast_anom > 1.0 else TREND_DOWN if fcast_anom < -1.0 else TREND_STABLE
    else:
        fcast_anom = None
        fcast_dir  = TREND_STABLE

    fcast_precip       = fcast.get("precip_sum", [])[:7]
    fcast_precip_total = _safe_sum(fcast_precip)
    if fcast_precip_total is not None:
        if fcast_precip_total > normal_precip_week * 2:
            fcast_precip_note = "⚠️ Kraftig nedbør varslet neste 7 dager (>2× normalt)"
        elif fcast_precip_total > normal_precip_week * 1.3:
            fcast_precip_note = "Mer nedbør enn normalt"
        elif fcast_precip_total < normal_precip_week * 0.5:
            fcast_precip_note = "Tørrere enn normalt"
        else:
            fcast_precip_note = "Nær normalt nedbør"
    else:
        fcast_precip_note = "Ingen data"

    # ── Varsler for kommende ekstremvær ──────────────────────────────────────
    alerts_7d: list[str] = []
    if fcast_precip_total is not None and fcast_precip_total > normal_precip_week * 2:
        alerts_7d.append(f"🌧️ Kraftig nedbør neste 7d ({fcast_precip_total:.0f}mm, >{normal_precip_week*2:.0f}mm normalt)")
    if fcast_wind_gusts is not None and fcast_wind_gusts > 20.0:
        alerts_7d.append(f"🌬️ Storm varslet neste 7d (vindkast {fcast_wind_gusts:.1f} m/s)")
    elif fcast_wind_gusts is not None and fcast_wind_gusts > 15.0:
        alerts_7d.append(f"💨 Sterk vind varslet neste 7d (kast {fcast_wind_gusts:.1f} m/s)")
    if risk_drought == RISK_HIGH:
        alerts_7d.append("🌵 Tørkerisiko: 30d nedbørsunderskudd + varmeavvik kombinert")
    elif risk_drought == RISK_MODERATE:
        alerts_7d.append("🌡️ Tidlig tørkesignal: underskudd + varme")

    wind_gust_now_ms = fcast.get("wind_gust_now_ms")   # fra data_fetcher hvis tilgjengelig
    wind_sustained_ms = fcast.get("wind_sustained_ms")

    return {
        "region_id":    rid,
        "region_name":  region["name"],
        "week_start":   week_start,
        "week_end":     week_end,

        # Dagens observasjon
        "temp_day_actual":    actual_day,
        "temp_day_normal":    normal_day,
        "temp_day_anomaly":   anom_day,
        "temp_night_actual":  actual_night,
        "temp_night_normal":  normal_night,
        "temp_night_anomaly": anom_night,
        "temp_status":        temp_status,

        # 7-dagers temperaturtrend
        "temp_7d_avg":        temp_7d_avg,
        "temp_7d_anomaly":    temp_7d_anomaly,
        "temp_7d_daily":      temp_7d_daily,

        # Ukesnedbør
        "precip_actual_mm":   actual_precip,
        "precip_normal_mm":   normal_precip_week,
        "precip_anomaly_mm":  precip_anom_mm,
        "precip_anomaly_pct": precip_anom_pct,

        # 30-dagers nedbørstrend
        "precip_30d_daily":       precip_30d_daily,
        "precip_30d_total_mm":    precip_30d_total if precip_30d_daily else None,
        "precip_30d_normal_mm":   precip_30d_normal,
        "precip_30d_anomaly_pct": precip_30d_anom_pct,

        "frost_risk":         frost_risk,

        "risk_temp":       risk_temp,
        "risk_precip":     risk_precip,
        "risk_drought":    risk_drought,
        "risk_wind":       risk_wind,
        "risk_transport":  risk_transport,
        "risk_production": risk_production,
        "risk_total":      risk_total,

        # Vind (neste 7 dager)
        "wind_gust_max_7d_ms":  fcast_wind_gusts,
        "wind_gust_now_ms":     wind_gust_now_ms,
        "wind_sustained_ms":    wind_sustained_ms,

        # 7-dagers varsler (listen av tekst-strenger)
        "alerts_7d":            alerts_7d,

        "forecast_temp_direction": fcast_dir,
        "forecast_temp_anomaly":   round(fcast_anom, 1) if fcast_anom is not None else None,
        "forecast_precip_note":    fcast_precip_note,
    }


def _classify_temp_risk(anomaly: Optional[float], thr: dict) -> str:
    if anomaly is None:
        return RISK_LOW
    lo_mod, hi_mod = thr.get("moderate", [-3.0, 3.0])
    lo_low, hi_low = thr.get("low", [-1.5, 1.5])
    if lo_low <= anomaly <= hi_low:
        return RISK_LOW
    if lo_mod <= anomaly <= hi_mod:
        return RISK_MODERATE
    return RISK_HIGH


def _classify_precip_risk(pct: Optional[float], thr: dict) -> str:
    """
    Asymmetrisk risikoscoring for nedbør:
    - Overskudd vektes tyngre enn underskudd (flom, råte, transportstans)
    - Underskudd gir maks Moderat alene — krever kombinasjon med temperatur for Høy
    - Tørkekombinasjon håndteres i _classify_drought_risk() + _derive_secondary_risks()
    """
    if pct is None:
        return RISK_LOW
    high_exc = thr.get("high_excess_pct",      150)   # >150% → Høy
    mod_exc  = thr.get("moderate_excess_pct",   50)   # >50%  → Moderat
    mod_def  = thr.get("moderate_deficit_pct", -40)   # <-40% → Moderat (maks alene)
    # Underskudd kan ikke alene nå Høy — det krever drought-kombinasjon
    if pct > high_exc:
        return RISK_HIGH
    if pct > mod_exc:
        return RISK_MODERATE
    if pct < mod_def:
        return RISK_MODERATE   # tørkeunderskudd — maks Moderat her
    return RISK_LOW


def _classify_drought_risk(precip_30d_pct: Optional[float],
                            temp_7d_anom: Optional[float],
                            month: int = 6) -> str:
    """
    Tørkeindikator: Kombinasjon av 30d nedbørsunderskudd + varmeavvik.
    Krever BEGGE signaler for å gi Høy.

    Sesongfilter (agronmisk korrekt):
      - Nov–Mar (mnd 11,12,1,2,3): tørke ikke relevant → alltid Lav
      - Apr, Okt: lav sesongvekt (0.3) — tidlig/sen sesong
      - Mai–Sep: full vekt (1.0) — primær tørkesesong

    Absolutt minimumsterskel:
      - Krever at 30d total er reelt lav, ikke bare relativ
      - Kalibrert via precip_30d_pct men blokkeres av sesongfilter

    Logikk:
      - 30d underskudd ≥ 40% + temp +1.5°C + sesong ≥ 0.3 → Høy tørkerisiko
      - 30d underskudd ≥ 25% + temp +1.0°C + sesong ≥ 0.3 → Moderat tørkerisiko
      - Nov–Mar: alltid Lav (nedbørsunderskudd er forventet/uproblematisk)
    """
    # Sesongvekt — nov-mar er aldri tørkerisikomåneder i Sør-Europa/MA
    SEASON_WEIGHT = {
        1: 0.0, 2: 0.0, 3: 0.0,   # jan-mar: vinter/tidlig vår
        4: 0.3,                     # apr: overgang
        5: 1.0, 6: 1.0, 7: 1.0,   # mai-jul: høy sesong
        8: 1.0, 9: 1.0,            # aug-sep: høy sesong
        10: 0.3,                    # okt: avslutning
        11: 0.0, 12: 0.0,          # nov-des: vinter
    }
    weight = SEASON_WEIGHT.get(month, 0.5)

    if weight == 0.0:
        return RISK_LOW   # Vinter — tørke ikke relevant

    if precip_30d_pct is None or temp_7d_anom is None:
        return RISK_LOW

    deficit = precip_30d_pct   # negativt tall = underskudd
    heat    = temp_7d_anom

    # Juster terskler etter sesongvekt (apr/okt krever sterkere signal)
    deficit_high  = -40  / weight   # apr/okt: krever -133%, mai-sep: -40%
    deficit_mod   = -25  / weight
    heat_high     = 1.5  / weight
    heat_mod      = 1.0  / weight

    # Begrens til rimelige absoluttverdier
    deficit_high = max(deficit_high, -80)
    deficit_mod  = max(deficit_mod,  -60)
    heat_high    = min(heat_high,     4.0)
    heat_mod     = min(heat_mod,      3.0)

    if deficit <= deficit_high and heat >= heat_high:
        return RISK_HIGH
    if deficit <= deficit_mod and heat >= heat_mod:
        return RISK_MODERATE
    return RISK_LOW


def _classify_wind_risk(wind_gust_max_ms: Optional[float]) -> str:
    """
    Vindrisiko basert på maksimalt vindkast (m/s) de neste 7 dagene.
    Terskler fra agro-meteorologisk standard:
      - >20 m/s → Høy (mekanisk skade på frukt/grønnsaker)
      - >15 m/s → Moderat (risiko for skade, særlig blomstringsperiode)
    """
    if wind_gust_max_ms is None:
        return RISK_LOW
    if wind_gust_max_ms > 20.0:
        return RISK_HIGH
    if wind_gust_max_ms > 15.0:
        return RISK_MODERATE
    return RISK_LOW


def _classify_transport_risk(pct: Optional[float], thr: dict) -> str:
    """
    Transport-risiko drives KUN av nedbørsoverskudd (flom, veistengning).
    Tørke gir IKKE transportrisiko — bare produksjonsrisiko.
    """
    if pct is None:
        return RISK_LOW
    high_exc = thr.get("high_excess_pct",  150)
    mod_exc  = thr.get("moderate_excess_pct", 50)
    if pct > high_exc:
        return RISK_HIGH
    if pct > mod_exc:
        return RISK_MODERATE
    return RISK_LOW


def _derive_secondary_risks(risk_temp: str, risk_precip: str,
                             frost_risk: bool, region: dict,
                             precip_anom_pct: Optional[float] = None,
                             precip_thr: dict = None,
                             risk_drought: str = RISK_LOW,
                             risk_wind: str = RISK_LOW) -> tuple[str, str]:
    """
    Beregner transport- og produksjonsrisiko.

    Transport: KUN nedbørsoverskudd + frost trigger risiko — ikke tørke eller vind.
    Produksjon: Alle faktorer (temp, nedbør, tørke, vind, frost).
    """
    precip_thr = precip_thr or {}
    transport  = _classify_transport_risk(precip_anom_pct, precip_thr)
    if frost_risk and risk_temp in (RISK_MODERATE, RISK_HIGH):
        transport = RISK_HIGH

    # Produksjonsrisiko: alle risikofaktorer
    prod_levels = [risk_temp, risk_precip, risk_drought, risk_wind]
    if frost_risk:
        prod_levels.append(RISK_HIGH)

    if RISK_HIGH in prod_levels:
        production = RISK_HIGH
    elif RISK_MODERATE in prod_levels:
        production = RISK_MODERATE
    else:
        production = RISK_LOW

    return transport, production


def _total_risk(risk_temp: str, risk_precip: str,
                risk_transport: str, risk_production: str,
                risk_drought: str = RISK_LOW,
                risk_wind: str = RISK_LOW) -> str:
    levels = [risk_temp, risk_precip, risk_transport, risk_production,
              risk_drought, risk_wind]
    if RISK_HIGH in levels:
        return RISK_HIGH
    if RISK_MODERATE in levels:
        return RISK_MODERATE
    return RISK_LOW


# ── Historical trend (4 weeks) ────────────────────────────────────────────────

def _history_path(region_id: str) -> Path:
    return HISTORY_DIR / f"{region_id}_history.json"


def save_to_history(analyses: list[dict]):
    """Appends current week's analysis to each region's history file."""
    for a in analyses:
        path    = _history_path(a["region_id"])
        history = []
        if path.exists():
            with open(path) as f:
                history = json.load(f)

        existing_weeks = {h["week_start"] for h in history}
        if a["week_start"] not in existing_weeks:
            # Store lightweight snapshot — ikke lagre 30d daily (stor payload)
            snapshot = {k: v for k, v in a.items()
                        if k not in ("precip_30d_daily", "temp_7d_daily")}
            history.append(snapshot)
            history = sorted(history, key=lambda x: x["week_start"])[-12:]
            with open(path, "w") as f:
                json.dump(history, f, indent=2)


def load_trend(region_id: str, weeks: int = 4) -> list[dict]:
    """Returns last N weeks of analysis for a region."""
    path = _history_path(region_id)
    if not path.exists():
        return []
    with open(path) as f:
        history = json.load(f)
    return sorted(history, key=lambda x: x["week_start"])[-weeks:]


def compute_trend_direction(region_id: str) -> str:
    """Returns ↑ / → / ↓ based on temperature anomaly trend over last 4 weeks."""
    history = load_trend(region_id, weeks=4)
    if len(history) < 2:
        return TREND_STABLE
    anoms = [h["temp_day_anomaly"] for h in history if h.get("temp_day_anomaly") is not None]
    if len(anoms) < 2:
        return TREND_STABLE
    delta = anoms[-1] - anoms[0]
    if delta > 1.0:
        return TREND_UP
    if delta < -1.0:
        return TREND_DOWN
    return TREND_STABLE


# ── Main entry point ──────────────────────────────────────────────────────────

def analyze_all(fetched_data: dict,
                config_path: str = "config/regions.yaml") -> list[dict]:
    """
    Runs analysis for all regions. Returns list of analysis dicts
    in the same region order as config file.
    """
    thresholds = _load_thresholds(config_path)

    with open(config_path) as f:
        cfg = yaml.safe_load(f)
    ordered_ids = [r["id"] for r in cfg["regions"]]

    analyses = []
    for rid in ordered_ids:
        if rid not in fetched_data:
            logger.warning(f"No data for region {rid} – skipping")
            continue
        record = fetched_data[rid]
        try:
            result = analyze_region(record, thresholds)
            result["trend_direction"] = compute_trend_direction(rid)
            result["trend_history"]   = load_trend(rid, weeks=4)
            analyses.append(result)
            logger.info(
                f"  Analyzed: {result['region_name']}  "
                f"T={result['temp_day_actual']}°C  "
                f"7d={result['temp_7d_avg']}°C  "
                f"30d={result['precip_30d_anomaly_pct']:+.0f}%  "
                f"Risk={result['risk_total']}"
                if result['precip_30d_anomaly_pct'] is not None
                else f"  Analyzed: {result['region_name']}  "
                     f"T={result['temp_day_actual']}°C  "
                     f"Risk={result['risk_total']}"
            )
        except Exception as e:
            logger.error(f"Analysis failed for {rid}: {e}", exc_info=True)

    save_to_history(analyses)
    return analyses


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s  %(levelname)-8s %(message)s")
    dummy_record = {
        "region": {
            "id": "ES_HUELVA",
            "name": "ES – Huelva",
            "country": "Spain",
            "lat": 37.26, "lon": -6.95,
            "aemet_station": "4642E",
            "key_crops": ["strawberries"],
            "normals": {
                "temp_day":   [15,16,19,21,25,30,33,33,29,23,18,15],
                "temp_night": [ 6, 7, 9,11,14,18,20,20,17,13, 9, 7],
                "precip_mm":  [82,70,54,42,24, 6, 1, 2,18,52,80,90],
            }
        },
        "observations": {
            "temp_max":   [12, 13, 11, 14, 13, 12, 11],
            "temp_min":   [ 6,  7,  5,  7,  6,  5,  6],
            "precip_sum": [28, 42, 65, 12, 55, 30, 18],
        },
        "forecast_14d": {
            "temp_max":  [15,14,15,16,17,16,15,16,17,17,18,18,17,16],
            "temp_min":  [ 8, 7, 8, 9, 9, 8, 8, 9, 9,10,10, 9, 9, 8],
            "precip_sum":[10, 5, 2, 0, 0, 3, 8, 5, 2, 0, 0, 4, 6, 3],
        },
        # Simulerte 7d og 30d data
        "temp_7d_daily": [
            {"date": "2026-02-16", "temp_max": 13.2, "temp_min": 6.1, "temp_mean": 9.6},
            {"date": "2026-02-17", "temp_max": 14.0, "temp_min": 6.8, "temp_mean": 10.4},
            {"date": "2026-02-18", "temp_max": 11.5, "temp_min": 5.2, "temp_mean":  8.3},
            {"date": "2026-02-19", "temp_max": 12.8, "temp_min": 6.0, "temp_mean":  9.4},
            {"date": "2026-02-20", "temp_max": 13.5, "temp_min": 6.5, "temp_mean": 10.0},
            {"date": "2026-02-21", "temp_max": 14.2, "temp_min": 7.0, "temp_mean": 10.6},
            {"date": "2026-02-22", "temp_max": 15.0, "temp_min": 7.5, "temp_mean": 11.2},
        ],
        "precip_30d_daily": [
            {"date": f"2026-01-{d:02d}", "precip_mm": round(d * 2.5, 1)} for d in range(1, 31)
        ],
        "week_start": "2026-02-09",
        "week_end":   "2026-02-15",
    }
    thresholds = {
        "temperature":   {"low": [-1.5, 1.5], "moderate": [-3.0, 3.0]},
        "precipitation": {"high_excess_pct": 150, "moderate_excess_pct": 50, "high_deficit_pct": -40},
        "frost_threshold_c": 2,
    }
    result = analyze_region(dummy_record, thresholds)
    print(json.dumps({k: v for k, v in result.items()
                      if k not in ("precip_30d_daily", "temp_7d_daily")},
                     indent=2, ensure_ascii=False))
    print(f"\ntemp_7d_avg:            {result['temp_7d_avg']}")
    print(f"temp_7d_anomaly:        {result['temp_7d_anomaly']}")
    print(f"precip_30d_total_mm:    {result['precip_30d_total_mm']}")
    print(f"precip_30d_normal_mm:   {result['precip_30d_normal_mm']}")
    print(f"precip_30d_anomaly_pct: {result['precip_30d_anomaly_pct']}")
