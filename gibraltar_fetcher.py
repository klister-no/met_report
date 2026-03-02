"""
gibraltar_fetcher.py
---------------------
Henter marint vær og vinddata for Gibraltar-sundet.

Datakilder (alle gratis, ingen API-nøkkel):
  1. Open-Meteo Marine API  — bølgehøyde, bølgeperiode (5km EU-modell, 3 dager)
  2. Open-Meteo Forecast API — vindstyrke, vindretning, vindkast (ECMWF)

Gibraltar-sundet koordinater: 35.90°N, -5.60°W
(Midtpunkt mellom Algeciras og Tanger Med)

Enheter: m/s primært (maritim standard), Beaufort-skala for kontekst.

Terskler for driftsavbrudd (basert på APBA/Baleària driftsregler):
  Gjennomsnittsvind:
    - > 13.9 m/s (6 Bf)  → forsinkelser sannsynlig
    - > 20.8 m/s (8 Bf)  → suspensjon sannsynlig
  Bølgehøyde:
    - > 2.0 m            → forsinkelser sannsynlig
    - > 3.5 m            → suspensjon sannsynlig

Tidligere feil: kode brukte km/t for terskler men viste "97 km/t" (= 27 m/s = Bf 10)
som virket feil — verdien var matematisk korrekt, men km/t er ikke maritim standard.
"""

import logging
import requests
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

MARINE_API   = "https://marine-api.open-meteo.com/v1/marine"
FORECAST_API = "https://api.open-meteo.com/v1/forecast"
TIMEOUT      = 20

GIBRALTAR_LAT = 35.90
GIBRALTAR_LON = -5.60

# Terskler i m/s (maritim standard)
THRESHOLDS = {
    "wind_delay_ms":     13.9,   # 6 Beaufort — liten kuling
    "wind_suspend_ms":   20.8,   # 8 Beaufort — sterk kuling
    "wave_delay_m":       2.0,
    "wave_suspend_m":     3.5,
}

# Beaufort-skala (m/s grenser og navn)
BEAUFORT = [
    (0,   0.3,  "Stille"),
    (1,   1.6,  "Flau"),
    (2,   3.4,  "Svak"),
    (3,   5.5,  "Lett bris"),
    (4,   8.0,  "Laber bris"),
    (5,  10.8,  "Frisk bris"),
    (6,  13.9,  "Liten kuling"),
    (7,  17.2,  "Stiv kuling"),
    (8,  20.8,  "Sterk kuling"),
    (9,  24.5,  "Liten storm"),
    (10, 28.5,  "Full storm"),
    (11, 32.7,  "Sterk storm"),
    (12, 99.0,  "Orkan"),
]

def _to_beaufort(ms: float) -> tuple[int, str]:
    for bf, upper, name in BEAUFORT:
        if ms < upper:
            return bf, name
    return 12, "Orkan"


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
    Alle vindverdier i m/s. Beaufort beregnes lokalt.
    """
    result = {
        "wind_now_ms":       None,   # gjennomsnittsvind m/s
        "wind_gust_now_ms":  None,   # vindkast m/s
        "wind_dir_now":      None,
        "wind_bf_now":       None,   # Beaufort-tall
        "wind_bf_name":      None,   # Beaufort-navn
        "wave_now_m":        None,
        "wave_max_48h_m":    None,
        "wind_max_48h_ms":   None,   # maks gjennomsnittsvind neste 48t
        "wind_gust_max_48h_ms": None,# maks vindkast neste 48t
        "forecast_hours":    [],
        "status":            "Ukjent",
        "status_reason":     "Ingen data",
        "alert_level":       "grå",
        "levante_risk":      False,
        "fetched_at":        datetime.now(timezone.utc).isoformat(),
    }

    # ── 1. Marine API — bølgehøyde ────────────────────────────────────────────
    marine_data = _request(MARINE_API, {
        "latitude":      GIBRALTAR_LAT,
        "longitude":     GIBRALTAR_LON,
        "hourly":        "wave_height,wave_period",
        "forecast_days": 3,
        "timezone":      "Europe/Madrid",
    })
    wave_hours, wave_heights = [], []
    if marine_data and "hourly" in marine_data:
        wave_hours   = marine_data["hourly"].get("time", [])
        wave_heights = marine_data["hourly"].get("wave_height", [])

    # ── 2. Forecast API — vind i m/s (standard, ingen wind_speed_unit-param) ──
    wind_data = _request(FORECAST_API, {
        "latitude":       GIBRALTAR_LAT,
        "longitude":      GIBRALTAR_LON,
        "hourly":         "windspeed_10m,windgusts_10m,winddirection_10m",
        "wind_speed_unit": "ms",   # eksplisitt m/s
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

    # ── 3. Finn "nå" ──────────────────────────────────────────────────────────
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

    result["wind_now_ms"]      = wind_now
    result["wind_gust_now_ms"] = gust_now
    result["wave_now_m"]       = wave_now

    if wind_now is not None:
        bf, name = _to_beaufort(wind_now)
        result["wind_bf_now"]  = bf
        result["wind_bf_name"] = name

    # Vindretning
    if dir_now is not None:
        dirs = ["N","NNØ","NØ","ØNØ","Ø","ØSØ","SØ","SSØ","S","SSV","SV","VSV","V","VNV","NV","NNV"]
        result["wind_dir_now"] = dirs[int((dir_now + 11.25) / 22.5) % 16]

    # ── 4. Maks neste 48 timer ────────────────────────────────────────────────
    h48_speed = wind_speeds[now_wind_idx:now_wind_idx + 48]
    h48_gust  = wind_gusts[now_wind_idx:now_wind_idx + 48]
    h48_wave  = wave_heights[now_wave_idx:now_wave_idx + 48]
    h48_dirs  = wind_dirs[now_wind_idx:now_wind_idx + 48]

    result["wind_max_48h_ms"]      = round(max((v for v in h48_speed if v), default=0), 1)
    result["wind_gust_max_48h_ms"] = round(max((v for v in h48_gust  if v), default=0), 1)
    result["wave_max_48h_m"]       = round(max((v for v in h48_wave  if v), default=0), 1)

    # ── 5. Levante-risiko ─────────────────────────────────────────────────────
    levante_hours = sum(1 for d in h48_dirs if d is not None and 45 <= d <= 135)
    result["levante_risk"] = levante_hours >= 6

    # ── 6. 12-punkts tidsserie (4t intervall) ────────────────────────────────
    pts = []
    for i in range(0, min(48, len(wind_hours) - now_wind_idx), 4):
        iw = now_wind_idx + i
        iv = now_wave_idx + i
        t  = wind_hours[iw][-5:] if iw < len(wind_hours) else ""
        ws = _safe(wind_speeds, iw)
        pts.append({
            "time":    t,
            "wind_ms": ws,
            "gust_ms": _safe(wind_gusts, iw),
            "wave_m":  _safe(wave_heights, iv),
            "bf":      _to_beaufort(ws)[0] if ws is not None else None,
        })
    result["forecast_hours"] = pts

    # ── 7. Statusklassifisering (basert på gjennomsnittsvind, ikke kast) ─────
    T        = THRESHOLDS
    cur_wind = wind_now or 0
    max_wind = result["wind_max_48h_ms"] or 0
    cur_wave = wave_now or 0
    max_wave = result["wave_max_48h_m"]  or 0

    def wind_desc(ms):
        bf, name = _to_beaufort(ms)
        return f"{ms:.1f} m/s ({name}, Bf {bf})"

    if cur_wind >= T["wind_suspend_ms"] or cur_wave >= T["wave_suspend_m"]:
        status = "Suspensjon"
        alert  = "rød"
        parts  = []
        if cur_wind >= T["wind_suspend_ms"]: parts.append(f"Vind {wind_desc(cur_wind)}")
        if cur_wave >= T["wave_suspend_m"]:  parts.append(f"Bølger {cur_wave:.1f}m")
        reason = f"Nåværende: {', '.join(parts)} — over suspensjonsgrense"

    elif max_wind >= T["wind_suspend_ms"] or max_wave >= T["wave_suspend_m"]:
        status = "Suspensjon ventet"
        alert  = "rød"
        reason = f"Prognose: vind opptil {wind_desc(max_wind)}, bølger {max_wave:.1f}m neste 48t"

    elif cur_wind >= T["wind_delay_ms"] or cur_wave >= T["wave_delay_m"]:
        status = "Forsinkelser"
        alert  = "gul"
        parts  = []
        if cur_wind >= T["wind_delay_ms"]: parts.append(f"vind {wind_desc(cur_wind)}")
        if cur_wave >= T["wave_delay_m"]:  parts.append(f"bølger {cur_wave:.1f}m")
        reason = f"Nåværende: {', '.join(parts)}"

    elif max_wind >= T["wind_delay_ms"] or max_wave >= T["wave_delay_m"]:
        status = "Forsinkelser mulig"
        alert  = "gul"
        reason = f"Prognose: vind opptil {wind_desc(max_wind)}, bølger {max_wave:.1f}m neste 48t"

    else:
        status = "Normal drift"
        alert  = "grønn"
        bf_now, _ = _to_beaufort(cur_wind) if cur_wind else (0, "")
        reason = f"Vind {cur_wind:.1f} m/s (Bf {bf_now}), bølger {cur_wave:.1f}m — innenfor driftsgrenser"

    if result["levante_risk"] and alert == "grønn":
        alert   = "gul"
        reason += " · Levante (Ø-vind) varslet neste 48t"

    result["status"]        = status
    result["status_reason"] = reason
    result["alert_level"]   = alert

    logger.info(f"Gibraltar: {status} | vind={cur_wind}m/s Bf{result['wind_bf_now']} bølger={cur_wave}m | alert={alert}")
    return result


def build_gibraltar_html(data: dict) -> str:
    if not data or data.get("alert_level") == "grå":
        return _build_gibraltar_error()

    alert     = data["alert_level"]
    status    = data["status"]
    reason    = data["status_reason"]
    wind_ms   = data["wind_now_ms"]
    gust_ms   = data["wind_gust_now_ms"]
    wind_dir  = data["wind_dir_now"] or "—"
    bf        = data["wind_bf_now"]
    bf_name   = data["wind_bf_name"] or "—"
    wave_now  = data["wave_now_m"]
    wind_max  = data["wind_max_48h_ms"]
    wave_max  = data["wave_max_48h_m"]
    levante   = data["levante_risk"]
    pts       = data["forecast_hours"]

    colors = {
        "grønn": ("--success", "#166534", "🟢"),
        "gul":   ("--warning", "#92400e", "🟡"),
        "rød":   ("--danger",  "#991b1b", "🔴"),
    }
    color_var, text_color, emoji = colors.get(alert, ("--muted", "#374151", "⚪"))
    pill_cls = {"grønn": "pill-low", "gul": "pill-mod", "rød": "pill-high"}.get(alert, "pill-low")

    def fms(v):
        if v is None: return "—"
        bf_v, _ = _to_beaufort(v)
        return f"{v:.1f} m/s · Bf {bf_v}"

    def fwave(v):
        return f"{v:.1f} m" if v is not None else "—"

    wind_str  = fms(wind_ms)
    gust_str  = fms(gust_ms)
    wmax_str  = f"{wind_max:.1f} m/s" if wind_max is not None else "—"
    gmax_str  = f"{data.get('wind_gust_max_48h_ms', 0):.1f} m/s" if data.get('wind_gust_max_48h_ms') else "—"
    vmax_str  = fwave(wave_max)
    wave_str  = fwave(wave_now)
    bf_str    = f"Bf {bf} — {bf_name}" if bf is not None else "—"

    wind_bars = _build_mini_bars(pts, "wind_ms", 0, 30, "wind")
    wave_bars = _build_mini_bars(pts, "wave_m",  0, 5,  "wave")
    time_labs = "".join(f'<div class="gib-time-label">{p["time"]}</div>' for p in pts)

    levante_badge = (
        '<span class="gib-badge gib-badge-warn">⚠️ Levante-risiko (Ø-vind)</span>'
        if levante else ""
    )

    return f"""
<div class="gibraltar-section">
  <div class="gibraltar-header" style="border-left:4px solid var({color_var},#22c55e);">
    <div class="gib-title-row">
      <div>
        <div class="gib-title">⛴️ Gibraltar-sundet — Tanger Med ↔ Algeciras</div>
        <div class="gib-subtitle">35°54'N 5°36'V · ~14 km bred · ~35 min overfartstid · ECMWF-modell</div>
      </div>
      <span class="pill {pill_cls}">{emoji} {status}</span>
    </div>
    <div class="gib-reason">{reason}</div>
    {levante_badge}
  </div>

  <div class="gib-conditions-grid">
    <div class="gib-condition-card">
      <div class="gib-cond-label">Gjennomsnittsvind nå</div>
      <div class="gib-cond-value" style="color:{text_color};">{wind_str}</div>
      <div class="gib-cond-sub">Retning: {wind_dir} · {bf_str}</div>
    </div>
    <div class="gib-condition-card">
      <div class="gib-cond-label">Vindkast nå</div>
      <div class="gib-cond-value" style="color:{text_color};">{gust_str}</div>
      <div class="gib-cond-sub">Maks 3-sek gjennomsnitt (WMO)</div>
    </div>
    <div class="gib-condition-card">
      <div class="gib-cond-label">Bølgehøyde nå</div>
      <div class="gib-cond-value" style="color:{text_color};">{wave_str}</div>
      <div class="gib-cond-sub">Signifikant Hs (ECMWF WAM)</div>
    </div>
    <div class="gib-condition-card">
      <div class="gib-cond-label">Maks neste 48t</div>
      <div class="gib-cond-value">{wmax_str} / {vmax_str}</div>
      <div class="gib-cond-sub">Snitt-vind / Bølger · Kast: {gmax_str}</div>
    </div>
  </div>

  <div class="gib-forecast">
    <div class="gib-forecast-title">48-timers prognose (gjennomsnittsvind og bølger)</div>
    <div class="gib-chart-row">
      <div class="gib-chart-label">Vind m/s</div>
      <div class="gib-chart-bars">{wind_bars}</div>
    </div>
    <div class="gib-chart-row">
      <div class="gib-chart-label">Bølger m</div>
      <div class="gib-chart-bars">{wave_bars}</div>
    </div>
    <div class="gib-chart-row">
      <div class="gib-chart-label"></div>
      <div class="gib-chart-bars gib-time-row">{time_labs}</div>
    </div>
    <div class="gib-thresholds">
      <span>🟡 Forsinkelser: vind &gt;13.9 m/s (Bf 6) eller bølger &gt;2m</span>
      <span>🔴 Suspensjon: vind &gt;20.8 m/s (Bf 8) eller bølger &gt;3.5m</span>
    </div>
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


def _build_mini_bars(pts, key, min_val, max_val, kind):
    T = THRESHOLDS
    delay_t   = T["wind_delay_ms"]   if kind == "wind" else T["wave_delay_m"]
    suspend_t = T["wind_suspend_ms"] if kind == "wind" else T["wave_suspend_m"]
    bars = []
    for p in pts:
        v = p.get(key)
        if v is None:
            bars.append('<div class="gib-bar gib-bar-empty" style="height:4px;"></div>')
            continue
        height = max(4, int((v - min_val) / max(max_val - min_val, 1) * 36))
        cls = ("gib-bar-red" if v >= suspend_t
               else "gib-bar-yellow" if v >= delay_t
               else "gib-bar-green")
        unit = "m/s" if kind == "wind" else "m"
        bars.append(f'<div class="gib-bar {cls}" style="height:{height}px;" title="{v:.1f} {unit}"></div>')
    return "".join(bars)


def _build_gibraltar_error():
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


def build_gibraltar_bar(data: dict) -> str:
    alert   = data.get("alert_level", "grå")
    status  = data.get("status", "Ukjent")
    wind_ms = data.get("wind_now_ms")
    gust_ms = data.get("wind_gust_now_ms")
    wave    = data.get("wave_now_m")
    levante = data.get("levante_risk", False)
    bf      = data.get("wind_bf_now")

    bar_cls  = {"grønn": "gib-bar-green", "gul": "gib-bar-yellow",
                "rød":   "gib-bar-red",   "grå": "gib-bar-unknown"}.get(alert, "gib-bar-unknown")
    pill_cls = {"grønn": "pill-low", "gul": "pill-mod",
                "rød":   "pill-high", "grå": "pill-low"}.get(alert, "pill-low")
    emoji    = {"grønn": "🟢", "gul": "🟡", "rød": "🔴", "grå": "⚪"}.get(alert, "⚪")

    parts = []
    if wind_ms is not None:
        parts.append(f"Vind {wind_ms:.1f} m/s (Bf {bf})" if bf else f"Vind {wind_ms:.1f} m/s")
    if gust_ms is not None:
        parts.append(f"kast {gust_ms:.1f} m/s")
    if wave is not None:
        parts.append(f"bølger {wave:.1f}m")
    if levante:
        parts.append("⚠️ Levante")
    detail = " · ".join(parts) if parts else "Ingen data"

    return (
        f'<div class="gib-status-bar {bar_cls}">'
        f'<span class="gib-bar-icon">⛴️</span>'
        f'<span class="gib-bar-label">Gibraltar-sundet</span>'
        f'<span class="pill {pill_cls}">{emoji} {status}</span>'
        f'<span class="gib-bar-detail">{detail}</span>'
        f'<a href="#" class="gib-bar-link" '
        f'onclick="document.querySelector(\'[data-tab=ports]\').click();return false;">'
        f'→ Detaljer</a>'
        f'</div>'
    )


if __name__ == "__main__":
    import json, logging
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    data = fetch_gibraltar_conditions()
    summary = {k: v for k, v in data.items() if k != "forecast_hours"}
    print(json.dumps(summary, indent=2, ensure_ascii=False))
