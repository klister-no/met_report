"""
gibraltar_fetcher.py
---------------------
Henter marint vær og vinddata for Gibraltar-sundet.

Datakilder (alle gratis, ingen API-nøkkel):
  1. Open-Meteo Marine API  — bølgehøyde, bølgeperiode (5km EU-modell, 3 dager)
  2. Open-Meteo Forecast API — vindstyrke, vindretning, vindkast (ECMWF)

Gibraltar-sundet koordinater: 35.90°N, -5.60°W
(Midtpunkt mellom Algeciras og Tanger Med)

Terskler for driftsavbrudd (basert på historiske data fra APBA/Baleària):
  - Vind > 50 km/t    → forsinkelser sannsynlig
  - Vind > 70 km/t    → suspensjon sannsynlig
  - Bølger > 2.0 m    → forsinkelser sannsynlig
  - Bølger > 3.5 m    → suspensjon sannsynlig
  (Kilde: Algeciras Bay Port Authority driftsregler)
"""

import logging
import requests
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

MARINE_API   = "https://marine-api.open-meteo.com/v1/marine"
FORECAST_API = "https://api.open-meteo.com/v1/forecast"
TIMEOUT      = 20

# Midtpunkt av sundet
GIBRALTAR_LAT = 35.90
GIBRALTAR_LON = -5.60

# Operasjonelle terskler (km/t og meter)
THRESHOLDS = {
    "wind_delay_kmh":    50,
    "wind_suspend_kmh":  70,
    "wave_delay_m":       2.0,
    "wave_suspend_m":     3.5,
}


def _request(url: str, params: dict) -> Optional[dict]:
    try:
        r = requests.get(url, params=params, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.warning(f"Gibraltar fetch failed ({url}): {e}")
        return None


def fetch_gibraltar_conditions() -> dict:
    """
    Henter nåværende og 48-timers prognosevær for Gibraltar-sundet.

    Returnerer dict med:
    {
      "wind_now_kmh":       float | None,   # vindstyrke nå
      "wind_gust_now_kmh":  float | None,   # vindkast nå
      "wind_dir_now":       str | None,     # "NV", "Ø" osv.
      "wave_now_m":         float | None,   # signifikant bølgehøyde nå
      "wave_max_48h_m":     float | None,   # maks bølger neste 48t
      "wind_max_48h_kmh":   float | None,   # maks vind neste 48t
      "forecast_hours":     list[dict],     # time-serie (12 punkter, 4t intervall)
      "status":             str,            # "Normal" / "Forsinkelser" / "Suspensjon"
      "status_reason":      str,            # forklaring på status
      "alert_level":        str,            # "grønn" / "gul" / "rød"
      "levante_risk":       bool,           # Levante-vind fra øst (særlig farlig)
      "fetched_at":         str,            # ISO timestamp
    }
    """
    result = {
        "wind_now_kmh":      None,
        "wind_gust_now_kmh": None,
        "wind_dir_now":      None,
        "wave_now_m":        None,
        "wave_max_48h_m":    None,
        "wind_max_48h_kmh":  None,
        "forecast_hours":    [],
        "status":            "Ukjent",
        "status_reason":     "Ingen data",
        "alert_level":       "grå",
        "levante_risk":      False,
        "fetched_at":        datetime.now(timezone.utc).isoformat(),
    }

    # ── 1. Marine API — bølgehøyde ────────────────────────────────────────────
    marine_data = _request(MARINE_API, {
        "latitude":  GIBRALTAR_LAT,
        "longitude": GIBRALTAR_LON,
        "hourly":    "wave_height,wave_period",
        "forecast_days": 3,
        "timezone":  "Europe/Madrid",
    })

    wave_hours, wave_heights = [], []
    if marine_data and "hourly" in marine_data:
        wave_hours   = marine_data["hourly"].get("time", [])
        wave_heights = marine_data["hourly"].get("wave_height", [])

    # ── 2. Forecast API — vind ────────────────────────────────────────────────
    wind_data = _request(FORECAST_API, {
        "latitude":       GIBRALTAR_LAT,
        "longitude":      GIBRALTAR_LON,
        "hourly":         "windspeed_10m,windgusts_10m,winddirection_10m",
        "wind_speed_unit": "kmh",
        "forecast_days":  3,
        "timezone":       "Europe/Madrid",
    })

    wind_hours, wind_speeds, wind_gusts, wind_dirs = [], [], [], []
    if wind_data and "hourly" in wind_data:
        wind_hours  = wind_data["hourly"].get("time", [])
        wind_speeds = wind_data["hourly"].get("windspeed_10m", [])
        wind_gusts  = wind_data["hourly"].get("windgusts_10m", [])
        wind_dirs   = wind_data["hourly"].get("winddirection_10m", [])

    if not wind_hours and not wave_hours:
        return result

    # ── 3. Finn "nå" (nærmeste time) ─────────────────────────────────────────
    now_cet = datetime.now(timezone(timedelta(hours=1)))
    now_str = now_cet.strftime("%Y-%m-%dT%H:00")

    now_wind_idx = next((i for i, t in enumerate(wind_hours) if t >= now_str), 0)
    now_wave_idx = next((i for i, t in enumerate(wave_hours) if t >= now_str), 0)

    def _safe(lst, idx):
        return round(float(lst[idx]), 1) if idx < len(lst) and lst[idx] is not None else None

    wind_now  = _safe(wind_speeds, now_wind_idx)
    gust_now  = _safe(wind_gusts,  now_wind_idx)
    dir_now   = _safe(wind_dirs,   now_wind_idx)
    wave_now  = _safe(wave_heights, now_wave_idx)

    result["wind_now_kmh"]      = wind_now
    result["wind_gust_now_kmh"] = gust_now
    result["wave_now_m"]        = wave_now

    # Vindretning som kompassretning
    if dir_now is not None:
        dirs = ["N","NNØ","NØ","ØNØ","Ø","ØSØ","SØ","SSØ","S","SSV","SV","VSV","V","VNV","NV","NNV"]
        result["wind_dir_now"] = dirs[int((dir_now + 11.25) / 22.5) % 16]

    # ── 4. Maks neste 48 timer ────────────────────────────────────────────────
    h48_wind = wind_speeds[now_wind_idx:now_wind_idx + 48]
    h48_gust = wind_gusts[now_wind_idx:now_wind_idx + 48]
    h48_wave = wave_heights[now_wave_idx:now_wave_idx + 48]

    result["wind_max_48h_kmh"] = round(max((v for v in h48_gust if v), default=0), 1)
    result["wave_max_48h_m"]   = round(max((v for v in h48_wave if v), default=0), 1)

    # ── 5. Levante-risiko (Ø-vind, 45°–135°, særlig farlig i sundet) ─────────
    h48_dirs = wind_dirs[now_wind_idx:now_wind_idx + 48]
    levante_hours = sum(1 for d in h48_dirs if d is not None and 45 <= d <= 135)
    result["levante_risk"] = levante_hours >= 6   # ≥6 timer østlig vind

    # ── 6. 12-punkts tidsserie (4-timers intervall) for grafen ───────────────
    forecast_pts = []
    for i in range(0, min(48, len(wind_hours) - now_wind_idx), 4):
        idx_w = now_wind_idx + i
        idx_v = now_wave_idx + i
        t_str = wind_hours[idx_w] if idx_w < len(wind_hours) else ""
        forecast_pts.append({
            "time":       t_str[-5:] if t_str else "",   # "HH:MM"
            "wind_kmh":   _safe(wind_speeds, idx_w),
            "gust_kmh":   _safe(wind_gusts,  idx_w),
            "wave_m":     _safe(wave_heights, idx_v),
        })
    result["forecast_hours"] = forecast_pts

    # ── 7. Statusklassifisering ───────────────────────────────────────────────
    max_wind = result["wind_max_48h_kmh"] or 0
    max_wave = result["wave_max_48h_m"]   or 0
    cur_wind = wind_now or 0
    cur_wave = wave_now or 0

    T = THRESHOLDS
    if cur_wind >= T["wind_suspend_kmh"] or cur_wave >= T["wave_suspend_m"]:
        status = "Suspensjon"
        alert  = "rød"
        reason_parts = []
        if cur_wind >= T["wind_suspend_kmh"]:
            reason_parts.append(f"Vind {cur_wind:.0f} km/t")
        if cur_wave >= T["wave_suspend_m"]:
            reason_parts.append(f"Bølger {cur_wave:.1f}m")
        reason = f"Nåværende: {', '.join(reason_parts)} — over suspensjonsgrense"

    elif max_wind >= T["wind_suspend_kmh"] or max_wave >= T["wave_suspend_m"]:
        status = "Suspensjon ventet"
        alert  = "rød"
        reason = f"Prognose: vind opptil {max_wind:.0f} km/t, bølger {max_wave:.1f}m neste 48t"

    elif cur_wind >= T["wind_delay_kmh"] or cur_wave >= T["wave_delay_m"]:
        status = "Forsinkelser"
        alert  = "gul"
        reason_parts = []
        if cur_wind >= T["wind_delay_kmh"]:
            reason_parts.append(f"vind {cur_wind:.0f} km/t")
        if cur_wave >= T["wave_delay_m"]:
            reason_parts.append(f"bølger {cur_wave:.1f}m")
        reason = f"Nåværende: {', '.join(reason_parts)}"

    elif max_wind >= T["wind_delay_kmh"] or max_wave >= T["wave_delay_m"]:
        status = "Forsinkelser mulig"
        alert  = "gul"
        reason = f"Prognose: vind opptil {max_wind:.0f} km/t, bølger {max_wave:.1f}m neste 48t"

    else:
        status = "Normal drift"
        alert  = "grønn"
        reason = f"Vind {cur_wind:.0f} km/t, bølger {cur_wave:.1f}m — innenfor normale driftsgrenser"

    if result["levante_risk"] and alert == "grønn":
        alert = "gul"
        reason += ". Levante (Ø-vind) varsel neste 48t."

    result["status"]        = status
    result["status_reason"] = reason
    result["alert_level"]   = alert

    logger.info(f"Gibraltar: {status} | vind={cur_wind}km/t bølger={cur_wave}m | alert={alert}")
    return result


def build_gibraltar_html(data: dict) -> str:
    """
    Bygger HTML-blokk for Gibraltar-sundet-seksjonen.
    Inkluderer nåstatus, 48t prognose og lenker.
    """
    if not data or data.get("alert_level") == "grå":
        return _build_gibraltar_error()

    alert      = data["alert_level"]
    status     = data["status"]
    reason     = data["status_reason"]
    wind_now   = data["wind_now_kmh"]
    gust_now   = data["wind_gust_now_kmh"]
    wind_dir   = data["wind_dir_now"] or "—"
    wave_now   = data["wave_now_m"]
    wind_max   = data["wind_max_48h_kmh"]
    wave_max   = data["wave_max_48h_m"]
    levante    = data["levante_risk"]
    pts        = data["forecast_hours"]

    # Fargeskjema
    colors = {
        "grønn": ("--success", "#166534", "🟢"),
        "gul":   ("--warning", "#92400e", "🟡"),
        "rød":   ("--danger",  "#991b1b", "🔴"),
    }
    color_var, text_color, emoji = colors.get(alert, ("--muted", "#374151", "⚪"))

    # Pill
    pill_cls = {"grønn": "pill-low", "gul": "pill-mod", "rød": "pill-high"}.get(alert, "pill-low")

    # Formater verdier
    def fv(v, unit="", fmt=".0f"):
        return f"{v:{fmt}}{unit}" if v is not None else "—"

    wind_str  = fv(wind_now, " km/t")
    gust_str  = fv(gust_now, " km/t")
    wave_str  = fv(wave_now, " m", ".1f")
    wmax_str  = fv(wind_max, " km/t")
    vmax_str  = fv(wave_max, " m", ".1f")

    # 48t prognose mini-grafer (vind og bølger)
    wind_bars = _build_mini_bars(pts, "wind_kmh", 0, 100, "wind")
    wave_bars = _build_mini_bars(pts, "wave_m",   0, 5,   "wave")
    time_labels = "".join(
        f'<div class="gib-time-label">{p["time"]}</div>' for p in pts
    )

    levante_badge = (
        '<span class="gib-badge gib-badge-warn">⚠️ Levante-risiko</span>'
        if levante else ""
    )

    # Terskelverdier for kontekst
    thresh_html = (
        '<div class="gib-thresholds">'
        '<span>Forsinkelser: >50 km/t eller >2m</span>'
        '<span>Suspensjon: >70 km/t eller >3.5m</span>'
        '</div>'
    )

    html = f"""
<div class="gibraltar-section">
  <div class="gibraltar-header" style="border-left: 4px solid var({color_var}, #22c55e);">
    <div class="gib-title-row">
      <div>
        <div class="gib-title">⛴️ Gibraltar-sundet — Tanger Med ↔ Algeciras</div>
        <div class="gib-subtitle">35°54'N 5°36'V · ~14 km bred · ~35 min overfartstid</div>
      </div>
      <span class="pill {pill_cls}">{emoji} {status}</span>
    </div>
    <div class="gib-reason">{reason}</div>
    {levante_badge}
  </div>

  <div class="gib-conditions-grid">
    <div class="gib-condition-card">
      <div class="gib-cond-label">Vind nå</div>
      <div class="gib-cond-value" style="color:{text_color};">{wind_str}</div>
      <div class="gib-cond-sub">Retning: {wind_dir}</div>
    </div>
    <div class="gib-condition-card">
      <div class="gib-cond-label">Vindkast nå</div>
      <div class="gib-cond-value" style="color:{text_color};">{gust_str}</div>
      <div class="gib-cond-sub">Maks 10 min</div>
    </div>
    <div class="gib-condition-card">
      <div class="gib-cond-label">Bølgehøyde nå</div>
      <div class="gib-cond-value" style="color:{text_color};">{wave_str}</div>
      <div class="gib-cond-sub">Signifikant Hs</div>
    </div>
    <div class="gib-condition-card gib-forecast-card">
      <div class="gib-cond-label">Maks neste 48t</div>
      <div class="gib-cond-value">{wmax_str} / {vmax_str}</div>
      <div class="gib-cond-sub">Vind / Bølger</div>
    </div>
  </div>

  <div class="gib-forecast">
    <div class="gib-forecast-title">48-timers prognose</div>
    <div class="gib-chart-row">
      <div class="gib-chart-label">Vind</div>
      <div class="gib-chart-bars">{wind_bars}</div>
    </div>
    <div class="gib-chart-row">
      <div class="gib-chart-label">Bølger</div>
      <div class="gib-chart-bars">{wave_bars}</div>
    </div>
    <div class="gib-chart-row">
      <div class="gib-chart-label"></div>
      <div class="gib-chart-bars gib-time-row">{time_labels}</div>
    </div>
    {thresh_html}
  </div>

  <div class="gib-links">
    <a href="https://www.tangermed-passagers.com/en/ferry-time-table/live-departures"
       target="_blank" class="gib-link">📋 Tanger Med live-avganger</a>
    <a href="https://ferryweather.com/strait-of-gibraltar/"
       target="_blank" class="gib-link">🌊 FerryWeather Gibraltar</a>
    <a href="https://gibraltarport.com/weather-and-tide"
       target="_blank" class="gib-link">⚓ Gibraltar Port vær</a>
    <a href="https://www.apba.es/en/port-services/port-operations"
       target="_blank" class="gib-link">🚢 APBA Algeciras driftsstatus</a>
  </div>
</div>"""

    return html


def _build_mini_bars(pts: list, key: str, min_val: float, max_val: float, kind: str) -> str:
    """Bygger 12 søyler for 48t prognose-grafen."""
    bars = []
    delay_thresh   = THRESHOLDS["wind_delay_kmh"]  if kind == "wind" else THRESHOLDS["wave_delay_m"]
    suspend_thresh = THRESHOLDS["wind_suspend_kmh"] if kind == "wind" else THRESHOLDS["wave_suspend_m"]

    for p in pts:
        v = p.get(key)
        if v is None:
            bars.append('<div class="gib-bar gib-bar-empty" style="height:4px;"></div>')
            continue
        height = max(4, int((v - min_val) / max(max_val - min_val, 1) * 36))
        if v >= suspend_thresh:
            cls = "gib-bar-red"
        elif v >= delay_thresh:
            cls = "gib-bar-yellow"
        else:
            cls = "gib-bar-green"
        unit = "km/t" if kind == "wind" else "m"
        bars.append(f'<div class="gib-bar {cls}" style="height:{height}px;" title="{v:.1f} {unit}"></div>')

    return "".join(bars)


def _build_gibraltar_error() -> str:
    return """
<div class="gibraltar-section gibraltar-error">
  <div class="gib-title">⛴️ Gibraltar-sundet — Tanger Med ↔ Algeciras</div>
  <div class="gib-reason">Kunne ikke hente maritimt vær — prøv igjen ved neste kjøring.</div>
  <div class="gib-links">
    <a href="https://www.tangermed-passagers.com/en/ferry-time-table/live-departures"
       target="_blank" class="gib-link">📋 Tanger Med live-avganger</a>
    <a href="https://ferryweather.com/strait-of-gibraltar/"
       target="_blank" class="gib-link">🌊 FerryWeather Gibraltar</a>
  </div>
</div>"""


if __name__ == "__main__":
    import json, logging
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    data = fetch_gibraltar_conditions()
    # Print summary without full forecast_hours
    summary = {k: v for k, v in data.items() if k != "forecast_hours"}
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"\nPrognose-punkter: {len(data['forecast_hours'])}")
    html = build_gibraltar_html(data)
    print(f"HTML generert: {len(html)} tegn")
