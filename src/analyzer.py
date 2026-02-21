"""
analyzer.py
-----------
Calculates temperature anomalies, precipitation anomalies,
risk levels and 4-week trend from fetched data + historical records.
"""

import json
import logging
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

      "temp_day_actual":    float | None,   # mean of daily max (°C)
      "temp_day_normal":    float | None,
      "temp_day_anomaly":   float | None,
      "temp_night_actual":  float | None,   # mean of daily min (°C)
      "temp_night_normal":  float | None,
      "temp_night_anomaly": float | None,
      "temp_status":        str,            # 🟢 🟡 🔵

      "precip_actual_mm":   float | None,   # weekly total
      "precip_normal_mm":   float | None,   # prorated monthly normal
      "precip_anomaly_mm":  float | None,
      "precip_anomaly_pct": float | None,

      "frost_risk":         bool,

      "risk_temp":       str,   # Lav / Moderat / Høy
      "risk_precip":     str,
      "risk_transport":  str,
      "risk_production": str,
      "risk_total":      str,

      "forecast_temp_direction": str,   # ↑ → ↓
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

    normals       = region["normals"]
    normal_day    = normals["temp_day"][month_idx]
    normal_night  = normals["temp_night"][month_idx]
    normal_precip = normals["precip_mm"][month_idx]
    # Week is ~1/4 of month → prorated normal for 7 days
    normal_precip_week = round(normal_precip * (7 / 30), 1)

    # ── Observed temperatures ─────────────────────────────────────────────────
    actual_day   = _safe_mean(obs.get("temp_max", []))
    actual_night = _safe_mean(obs.get("temp_min", []))
    anom_day     = round(actual_day   - normal_day,   1) if actual_day   is not None else None
    anom_night   = round(actual_night - normal_night, 1) if actual_night is not None else None

    # Status indicator based on day anomaly
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

    # ── Precipitation ─────────────────────────────────────────────────────────
    actual_precip     = _safe_sum(obs.get("precip_sum", []))
    precip_anom_mm    = round(actual_precip - normal_precip_week, 1) if actual_precip is not None else None
    precip_anom_pct   = (round((actual_precip / normal_precip_week - 1) * 100, 0)
                         if actual_precip is not None and normal_precip_week > 0 else None)

    # ── Frost risk ────────────────────────────────────────────────────────────
    frost_thresh = thresholds.get("frost_threshold_c", 2)
    frost_risk   = (actual_night is not None and actual_night < frost_thresh)

    # ── Risk classification ───────────────────────────────────────────────────
    risk_temp = _classify_temp_risk(anom_day, thr)
    risk_precip = _classify_precip_risk(precip_anom_pct, thresholds.get("precipitation", {}))
    risk_transport, risk_production = _derive_secondary_risks(
        risk_temp, risk_precip, frost_risk, region
    )
    risk_total = _total_risk(risk_temp, risk_precip, risk_transport, risk_production)

    # ── Forecast direction (next 7 days vs. normal) ───────────────────────────
    fcast_temps = fcast.get("temp_max", [])[:7]
    fcast_day_mean = _safe_mean(fcast_temps)
    if fcast_day_mean is not None:
        fcast_anom = fcast_day_mean - normal_day
        if fcast_anom > 1.0:
            fcast_dir = TREND_UP
        elif fcast_anom < -1.0:
            fcast_dir = TREND_DOWN
        else:
            fcast_dir = TREND_STABLE
    else:
        fcast_dir = TREND_STABLE

    fcast_precip = fcast.get("precip_sum", [])[:7]
    fcast_precip_total = _safe_sum(fcast_precip)
    if fcast_precip_total is not None:
        if fcast_precip_total > normal_precip_week * 2:
            fcast_precip_note = "Mye nedbør ventet (>2× normalt)"
        elif fcast_precip_total > normal_precip_week * 1.3:
            fcast_precip_note = "Mer nedbør enn normalt"
        elif fcast_precip_total < normal_precip_week * 0.5:
            fcast_precip_note = "Tørrere enn normalt"
        else:
            fcast_precip_note = "Nær normalt nedbør"
    else:
        fcast_precip_note = "Ingen data"

    return {
        "region_id":    rid,
        "region_name":  region["name"],
        "week_start":   week_start,
        "week_end":     week_end,

        "temp_day_actual":    actual_day,
        "temp_day_normal":    normal_day,
        "temp_day_anomaly":   anom_day,
        "temp_night_actual":  actual_night,
        "temp_night_normal":  normal_night,
        "temp_night_anomaly": anom_night,
        "temp_status":        temp_status,

        "precip_actual_mm":   actual_precip,
        "precip_normal_mm":   normal_precip_week,
        "precip_anomaly_mm":  precip_anom_mm,
        "precip_anomaly_pct": precip_anom_pct,

        "frost_risk":         frost_risk,

        "risk_temp":       risk_temp,
        "risk_precip":     risk_precip,
        "risk_transport":  risk_transport,
        "risk_production": risk_production,
        "risk_total":      risk_total,

        "forecast_temp_direction": fcast_dir,
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
    if pct is None:
        return RISK_LOW
    high_exc  = thr.get("high_excess_pct", 150)
    mod_exc   = thr.get("moderate_excess_pct", 50)
    high_def  = thr.get("high_deficit_pct", -40)
    if pct > high_exc:
        return RISK_HIGH
    if pct > mod_exc or pct < high_def:
        return RISK_MODERATE
    return RISK_LOW


def _derive_secondary_risks(risk_temp: str, risk_precip: str,
                             frost_risk: bool, region: dict) -> tuple[str, str]:
    """
    Transport risk: driven by extreme precip (flooding, road closures).
    Production risk: driven by temp extremes AND precip extremes.
    """
    transport  = risk_precip   # flooding = primary transport disruptor
    if frost_risk and risk_temp in (RISK_MODERATE, RISK_HIGH):
        transport = RISK_HIGH

    # Production risk = worst of temp and precip, boosted by frost
    prod_levels = [risk_temp, risk_precip]
    if RISK_HIGH in prod_levels:
        production = RISK_HIGH
    elif RISK_MODERATE in prod_levels:
        production = RISK_MODERATE
    else:
        production = RISK_LOW

    if frost_risk:
        production = RISK_HIGH

    return transport, production


def _total_risk(risk_temp: str, risk_precip: str,
                risk_transport: str, risk_production: str) -> str:
    levels = [risk_temp, risk_precip, risk_transport, risk_production]
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
        path = _history_path(a["region_id"])
        history = []
        if path.exists():
            with open(path) as f:
                history = json.load(f)

        # Avoid duplicate weeks
        existing_weeks = {h["week_start"] for h in history}
        if a["week_start"] not in existing_weeks:
            history.append(a)
            # Keep last 12 weeks only
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
    """
    Returns ↑ / → / ↓ based on temperature anomaly trend over last 4 weeks.
    """
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

    # Preserve config order
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
            # Attach 4-week trend
            result["trend_direction"] = compute_trend_direction(rid)
            result["trend_history"]   = load_trend(rid, weeks=4)
            analyses.append(result)
            logger.info(f"  Analyzed: {result['region_name']}  "
                        f"T={result['temp_day_actual']}°C  "
                        f"Risk={result['risk_total']}")
        except Exception as e:
            logger.error(f"Analysis failed for {rid}: {e}", exc_info=True)

    # Persist this week to history
    save_to_history(analyses)
    return analyses


if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s  %(levelname)-8s %(message)s")
    # Quick smoke test with dummy data
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
        "week_start": "2026-02-09",
        "week_end":   "2026-02-15",
    }
    thresholds = {
        "temperature":   {"low": [-1.5, 1.5], "moderate": [-3.0, 3.0]},
        "precipitation": {"high_excess_pct": 150, "moderate_excess_pct": 50, "high_deficit_pct": -40},
        "frost_threshold_c": 2,
    }
    result = analyze_region(dummy_record, thresholds)
    print(json.dumps(result, indent=2, ensure_ascii=False))
