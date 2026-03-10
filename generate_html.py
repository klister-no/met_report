"""
generate_html.py
----------------
Henter vaerdata, analyserer og skriver oppdatert index.html.
Alle build_*-funksjoner er definert foer update_html og main().
"""

import argparse
import logging
import os
import re
import sys
from datetime import date, timedelta, datetime, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent / "src"))

from data_fetcher    import fetch_all_regions
from analyzer        import analyze_all
from narrative       import generate_all_narratives
from news_fetcher    import fetch_news, render_news_html
from norway_fetcher  import fetch_all_norway_regions
from ecmwf_fetcher   import fetch_ecmwf_all_regions, EUROPE_REGIONS, NORWAY_REGIONS

sys.path.insert(0, str(Path(__file__).parent))
from generate_norway_html import inject_norway_into_html
from generate_ecmwf_html  import inject_ecmwf_into_html
from gibraltar_fetcher    import fetch_gibraltar_conditions, build_gibraltar_html, build_gibraltar_bar

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("generate_html")


# =============================================================================
# ARGUMENT PARSING + WEEK BOUNDS
# =============================================================================

def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--week", type=str, default=None,
                   help="Uke-startdato YYYY-MM-DD. Standard: siste fullfoerte uke.")
    p.add_argument("--no-cache", action="store_true")
    return p.parse_args()


def get_week_bounds(override=None):
    if override:
        start = date.fromisoformat(override)
    else:
        today = date.today()
        start = today - timedelta(days=today.weekday() + 7)
    end = start + timedelta(days=6)
    return start, end


# =============================================================================
# HTML HELPERS
# =============================================================================

def risk_pill(level: str) -> str:
    cls = {"Hoey": "pill-high", "Moderat": "pill-mod", "Lav": "pill-low"}.get(level, "pill-low")
    # Handle Norwegian characters
    if level == "Høy":
        cls = "pill-high"
    return f'<span class="pill {cls}">{level}</span>'


def status_icon(status: str) -> str:
    return f'<span class="status-icon">{status}</span>'


def anom_class(val):
    """Generell avviksklasse — positiv=blå (overskudd), negativ=oransje (underskudd)."""
    if val is None:
        return "anom-neu"
    return "anom-pos" if val > 0 else "anom-neg" if val < 0 else "anom-neu"


def temp_anom_class(val):
    """Temperaturavvik — varm=rød, kald=blå (standard klimakart-konvensjon)."""
    if val is None:
        return "anom-neu"
    return "anom-warm" if val > 0 else "anom-cool" if val < 0 else "anom-neu"


def fmt(val, suffix="°C", decimals=1):
    if val is None:
        return "N/A"
    return f"{val:+.{decimals}f}{suffix}" if val != 0 else f"0{suffix}"


def fmt_range(val, suffix="°C"):
    if val is None:
        return "N/A"
    return f"{val:.1f}{suffix}"


# =============================================================================
# BUILD FUNCTIONS — all defined here, before update_html
# =============================================================================

def build_kpi_sub(analyses: list, level: str) -> str:
    """Return comma-separated short region names for a given risk level."""
    regions = [a for a in analyses if a.get("risk_total") == level]
    if not regions:
        if level == "Høy":
            return "Ingen høy-risiko regioner"
        if level == "Moderat":
            return "Ingen moderat-risiko regioner"
        if level == "Lav":
            return "Alle regioner normale"
    names = []
    for a in regions:
        rn = a["region_name"]
        short = rn.split("–")[-1].strip() if "–" in rn else rn
        short = short.replace(" / Amalfi", "").replace(" (nord)", "").replace(" (ref.)", "")
        names.append(short)
    return ", ".join(names)


def build_alert_box(analyses: list, week_num: int, year: int) -> str:
    """Generate dynamic alert box based on current risk levels."""
    high = [a for a in analyses if a.get("risk_total") == "Høy"]
    mod  = [a for a in analyses if a.get("risk_total") == "Moderat"]

    if not high and not mod:
        return (
            '<div class="alert-box success">'
            '<strong>✅ NORMAL FORSYNINGSSITUASJON — UKE {w}/{y}</strong> '
            'Alle regioner er innenfor normale parametere. Ingen aktive forstyrrelser.'
            '</div>'
        ).format(w=week_num, y=year)

    if not high and mod:
        mod_names = ", ".join(
            a["region_name"].split("–")[-1].strip() if "–" in a["region_name"]
            else a["region_name"] for a in mod
        )
        return (
            f'<div class="alert-box warning">'
            f'<strong>⚡ MODERAT FORSYNINGSRISIKO — UKE {week_num}/{year}</strong> '
            f'{len(mod)} region(er) med moderat risiko: {mod_names}. '
            f'Ingen kritiske forstyrrelser registrert.'
            f'</div>'
        )

    high_names = ", ".join(
        a["region_name"].split("–")[-1].strip() if "–" in a["region_name"]
        else a["region_name"] for a in high
    )
    count = len(high)
    simultaneous = "simultant " if count >= 3 else ""

    obs_lines = []
    for a in high:
        rn = a["region_name"].split("–")[-1].strip() if "–" in a["region_name"] else a["region_name"]
        pct = a.get("precip_anomaly_pct")
        td  = a.get("temp_day_anomaly")
        parts = []
        if pct is not None and abs(pct) > 50:
            parts.append(f"nedbør {pct:+.0f}% vs. norm")
        if td is not None and abs(td) > 1.5:
            parts.append(f"temp {td:+.1f}°C vs. norm")
        if parts:
            obs_lines.append(f"{rn}: {', '.join(parts)}")

    obs_text = ". ".join(obs_lines) + "." if obs_lines else ""
    severity = "KRITISK" if count >= 3 else "HØYRISIKOVARSEL"
    box_class = "critical" if count >= 3 else "warning"

    return (
        f'<div class="alert-box {box_class}">'
        f'<strong>⚠️ {severity} FORSYNINGSSITUASJON — UKE {week_num}/{year}</strong> '
        f'{count} region(er) {simultaneous}med høy risiko: {high_names}. '
        f'{obs_text}'
        f'</div>'
    )


def build_sc_observation_cards(analyses: list) -> str:
    """Generate observation cards based on data only — no product assumptions."""
    high = [a for a in analyses if a.get("risk_total") == "Høy"]
    mod  = [a for a in analyses if a.get("risk_total") == "Moderat"]
    low  = [a for a in analyses if a.get("risk_total") == "Lav"]
    cards = []

    for a in high:
        rn   = a["region_name"].split("–")[-1].strip() if "–" in a["region_name"] else a["region_name"]
        cc   = a["region_id"].split("_")[0]
        flag = {"IT": "🇮🇹", "ES": "🇪🇸", "PT": "🇵🇹", "MA": "🇲🇦"}.get(cc, "🌍")
        obs  = []
        pct  = a.get("precip_anomaly_pct")
        td   = a.get("temp_day_anomaly")
        tn   = a.get("temp_night_anomaly")
        if pct is not None:
            obs.append(f"Nedbørsavvik: {pct:+.0f}% vs. normalperiode")
        if td is not None:
            obs.append(f"Dagtemperatur: {td:+.1f}°C vs. normal")
        if tn is not None:
            obs.append(f"Nattetemperatur: {tn:+.1f}°C vs. normal")
        if a.get("risk_transport") == "Høy":
            obs.append("Transportrisiko: Høy")
        note = a.get("forecast_precip_note", "")
        if note:
            obs.append(note)
        obs_html = "".join(f"<li>{o}</li>" for o in obs) if obs else "<li>Ingen detaljdata</li>"
        cards.append(
            f'<div class="info-card" style="border-left:3px solid #ef4444;">'
            f'<span class="info-card-icon">{flag} ⚠️</span>'
            f'<div class="info-card-title">{rn} — Høy risiko</div>'
            f'<div class="info-card-body"><ul style="margin:0 0 0 1rem;font-size:12px;">{obs_html}</ul></div>'
            f'</div>'
        )

    for a in mod:
        rn   = a["region_name"].split("–")[-1].strip() if "–" in a["region_name"] else a["region_name"]
        cc   = a["region_id"].split("_")[0]
        flag = {"IT": "🇮🇹", "ES": "🇪🇸", "PT": "🇵🇹", "MA": "🇲🇦"}.get(cc, "🌍")
        obs  = []
        pct  = a.get("precip_anomaly_pct")
        td   = a.get("temp_day_anomaly")
        if pct is not None:
            obs.append(f"Nedbørsavvik: {pct:+.0f}% vs. normalperiode")
        if td is not None:
            obs.append(f"Dagtemperatur: {td:+.1f}°C vs. normal")
        obs_html = "".join(f"<li>{o}</li>" for o in obs) if obs else "<li>Ingen detaljdata</li>"
        cards.append(
            f'<div class="info-card" style="border-left:3px solid #f97316;">'
            f'<span class="info-card-icon">{flag} ⚡</span>'
            f'<div class="info-card-title">{rn} — Moderat risiko</div>'
            f'<div class="info-card-body"><ul style="margin:0 0 0 1rem;font-size:12px;">{obs_html}</ul></div>'
            f'</div>'
        )

    if low:
        low_names = ", ".join(
            a["region_name"].split("–")[-1].strip() if "–" in a["region_name"]
            else a["region_name"] for a in low
        )
        cards.append(
            f'<div class="info-card" style="border-left:3px solid #22c55e;">'
            f'<span class="info-card-icon">✅</span>'
            f'<div class="info-card-title">Normale regioner ({len(low)})</div>'
            f'<div class="info-card-body" style="font-size:12px;">{low_names}</div>'
            f'</div>'
        )

    if len(high) >= 2:
        cards.append(
            f'<div class="info-card" style="border-left:3px solid #dc2626;background:#fff5f5;">'
            f'<span class="info-card-icon">⚠️</span>'
            f'<div class="info-card-title">Simultankrise — {len(high)} regioner</div>'
            f'<div class="info-card-body" style="font-size:12px;">'
            f'{len(high)} høy-risiko regioner aktive samtidig. Se tabellene for detaljer.'
            f'</div></div>'
        )

    if not cards:
        cards.append(
            '<div class="info-card" style="border-left:3px solid #22c55e;">'
            '<span class="info-card-icon">✅</span>'
            '<div class="info-card-title">Alle regioner — Normal status</div>'
            '<div class="info-card-body" style="font-size:12px;">Ingen avvik registrert.</div>'
            '</div>'
        )

    return '<div class="info-grid mt-2">\n' + "\n".join(cards) + "\n</div>"


def _short_region_name(region_name: str) -> str:
    """Extract short display name — strips country prefix, parentheses suffix."""
    if "–" in region_name:
        short = region_name.split("–", 1)[1].strip()
    else:
        short = region_name
    # Fjern parentes-suffix som "(Gandia–Oliva)", "(ref.)", "(nord)" osv.
    short = re.sub(r"\s*\([^)]*\)\s*$", "", short).strip()
    return short


def build_overview_rows(analyses: list) -> str:
    rows = []
    for a in analyses:
        rn  = a["region_name"]
        cc  = a["region_id"].split("_")[0]
        country_flag = {"IT": "🇮🇹 Italia", "ES": "🇪🇸 Spania",
                        "PT": "🇵🇹 Portugal", "MA": "🇲🇦 Marokko"}
        flag   = country_flag.get(cc, cc)
        status = a.get("temp_status", "🟢")
        pct    = a.get("precip_anomaly_pct")
        pct_str = f"{pct:+.0f}%" if pct is not None else "N/A"
        pct_cls = ("anom-pos" if pct and pct > 150
                   else "anom-neg" if pct and pct < -20
                   else "anom-neu")

        # Kun rød bakgrunn ved reelt avvik (>+80% eller temp >+2°C over normalt)
        t_anom = a.get("temp_day_anomaly")
        is_exceptional = (
            (pct is not None and abs(pct) > 80) or
            (t_anom is not None and abs(t_anom) > 2.0)
        )
        bg = ' style="background:#fff5f5;"' if (a.get("risk_total") == "Høy" and is_exceptional) else ""
        short = _short_region_name(rn)

        # Vind — PROGNOSE neste 7 dager (ikke observasjon)
        gust = a.get("wind_gust_max_7d_ms")
        if gust is None:
            gust_str = "—"
            gust_cls = ""
        elif gust > 20.0:
            gust_str = f"💨 {gust:.1f}"
            gust_cls = "wind-high"
        elif gust > 15.0:
            gust_str = f"💨 {gust:.1f}"
            gust_cls = "wind-mod"
        else:
            gust_str = f"{gust:.1f}"
            gust_cls = ""

        rows.append(
            f'<tr{bg}>'
            f'<td><span class="region-name">{short}</span><span class="region-country">{cc}</span></td>'
            f'<td>{flag}</td>'
            f'<td>{status_icon(status)}</td>'
            f'<td class="{pct_cls}">{pct_str}</td>'
            f'<td>{risk_pill(a.get("risk_temp","Lav"))}</td>'
            f'<td>{risk_pill(a.get("risk_precip","Lav"))}</td>'
            f'<td class="{gust_cls}" style="font-variant-numeric:tabular-nums;" '
            f'title="Maks vindkast i prognose, neste 7 dager (m/s)">{gust_str}</td>'
            f'<td>{risk_pill(a.get("risk_transport","Lav"))}</td>'
            f'<td>{risk_pill(a.get("risk_production","Lav"))}</td>'
            f'<td>{risk_pill(a.get("risk_total","Lav"))}</td>'
            f'</tr>'
        )
    return "\n".join(rows)


def build_temp_sparkline(daily: list) -> str:
    """7-søylers CSS-sparkline for daglig temperatur. Rød=varm, blå=kald."""
    if not daily:
        return '<div class="sparkbar-wrap"><span style="font-size:10px;color:#aaa;">—</span></div>'
    bars = []
    for d in daily[-7:]:
        mean = d.get("temp_mean")
        if mean is None:
            bars.append('<div class="sparkbar sparkbar-neu" style="height:6px;" title="Ingen data"></div>')
        else:
            height = min(28, max(8, int(abs(mean) * 1.5)))
            cls = ("sparkbar-high" if mean > 15
                   else "sparkbar-mod" if mean > 5
                   else "sparkbar-cool" if mean > 0
                   else "sparkbar-frost")
            date_str = d.get("date", "")
            bars.append(f'<div class="sparkbar {cls}" style="height:{height}px;" title="{date_str}: {mean:.1f}°C"></div>')
    return '<div class="sparkbar-wrap">' + "".join(bars) + "</div>"


def build_temp_rows(analyses: list) -> str:
    """
    Temperatur-tabell med to tidsnivåer:
    Dagens obs (dag/natt) + 7-dagers snitt/avvik/sparkline.
    """
    rows = []
    for a in analyses:
        rn  = a["region_name"]
        cc  = a["region_id"].split("_")[0]
        da  = a.get("temp_day_actual")
        dn  = a.get("temp_day_normal")
        dd  = a.get("temp_day_anomaly")
        na  = a.get("temp_night_actual")
        nn  = a.get("temp_night_normal")
        nd  = a.get("temp_night_anomaly")
        t7  = a.get("temp_7d_avg")
        t7d = a.get("temp_7d_anomaly")
        t7_str  = f"{t7:.1f}°C"   if t7  is not None else "—"
        t7d_str = f"{t7d:+.1f}°C" if t7d is not None else "—"
        t7_spark = build_temp_sparkline(a.get("temp_7d_daily", []))
        status = a.get("temp_status", "🟢")
        short  = rn.split("–")[-1].strip() if "–" in rn else rn
        rows.append(
            f'<tr>'
            f'<td><span class="region-name">{short}</span><span class="region-country">{cc}</span></td>'
            f'<td class="mono">{fmt_range(da)}</td>'
            f'<td class="mono">{fmt_range(dn)}</td>'
            f'<td class="{temp_anom_class(dd)}">{fmt(dd)}</td>'
            f'<td class="mono">{fmt_range(na)}</td>'
            f'<td class="mono">{fmt_range(nn)}</td>'
            f'<td class="{temp_anom_class(nd)}">{fmt(nd)}</td>'
            f'<td class="mono">{t7_str}</td>'
            f'<td class="{temp_anom_class(t7d)}">{t7d_str}</td>'
            f'<td>{t7_spark}</td>'
            f'<td>{status_icon(status)}</td>'
            f'</tr>'
        )
    return "\n".join(rows)


def build_precip_rows(analyses: list) -> str:
    rows = []
    for a in analyses:
        rn   = a["region_name"]
        cc   = a["region_id"].split("_")[0]
        obs  = a.get("precip_actual_mm")
        norm = a.get("precip_normal_mm")
        anom = a.get("precip_anomaly_mm")
        pct  = a.get("precip_anomaly_pct")
        rp   = a.get("risk_precip", "Lav")
        note = a.get("forecast_precip_note", "")
        pct_cls = ("pill-high" if pct and pct > 150
                   else "pill-mod" if pct and pct > 50
                   else "pill-cool" if pct and pct < -20
                   else "pill-low")
        bg       = ' style="background:#fff5f5;"' if rp == "Høy" else ""
        obs_str  = f"{obs:.1f}"   if obs  is not None else "N/A"
        norm_str = f"{norm:.1f}"  if norm is not None else "N/A"
        anom_str = f"{anom:+.1f}" if anom is not None else "N/A"
        pct_str  = f"{pct:+.0f}%" if pct  is not None else "N/A"
        short    = rn.split("–")[-1].strip() if "–" in rn else rn
        rows.append(
            f'<tr{bg}>'
            f'<td><span class="region-name">{short}</span><span class="region-country">{cc}</span></td>'
            f'<td class="mono">{obs_str}</td>'
            f'<td class="mono">{norm_str}</td>'
            f'<td class="{anom_class(anom)}">{anom_str}</td>'
            f'<td><span class="pill {pct_cls}">{pct_str}</span></td>'
            f'<td>{risk_pill(rp)}</td>'
            f'<td style="font-size:11px;text-align:left;">{note}</td>'
            f'</tr>'
        )
    return "\n".join(rows)


def build_supply_chain_rows(analyses: list) -> str:
    rows = []
    for a in analyses:
        rn    = a["region_name"]
        cc    = a["region_id"].split("_")[0]
        bg    = ' style="background:#fff5f5;"' if a.get("risk_total") == "Høy" else ""
        short = rn.split("–")[-1].strip() if "–" in rn else rn

        # Vind — vis m/s + risikopille
        gust  = a.get("wind_gust_max_7d_ms")
        gust_str  = f"💨 {gust:.1f} m/s" if gust is not None else "—"
        wind_cls  = ("wind-high" if gust and gust > 20
                     else "wind-mod" if gust and gust > 15
                     else "")

        # Alerts (fremtidsvarsel)
        alerts = a.get("alerts_7d", [])
        alerts_html = (
            " ".join(f'<span class="alert-badge">{al}</span>' for al in alerts)
            if alerts else "—"
        )

        rows.append(
            f'<tr{bg}>'
            f'<td><span class="region-name">{short}</span><span class="region-country">{cc}</span></td>'
            f'<td>{risk_pill(a.get("risk_temp","Lav"))}</td>'
            f'<td>{risk_pill(a.get("risk_precip","Lav"))}</td>'
            f'<td>{risk_pill(a.get("risk_drought","Lav"))}</td>'
            f'<td><span class="{wind_cls}">{gust_str}</span></td>'
            f'<td>{risk_pill(a.get("risk_wind","Lav"))}</td>'
            f'<td>{risk_pill(a.get("risk_transport","Lav"))}</td>'
            f'<td>{risk_pill(a.get("risk_production","Lav"))}</td>'
            f'<td>{risk_pill(a.get("risk_total","Lav"))}</td>'
            f'<td style="font-size:11px;max-width:160px;">{alerts_html}</td>'
            f'</tr>'
        )
    return "\n".join(rows)


def build_precip_30d_sparkline(daily_30d: list, normal_mm_per_day: float) -> str:
    """
    30 daglige søyler med nedbør (mm) + horisontal normallinje.
    Søylefarger: Blå = normal, Oransje = 1.5-3x normal, Rød = >3x normal, Grå = tørr.
    Normallinje som CSS border-top.
    """
    if not daily_30d:
        return '<div class="sparkbar-wrap"><span style="font-size:10px;color:#aaa;">Ingen 30d-data</span></div>'

    max_precip = max((d.get("precip_mm", 0) or 0) for d in daily_30d)
    if max_precip == 0:
        max_precip = normal_mm_per_day * 3 or 5.0

    # Scale: max bar = 32px
    scale = 32.0 / max(max_precip, 0.1)
    normal_height = min(32, max(2, int(normal_mm_per_day * scale)))

    bars = []
    for d in daily_30d[-30:]:
        p = d.get("precip_mm", 0) or 0
        height = max(2, int(p * scale))
        date_str = d.get("date", "")
        ratio = p / normal_mm_per_day if normal_mm_per_day > 0 else 0

        if p < 0.5:
            cls = "sparkbar-dry"
        elif ratio > 3:
            cls = "sparkbar-extreme"
        elif ratio > 1.5:
            cls = "sparkbar-wet"
        else:
            cls = "sparkbar-normal"

        bars.append(
            f'<div class="sparkbar {cls}" '
            f'style="height:{height}px;" '
            f'title="{date_str}: {p:.1f}mm (norm {normal_mm_per_day:.1f}mm/dag)"></div>'
        )

    bars_html = "".join(bars)
    # Wrapper with normallinje as pseudo-element via inline style
    return (
        f'<div class="sparkbar-wrap sparkbar-precip" '
        f'style="--normal-h:{normal_height}px;" '
        f'title="Normallinje: {normal_mm_per_day:.1f}mm/dag">'
        f'{bars_html}'
        f'</div>'
    )


def build_sparkline(history: list) -> str:
    """Fallback 4-ukers risikohistorikk sparkline (brukes i trend-tabell)."""
    if not history:
        return '<div class="sparkbar-wrap"><span style="font-size:10px;color:var(--muted);">Baseline</span></div>'
    bars = []
    max_weeks = 4
    padded = ([None] * (max_weeks - len(history))) + history[-max_weeks:]
    for h in padded:
        if h is None:
            bars.append('<div class="sparkbar sparkbar-neu" style="height:6px;" title="Ingen data"></div>')
        else:
            risk   = h.get("risk_total", "Lav")
            pct    = h.get("precip_anomaly_pct", 0) or 0
            height = min(28, max(4, int(abs(pct) / 20)))
            cls    = {"Høy": "sparkbar-high", "Moderat": "sparkbar-mod", "Lav": "sparkbar-low"}.get(risk, "sparkbar-neu")
            week   = h.get("week", "")
            bars.append(f'<div class="sparkbar {cls}" style="height:{height}px;" title="Uke {week}: {risk}, nedbør {pct:+.0f}%"></div>')
    return '<div class="sparkbar-wrap">' + "".join(bars) + "</div>"


def build_trend_rows(analyses: list) -> str:
    """
    Trendlinje-tabell. Nedbørs-kolonnen viser nå 30-dagers daglige søyler
    med normallinje — én graf per region.
    """
    rows = []
    for a in analyses:
        rn      = a["region_name"]
        cc      = a["region_id"].split("_")[0]
        trend   = a.get("trend_direction", "→")
        history = a.get("trend_history", [])
        trend_cls   = "trend-up" if trend == "↑" else "trend-down" if trend == "↓" else "trend-flat"
        trend_label = {"↑": "↑ Stigende", "↓": "↓ Fallende", "→": "→ Stabil"}.get(trend, "→ Stabil")

        n = len(history)
        if n >= 2:
            risks = [h.get("risk_total", "Lav") for h in history]
            if risks.count("Høy") >= 2:
                consensus = "⚠️ Vedvarende høy risiko"
            elif "Høy" in risks[-2:]:
                consensus = "↑ Risiko øker"
            elif risks[-1] == "Lav" and "Høy" in risks[:-1]:
                consensus = "↓ Risiko avtar"
            else:
                consensus = "→ Stabil"
        else:
            # Uke 1: vis situasjonsbeskrivelse basert på faktisk data
            risk_now  = a.get("risk_total", "Lav")
            pct_30    = a.get("precip_30d_anomaly_pct")
            t7        = a.get("temp_7d_anomaly")
            parts     = []
            if pct_30 is not None:
                if pct_30 > 150:
                    parts.append(f"Nedbør +{pct_30:.0f}% (30d)")
                elif pct_30 < -40:
                    parts.append(f"Tørke {pct_30:.0f}% (30d)")
                elif abs(pct_30) > 30:
                    parts.append(f"Nedbør {pct_30:+.0f}% (30d)")
            if t7 is not None and abs(t7) > 1.0:
                parts.append(f"Temp {t7:+.1f}°C (7d)")
            if parts:
                consensus = " · ".join(parts)
            else:
                consensus = f"Risiko: {risk_now}"

        # 30-dagers nedbørsgraf
        daily_30d      = a.get("precip_30d_daily", [])
        normal_30d_tot = a.get("precip_30d_normal_mm", 60.0)
        normal_per_day = round(normal_30d_tot / 30, 2) if normal_30d_tot else 2.0
        precip_30d_pct = a.get("precip_30d_anomaly_pct")
        pct_label      = f"{precip_30d_pct:+.0f}% (30d)" if precip_30d_pct is not None else "—"
        pct_cls        = anom_class(precip_30d_pct)
        precip_spark   = build_precip_30d_sparkline(daily_30d, normal_per_day)

        # Temp-avvik — bruk eksplisitt None-sjekk, ikke 'or' (0.0 er gyldig verdi)
        t7_anom  = a.get("temp_7d_anomaly")
        t1_anom  = a.get("temp_day_anomaly")
        anom_d   = t7_anom if t7_anom is not None else t1_anom
        if t7_anom is not None:
            avvik = f"{t7_anom:+.1f}°C (7d)"
        elif t1_anom is not None:
            avvik = f"{t1_anom:+.1f}°C"
        else:
            avvik = "Baseline"
        avc = anom_class(anom_d)

        short = rn.split("–")[-1].strip() if "–" in rn else rn
        rows.append(
            f'<tr>'
            f'<td><span class="region-name">{short}</span><span class="region-country">{cc}</span></td>'
            f'<td><span class="{trend_cls}">{trend_label}</span></td>'
            f'<td>{precip_spark}</td>'
            f'<td class="{pct_cls}" style="font-size:11px;white-space:nowrap;">{pct_label}</td>'
            f'<td>{consensus}</td>'
            f'<td class="{avc}">{avvik}</td>'
            f'</tr>'
        )
    return "\n".join(rows)


def _risk_color(level: str) -> str:
    return {"Høy": "#ef4444", "Moderat": "#f97316", "Lav": "#22c55e"}.get(level, "#6b7280")


def _risk_bg(level: str) -> str:
    return {"Høy": "#fff5f5", "Moderat": "#fff7ed", "Lav": "#f0fdf4"}.get(level, "#f9fafb")


# Regioner som vises i obs+prognose-sammenstillingen (prioriterte nøkkelregioner)
_SUMMARY_REGIONS = [
    "IT_PIEDMONT", "IT_ALTO_ADIGE", "IT_CENTRAL", "IT_NAPLES", "IT_AMALFI",
    "ES_MURCIA_LORCA", "ES_MURCIA_CAMPO", "ES_CASTELLON",
    "ES_VALENCIA_NORTE", "ES_VALENCIA_SUR",
    "ES_ALMERIA", "ES_HUELVA", "ES_SEVILLA",
    "PT_LISBON_ALGARVE", "MA_NORTH_RABAT", "MA_SOUTH_AGADIR",
]


def build_obs_forecast_summary(analyses: list) -> str:
    """
    Kompakt sammenstilling per region: observert forrige uke + prognose neste 7 dager.
    Viser kun faktiske avvik — ingen unødvendige farger for normalvariasjon.
    Rød tekst = eksepsjonelt avvik (>+80% nedbør eller >±2°C temperatur).
    """
    if not analyses:
        return ""

    rows = []
    for a in analyses:
        rid   = a.get("region_id", "")
        rn    = a.get("region_name", rid)
        short = _short_region_name(rn)
        cc    = rid.split("_")[0]

        # ── Obs forrige uke ──────────────────────────────────────────────────
        t_obs    = a.get("temp_day_actual")
        t_norm   = a.get("temp_day_normal")
        t_anom   = a.get("temp_day_anomaly")
        p_obs    = a.get("precip_actual_mm")
        p_norm   = a.get("precip_normal_mm")
        p_pct    = a.get("precip_anomaly_pct")
        p_30d    = a.get("precip_30d_anomaly_pct")

        # ── Prognose neste 7d ────────────────────────────────────────────────
        t_fc     = a.get("forecast_temp_anomaly")   # °C avvik fra normal
        gust_fc  = a.get("wind_gust_max_7d_ms")

        # ── Fargesetting: rød kun ved reelt eksepsjonelt avvik ───────────────
        def _val_color(val, threshold_pos, threshold_neg=None):
            if val is None:
                return "#374151"  # mørkgrå = nøytral
            thr_neg = threshold_neg if threshold_neg is not None else -threshold_pos
            if val > threshold_pos:
                return "#dc2626"   # rød
            if val < thr_neg:
                return "#2563eb"   # blå (kald/tørke)
            return "#374151"       # nøytral mørkgrå

        t_color  = _val_color(t_anom,  2.0, -2.0)
        p_color  = _val_color(p_pct,  80.0, -40.0)
        tf_color = _val_color(t_fc,    2.0, -2.0)

        # ── Formater verdier ─────────────────────────────────────────────────
        t_str  = f"{t_obs:.0f}°C ({t_anom:+.1f})" if t_obs is not None and t_anom is not None else (f"{t_obs:.0f}°C" if t_obs is not None else "—")
        p_str  = f"{p_obs:.0f}mm ({p_pct:+.0f}%)" if p_obs is not None and p_pct is not None else (f"{p_obs:.0f}mm" if p_obs is not None else "—")
        p30_str = f" · 30d: {p_30d:+.0f}%" if p_30d is not None else ""
        tf_str = f"{t_fc:+.1f}°C vs. norm" if t_fc is not None else "—"
        gust_str = f"{gust_fc:.0f} m/s" if gust_fc is not None else "—"
        gust_color = "#dc2626" if gust_fc and gust_fc > 20 else "#92400e" if gust_fc and gust_fc > 15 else "#374151"

        # ── Samlet risikoindikator ───────────────────────────────────────────
        risk = a.get("risk_total", "Lav")
        risk_dot = {"Høy": "🔴", "Moderat": "🟡", "Lav": "🟢"}.get(risk, "⚪")

        rows.append(
            f'<tr style="border-bottom:1px solid #e5e7eb;">'
            f'<td style="padding:6px 10px;font-size:13px;font-weight:600;color:#111827;white-space:nowrap;">'
            f'{risk_dot} {short} <span style="font-size:11px;color:#9ca3af;font-weight:400;">{cc}</span></td>'
            # Obs temp
            f'<td style="padding:6px 10px;font-size:13px;color:{t_color};font-variant-numeric:tabular-nums;">{t_str}</td>'
            # Obs nedbør
            f'<td style="padding:6px 10px;font-size:13px;color:{p_color};font-variant-numeric:tabular-nums;">{p_str}{p30_str}</td>'
            # Prognose temp-avvik
            f'<td style="padding:6px 10px;font-size:13px;color:{tf_color};font-variant-numeric:tabular-nums;">{tf_str}</td>'
            # Prognose vindkast
            f'<td style="padding:6px 10px;font-size:13px;color:{gust_color};font-variant-numeric:tabular-nums;">{gust_str}</td>'
            f'</tr>'
        )

    table = (
        '<div style="margin:1.5rem 0;">'
        '<div style="font-size:13px;font-weight:700;color:#374151;margin-bottom:8px;">'
        '📋 Obs + prognose — alle regioner</div>'
        '<div style="font-size:11px;color:#6b7280;margin-bottom:10px;">'
        'Obs = forrige uke · Prognose = neste 7 dager · '
        '<span style="color:#dc2626;">Rød</span> = eksepsjonelt avvik (&gt;±2°C / &gt;±80% nedbør) · '
        '<span style="color:#2563eb;">Blå</span> = kald/tørr anomali</div>'
        '<div style="overflow-x:auto;">'
        '<table style="border-collapse:collapse;width:100%;background:#fff;border:1px solid #e5e7eb;border-radius:6px;">'
        '<thead>'
        '<tr style="background:#f9fafb;border-bottom:2px solid #e5e7eb;">'
        '<th style="padding:7px 10px;text-align:left;font-size:12px;color:#6b7280;font-weight:600;">Region</th>'
        '<th style="padding:7px 10px;text-align:left;font-size:12px;color:#6b7280;font-weight:600;">Temp obs. (avvik)</th>'
        '<th style="padding:7px 10px;text-align:left;font-size:12px;color:#6b7280;font-weight:600;">Nedbør obs. (avvik)</th>'
        '<th style="padding:7px 10px;text-align:left;font-size:12px;color:#6b7280;font-weight:600;">Temp prog. 7d</th>'
        '<th style="padding:7px 10px;text-align:left;font-size:12px;color:#6b7280;font-weight:600;">Vindkast prog.</th>'
        '</tr>'
        '</thead>'
        '<tbody>'
        + "\n".join(rows) +
        '</tbody>'
        '</table>'
        '</div>'
        '</div>'
    )
    return table


def build_dashboard(analyses: list, gibraltar_data: dict | None = None) -> str:
    """
    Dashboard-fane: statuskort per region + varselseksjon + Gibraltar-linje.
    Viser: Risiko, Temp-avvik, Nedbørsavvik, Vind, Tørkeindikator, Varsler.
    """
    # ── Globalt sammendrag øverst ─────────────────────────────────────────────
    n_high = sum(1 for a in analyses if a.get("risk_total") == "Høy")
    n_mod  = sum(1 for a in analyses if a.get("risk_total") == "Moderat")
    n_low  = sum(1 for a in analyses if a.get("risk_total") == "Lav")

    if n_high >= 3:
        summary_cls  = "critical"
        summary_icon = "🚨"
        summary_txt  = f"SIMULTANKRISE — {n_high} regioner med høy risiko"
    elif n_high >= 1:
        summary_cls  = "warning"
        summary_icon = "⚠️"
        summary_txt  = f"{n_high} region(er) med høy risiko"
    elif n_mod >= 1:
        summary_cls  = "warning"
        summary_icon = "⚡"
        summary_txt  = f"{n_mod} region(er) med moderat risiko"
    else:
        summary_cls  = "success"
        summary_icon = "✅"
        summary_txt  = "Alle regioner normale"

    summary_bar = (
        f'<div class="alert-box {summary_cls}" style="margin-bottom:1rem;">'
        f'<strong>{summary_icon} {summary_txt}</strong> &nbsp;'
        f'<span style="font-size:12px;">'
        f'Høy: <b>{n_high}</b> &nbsp; Moderat: <b>{n_mod}</b> &nbsp; Lav: <b>{n_low}</b>'
        f'</span></div>'
    )

    # ── Aktive varsler (neste 7 dager) ────────────────────────────────────────
    all_alerts = []
    for a in analyses:
        for al in a.get("alerts_7d", []):
            rn = a["region_name"].split("–")[-1].strip() if "–" in a["region_name"] else a["region_name"]
            all_alerts.append((rn, al))

    if all_alerts:
        alert_items = "".join(
            f'<div class="dash-alert-item">'
            f'<span class="dash-alert-region">{rn}</span>'
            f'<span class="dash-alert-text">{al}</span>'
            f'</div>'
            for rn, al in all_alerts
        )
        alerts_section = (
            f'<div class="dash-alerts-panel">'
            f'<div class="dash-alerts-title">🔔 Varsler neste 7 dager ({len(all_alerts)})</div>'
            f'{alert_items}'
            f'</div>'
        )
    else:
        alerts_section = (
            '<div class="dash-alerts-panel dash-alerts-ok">'
            '<div class="dash-alerts-title">🔔 Ingen aktive varsler for neste 7 dager</div>'
            '</div>'
        )

    # ── Regionkort ────────────────────────────────────────────────────────────
    FLAG = {"IT": "🇮🇹", "ES": "🇪🇸", "PT": "🇵🇹", "MA": "🇲🇦"}
    cards_html = []

    for a in analyses:
        rid   = a["region_id"]
        cc    = rid.split("_")[0]
        flag  = FLAG.get(cc, "🌍")
        rn    = a["region_name"].split("–")[-1].strip() if "–" in a["region_name"] else a["region_name"]
        risk  = a.get("risk_total", "Lav")
        color = _risk_color(risk)
        bg    = _risk_bg(risk)

        # Temperatur
        t_anom = a.get("temp_7d_anomaly") if a.get("temp_7d_anomaly") is not None else a.get("temp_day_anomaly")
        t_str  = f"{t_anom:+.1f}°C" if t_anom is not None else "—"
        t_cls  = "anom-warm" if t_anom and t_anom > 0 else "anom-cool" if t_anom and t_anom < 0 else "anom-neu"

        # Nedbør
        p_pct = a.get("precip_anomaly_pct")
        p_30d = a.get("precip_30d_anomaly_pct")
        p_disp = p_30d if p_30d is not None else p_pct
        p_lbl  = "(30d)" if p_30d is not None else "(uke)"
        p_str  = f"{p_disp:+.0f}% {p_lbl}" if p_disp is not None else "—"
        p_cls  = ("anom-pos" if p_disp and p_disp > 30
                  else "anom-neg" if p_disp and p_disp < -30
                  else "anom-neu")

        # Vind
        gust = a.get("wind_gust_max_7d_ms")
        g_str = f"{gust:.1f} m/s" if gust is not None else "—"
        g_cls = ("wind-high" if gust and gust > 20
                 else "wind-mod" if gust and gust > 15
                 else "")

        # Tørke
        drought = a.get("risk_drought", "Lav")
        d_icon  = "🌵" if drought == "Høy" else "🌡️" if drought == "Moderat" else "✓"

        # Varsler
        alerts = a.get("alerts_7d", [])
        al_html = (f'<div class="dash-card-alert">{alerts[0]}</div>' if alerts else "")
        more    = f'<div class="dash-card-more">+{len(alerts)-1} til</div>' if len(alerts) > 1 else ""

        cards_html.append(
            f'<div class="dash-card" style="border-top:3px solid {color};background:{bg};">'
            f'  <div class="dash-card-header">'
            f'    <span class="dash-card-flag">{flag}</span>'
            f'    <span class="dash-card-name">{rn}</span>'
            f'    <span class="dash-card-cc">{cc}</span>'
            f'    {risk_pill(risk)}'
            f'  </div>'
            f'  <div class="dash-card-metrics">'
            f'    <div class="dash-metric"><span class="dash-metric-label">Temperatur</span>'
            f'      <span class="dash-metric-val {t_cls}">{t_str}</span></div>'
            f'    <div class="dash-metric"><span class="dash-metric-label">Nedbør</span>'
            f'      <span class="dash-metric-val {p_cls}">{p_str}</span></div>'
            f'    <div class="dash-metric"><span class="dash-metric-label">Vind (7d kast)</span>'
            f'      <span class="dash-metric-val {g_cls}">{g_str}</span></div>'
            f'    <div class="dash-metric"><span class="dash-metric-label">Tørke</span>'
            f'      <span class="dash-metric-val">{d_icon} {drought}</span></div>'
            f'  </div>'
            f'  {al_html}{more}'
            f'</div>'
        )

    cards_grid = '<div class="dash-cards-grid">\n' + "\n".join(cards_html) + "\n</div>"

    # ── Gibraltar-linje (hvis tilgjengelig) ───────────────────────────────────
    if gibraltar_data:
        from gibraltar_fetcher import build_gibraltar_bar
        gib_bar = build_gibraltar_bar(gibraltar_data)
    else:
        gib_bar = ""

    return f"{summary_bar}\n{gib_bar}\n{alerts_section}\n{cards_grid}\n{build_obs_forecast_summary(analyses)}"


def build_precip_alert(analyses: list) -> str:
    """Dynamic alert under precip table — show only if extreme precip detected."""
    extreme = [a for a in analyses
               if a.get("precip_anomaly_pct") is not None
               and a.get("precip_anomaly_pct") > 150]
    if not extreme:
        return ""
    names   = ", ".join(
        a["region_name"].split("–")[-1].strip() if "–" in a["region_name"]
        else a["region_name"] for a in extreme
    )
    max_pct  = max(a["precip_anomaly_pct"] for a in extreme)
    severity = "critical" if max_pct > 300 else "warning"
    icon     = "⚠️" if max_pct > 300 else "⚡"
    return (
        f'<div class="alert-box {severity} mt-2">'
        f'<strong>{icon} Ekstremt nedbørsavvik registrert</strong> '
        f'{len(extreme)} region(er) med nedbør over 150% av normalperiode: {names}. '
        f'Høyeste avvik: {max_pct:+.0f}% vs. klimanormal (WMO 1991–2020).'
        f'</div>'
    )


def build_confidence_grid(analyses: list) -> str:
    """Datakilde-kort per region — viser faktisk kilde, ikke abstrakt kvalitetsnivå."""
    cards = []
    SOURCE_MAP = {
        "IT": "ECMWF + Open-Meteo archive",
        "ES": "AEMET + Open-Meteo archive",
        "PT": "IPMA + Open-Meteo archive",
        "MA": "DMN + Open-Meteo archive",
    }
    for a in analyses:
        rn  = a["region_name"].split("–")[-1].strip() if "–" in a["region_name"] else a["region_name"]
        cc  = a["region_id"].split("_")[0]
        has_precip = a.get("precip_actual_mm") is not None
        has_temp   = a.get("temp_day_actual")  is not None
        has_7d     = a.get("temp_7d_avg")      is not None
        has_30d    = bool(a.get("precip_30d_daily"))
        risk       = a.get("risk_total", "Lav")

        source = SOURCE_MAP.get(cc, "Open-Meteo archive")

        if has_precip and has_temp:
            level = "Bekreftet"
            cls   = "conf-high"
            note  = f"{source}. Uke-obs + {'7d temp + ' if has_7d else ''}{'30d nedbør' if has_30d else 'prognose'}."
        elif has_precip or has_temp:
            level = "Delvis"
            cls   = "conf-mod"
            missing = "temp" if not has_temp else "nedbør"
            note  = f"{source}. Mangler {missing}-data denne perioden."
        else:
            level = "Ingen obs."
            cls   = "conf-low"
            note  = f"API-timeout. Viser klimanormaler og prognose."

        if risk == "Høy":
            note += " ⚠️ Høyrisikoregion."

        cards.append(
            f'<div class="confidence-card">'
            f'<div class="confidence-region">{rn} ({cc})</div>'
            f'<span class="confidence-level {cls}">{level}</span>'
            f'<div class="confidence-note">{note}</div>'
            f'</div>'
        )

    cards.append(
        '<div class="confidence-card">'
        '<div class="confidence-region">Sub-seasonal (uke 3–6)</div>'
        '<span class="confidence-level conf-low">Modellbasert</span>'
        '<div class="confidence-note">ECMWF ENS + EC46. Ensemble-spredning øker markant utover uke 2–3. Kun sannsynlighetstrender.</div>'
        '</div>'
    )
    return '<div class="confidence-grid">\n' + "\n".join(cards) + "\n</div>"


def build_port_alert(analyses: list) -> str:
    """Generate port/ferry status from transport risk data."""
    high_transport = [a for a in analyses if a.get("risk_transport") == "Høy"]
    mod_transport  = [a for a in analyses if a.get("risk_transport") == "Moderat"]

    if not high_transport and not mod_transport:
        return (
            '<div class="alert-box success">'
            '<strong>✅ Alle transportkorridorer operative</strong> '
            'Ingen værbetingede transportrisikoer registrert. Normale forhold ved alle havner og fergeoverganger.'
            '</div>'
        )

    affected = high_transport or mod_transport
    names    = ", ".join(
        a["region_name"].split("–")[-1].strip() if "–" in a["region_name"]
        else a["region_name"] for a in affected
    )
    severity = "warning" if not high_transport else "critical"
    icon     = "⚡" if not high_transport else "⚠️"
    level    = "Moderat" if not high_transport else "Høy"
    return (
        f'<div class="alert-box {severity}">'
        f'<strong>{icon} Transportrisiko {level} — regioner berørt: {names}</strong> '
        f'Transportrisiko basert på nedbørsavvik, veistengninger og havneforhold. '
        f'Se tabellene under for detaljer per region.'
        f'</div>'
    )

def build_footer_date(week_num: int, year: int, data_str: str) -> str:
    """Dynamic footer date string."""
    return f"Uke {week_num}/{year} · {data_str}"


def build_section_labels(week_num: int, year: int, week_start: date, week_end: date) -> dict:
    """
    Returns dict of dynamic section label strings.
    """
    months_no = ["januar","februar","mars","april","mai","juni",
                 "juli","august","september","oktober","november","desember"]
    start_str = f"{week_start.day}. {months_no[week_start.month-1]}"
    end_str   = f"{week_end.day}. {months_no[week_end.month-1]} {week_end.year}"
    return {
        "oversikt": f"Uke {week_num}, {year}",
        "precip":   f"{start_str} – {end_str}",
        "change":   f"Rapport nr. {week_num}/{year}",
    }


def build_port_cards(analyses: list) -> str:
    """
    Generate port cards dynamically from transport risk data.
    Each port gets a status badge based on risk level of nearby regions.
    """
    # Map ports to their associated production regions
    PORTS = [
        {
            "name": "⚓ Port of Rotterdam",
            "country": "🇳🇱 Nederland — Primær mottakshavn Nord-Europa",
            "regions": [],  # Rotterdam not directly tied to a production region
            "type": "receiving",
        },
        {
            "name": "⚓ Port of Amsterdam",
            "country": "🇳🇱 Nederland — Mottakshavn",
            "regions": [],
            "type": "receiving",
        },
        {
            "name": "⚓ Puerto de Algeciras",
            "country": "🇪🇸 Cádiz — Primær eksportport Andalusia + Fergehavn Marokko",
            "regions": ["ES_HUELVA", "ES_SEVILLA"],
            "type": "export",
        },
        {
            "name": "⚓ Puerto de Valencia",
            "country": "🇪🇸 Valencia — Eksport Murcia/Levante/Valencia",
            "regions": ["ES_MURCIA_CAMPO", "ES_MURCIA_LORCA", "ES_VALENCIA_SUR"],
            "type": "export",
        },
        {
            "name": "⚓ Puerto de Cartagena",
            "country": "🇪🇸 Murcia — Regional eksport Campo de Cartagena",
            "regions": ["ES_MURCIA_CAMPO", "ES_ALMERIA"],
            "type": "export",
        },
        {
            "name": "⚓ Puerto de Barcelona",
            "country": "🇪🇸 Katalonia — Kombinert eksport/import",
            "regions": [],
            "type": "export",
        },
    ]

    # Build risk lookup by region_id
    risk_map = {a["region_id"]: a.get("risk_transport", "Lav") for a in analyses}

    cards_nl, cards_es = [], []
    for port in PORTS:
        # Determine port status from associated regions
        region_risks = [risk_map.get(r, "Lav") for r in port["regions"]]
        if "Høy" in region_risks:
            status = "Forsinkelser"
            header_cls = "port-warning"
            pill_cls = "pill-mod"
        elif "Moderat" in region_risks:
            status = "Overvåkes"
            header_cls = "port-warning"
            pill_cls = "pill-mod"
        else:
            status = "Normal"
            header_cls = "port-ok"
            pill_cls = "pill-low"

        # Get transport details from highest-risk associated region
        note = ""
        for rid in port["regions"]:
            a = next((x for x in analyses if x["region_id"] == rid), None)
            if a and a.get("risk_transport") in ("Høy", "Moderat"):
                pct = a.get("precip_anomaly_pct")
                if pct and abs(pct) > 50:
                    rn = a["region_name"].split("–")[-1].strip()
                    note = f"Nedbørsavvik {pct:+.0f}% i {rn} påvirker tilfartsveier."
                break

        if not note and status == "Normal":
            note = "Normale operasjonsforhold."

        card = (
            f'<div class="port-card">'
            f'<div class="port-card-header {header_cls}">'
            f'<div><div class="port-name">{port["name"]}</div>'
            f'<div class="port-country">{port["country"]}</div></div>'
            f'<span class="pill {pill_cls}">{status}</span>'
            f'</div>'
            f'<div class="port-body">{note}</div>'
            f'</div>'
        )
        if port["country"].startswith("🇳🇱"):
            cards_nl.append(card)
        else:
            cards_es.append(card)

    nl_html = (
        '<div class="subsection-title">🇳🇱 Nederland — Mottakshavner</div>'
        '<div class="port-grid">' + "\n".join(cards_nl) + "</div>"
    ) if cards_nl else ""

    es_html = (
        '<div class="subsection-title mt-3">🇪🇸 Spania — Eksport- og transithavner</div>'
        '<div class="port-grid">' + "\n".join(cards_es) + "</div>"
    ) if cards_es else ""

    # ── Dynamisk fergekort Tanger Med–Algeciras ───────────────────────────────
    huelva  = next((x for x in analyses if x["region_id"] == "ES_HUELVA"),       None)
    sevilla = next((x for x in analyses if x["region_id"] == "ES_SEVILLA"),      None)
    ma_north = next((x for x in analyses if x["region_id"] == "MA_NORTH_RABAT"), None)

    ferry_risks = [
        x.get("risk_transport", "Lav") for x in [huelva, sevilla, ma_north] if x
    ]
    ferry_precip_pcts = [
        x.get("precip_anomaly_pct") for x in [huelva, sevilla] if x and x.get("precip_anomaly_pct")
    ]
    max_pct = max(ferry_precip_pcts) if ferry_precip_pcts else None

    if "Høy" in ferry_risks and max_pct and max_pct > 200:
        ferry_status   = "Kraftig redusert"
        ferry_cls      = "port-critical"
        ferry_pill     = "pill-high"
        ferry_pct_str  = f"⚠️ {max_pct:+.0f}% nedbørsavvik i tilfartsområdet"
        ferry_note     = (f"Ekstremnedbør i Huelva/Sevilla-regionen ({max_pct:+.0f}% vs norm) "
                          f"påvirker A-7/AP-7 tilfartsveier til Algeciras. "
                          f"Forventet redusert frekvens Tanger Med ↔ Algeciras.")
    elif "Høy" in ferry_risks:
        ferry_status  = "Forsinkelser"
        ferry_cls     = "port-warning"
        ferry_pill    = "pill-mod"
        ferry_pct_str = f"Transportrisiko Høy i tilfartsregioner"
        ferry_note    = "Forsinkelser mulig på Tanger Med–Algeciras. Sjekk Baleària/FRS/Trasmediterranea."
    elif "Moderat" in ferry_risks:
        ferry_status  = "Overvåkes"
        ferry_cls     = "port-warning"
        ferry_pill    = "pill-mod"
        ferry_pct_str = "Moderat risiko i tilfartsområdet"
        ferry_note    = "Normale avganger, men Andalusia/Marokko overvåkes for nedbørsutvikling."
    else:
        ferry_status  = "Normal drift"
        ferry_cls     = "port-ok"
        ferry_pill    = "pill-low"
        ferry_pct_str = "Ingen værbetingede forstyrrelser"
        ferry_note    = "Normale avganger Tanger Med ↔ Algeciras/Tarifa. A-7/AP-7 operative."

    ferry_html = (
        '<div class="subsection-title mt-3">⛴️ Fergeovergangen Marokko — Spania</div>'
        '<div class="ferry-card">'
        f'<div class="port-card-header {ferry_cls}">'
        f'<div><div class="port-name">⛴️ Tanger Med — Algeciras / Tarifa</div>'
        f'<div class="port-country">🇲🇦→🇪🇸 · Primær Marokko-eksportrute · Baleària · FRS · Trasmediterranea</div>'
        f'</div><span class="pill {ferry_pill}">{ferry_status}</span></div>'
        f'<div class="port-body">'
        f'<strong>{ferry_pct_str}.</strong> {ferry_note}'
        f'<div class="port-meta" style="margin-top:6px;">'
        f'<span>🚢 Tanger Med → Algeciras</span>'
        f'<span>⏱️ ~35 min overfartstid</span>'
        f'<span>📦 Primær rute: Marokko-grønnsaker til EU</span>'
        f'</div></div></div>'
    )

    return nl_html + "\n" + es_html + "\n" + ferry_html


def build_change_rows(analyses: list, prev_analyses: list = None) -> str:
    """
    Generate change table rows comparing current vs previous week.
    If no prev_analyses, shows current data as baseline.
    """
    rows = []
    for a in analyses:
        rn    = a["region_name"]
        cc    = a["region_id"].split("_")[0]
        short = rn.split("–")[-1].strip() if "–" in rn else rn

        risk_now  = a.get("risk_total", "Lav")
        td        = a.get("temp_day_anomaly")
        pct       = a.get("precip_anomaly_pct")
        conf      = "Høy" if (a.get("precip_actual_mm") and a.get("temp_day_actual")) else "Moderat"

        if prev_analyses:
            prev = next((p for p in prev_analyses if p["region_id"] == a["region_id"]), None)
            risk_prev = prev.get("risk_total", "Lav") if prev else risk_now
            if risk_now == "Høy" and risk_prev != "Høy":
                row_cls = "change-row-up"
                bg = ' style="background:#fff5f5;"'
            elif risk_now == "Lav" and risk_prev == "Høy":
                row_cls = "change-row-down"
                bg = ' style="background:#f0fff4;"'
            else:
                row_cls = "change-row-same"
                bg = ""
            note = f"{'▲ Forverring' if row_cls == 'change-row-up' else '▼ Bedring' if row_cls == 'change-row-down' else 'Stabil'}"
        else:
            # Baseline mode
            row_cls = "change-row-up" if risk_now == "Høy" else "change-row-same"
            bg = ' style="background:#fff5f5;"' if risk_now == "Høy" else ""
            note_map = {"Høy": "Høy risiko registrert", "Moderat": "Moderat risiko", "Lav": "Normalt vintersignal"}
            note = note_map.get(risk_now, "")

        td_str  = f"{td:+.1f}°C"  if td  is not None else "N/A"
        pct_str = f"{pct:+.0f}%"  if pct is not None else "N/A"
        td_cls  = anom_class(td)
        pct_cls = anom_class(pct)
        conf_cls = "conf-high" if conf == "Høy" else "conf-mod"

        rows.append(
            f'<tr class="{row_cls}"{bg}>'
            f'<td><span class="region-name">{short}</span><span class="region-country">{cc}</span></td>'
            f'<td>{risk_pill(risk_now)}</td>'
            f'<td class="{td_cls}">{td_str}</td>'
            f'<td class="{pct_cls}">{pct_str}</td>'
            f'<td><span class="confidence-level {conf_cls}" style="font-size:10px;">{conf}</span></td>'
            f'<td style="font-size:11px;text-align:left;">{note}</td>'
            f'</tr>'
        )
    return "\n".join(rows)




# =============================================================================
# PROGNOSE-FANE — dynamisk med Claude-tolkning
# =============================================================================

COUNTRY_FLAG = {"IT": "🇮🇹", "ES": "🇪🇸", "PT": "🇵🇹", "MA": "🇲🇦"}

def _forecast_signal_html(temp_anom: Optional[float],
                           precip_note: str,
                           gust: Optional[float],
                           alerts: list) -> str:
    """Lager én forecast-rad med retningspil og signal-tekst."""
    # Temperaturpil
    if temp_anom is None:
        temp_str = "→ Temp ukjent"
    elif temp_anom > 1.5:
        temp_str = f'<span class="forecast-signal trend-up">↑ Varmt +{temp_anom:.1f}°C</span>'
    elif temp_anom < -1.5:
        temp_str = f'<span class="forecast-signal trend-down">↓ Kjølig {temp_anom:.1f}°C</span>'
    else:
        temp_str = f'<span class="forecast-signal trend-flat">→ Nær normal temp ({temp_anom:+.1f}°C)</span>'

    # Nedbørssignal
    pn = precip_note or ""
    if "Kraftig" in pn or "⚠️" in pn:
        precip_str = f'<span style="color:#b45309;">⚠️ {pn.replace("⚠️ ","")}</span>'
    elif "Tørrere" in pn:
        precip_str = f'<span style="color:#92400e;">🌵 {pn}</span>'
    elif "Mer nedbør" in pn:
        precip_str = f'<span style="color:#1d4ed8;">🌧️ {pn}</span>'
    else:
        precip_str = f'<span style="color:#374151;">{pn}</span>'

    # Vindvarsel
    wind_str = ""
    if gust and gust > 20:
        wind_str = f' · <span style="color:#dc2626;">💨 Storm {gust:.0f} m/s</span>'
    elif gust and gust > 15:
        wind_str = f' · <span style="color:#d97706;">💨 Sterk vind {gust:.0f} m/s</span>'

    # Aktive varsler
    alert_html = ""
    if alerts:
        alert_html = " · " + " ".join(
            f'<span class="alert-badge">{a.split("(")[0].strip()}</span>'
            for a in alerts[:2]
        )

    return (f'{temp_str} · {precip_str}{wind_str}{alert_html}')


def _build_forecast_rules(analyses: list, week_start: date, week_end: date) -> str:
    """
    Regelbasert prognose-HTML. Brukes når Claude API ikke er tilgjengelig,
    eller som datainput til Claude.
    """
    week_num  = week_start.isocalendar()[1]
    next_week = week_start + timedelta(days=7)
    nw_num    = next_week.isocalendar()[1]
    months_no = ["jan","feb","mar","apr","mai","jun","jul","aug","sep","okt","nov","des"]
    date_str  = (f"{week_start.day}. {months_no[week_start.month-1]} – "
                 f"{week_end.day}. {months_no[week_end.month-1]} {week_end.year}")

    rows = []
    for a in analyses:
        cc    = a["region_id"].split("_")[0]
        flag  = COUNTRY_FLAG.get(cc, "")
        name  = a["region_name"].split("–")[-1].strip() if "–" in a["region_name"] else a["region_name"]
        fdir  = a.get("forecast_temp_direction", "→")
        fanom = a.get("forecast_temp_anomaly")
        pnote = a.get("forecast_precip_note", "Ingen data")
        gust  = a.get("wind_gust_max_7d_ms")
        alerts = a.get("alerts_7d", [])

        signal = _forecast_signal_html(fanom, pnote, gust, alerts)
        rows.append(
            f'<div class="forecast-row">'
            f'<span class="forecast-region">{flag} {name}</span>'
            f'<span>{signal}</span>'
            f'</div>'
        )

    rows_html = "\n        ".join(rows)

    return f"""
  <div class="forecast-grid">
    <div class="forecast-card">
      <div class="forecast-card-header">
        Prognose neste 7 dager (uke {nw_num})
        <span class="precision-tag">Open-Meteo forecast</span>
      </div>
      <div style="font-size:11px;color:var(--muted);padding:0.4rem 1rem 0;">
        Basert på observert uke {week_num} ({date_str}) + 7-dagersprognose
      </div>
      <div class="forecast-rows">
        {rows_html}
      </div>
    </div>
  </div>"""


def _summarize_for_claude(analyses: list, week_start: date) -> str:
    """Lager et kompakt datasett som input til Claude-tolkning."""
    lines = [f"Rapportuke: {week_start.isoformat()} (uke {week_start.isocalendar()[1]})"]
    lines.append(f"Måned: {week_start.month} ({'vinter' if week_start.month in [12,1,2,3] else 'vår/sommer/høst'})")
    lines.append("")
    for a in analyses:
        rid  = a["region_id"]
        name = a["region_name"].split("–")[-1].strip() if "–" in a["region_name"] else a["region_name"]
        lines.append(f"Region: {name} ({rid})")
        lines.append(f"  Temp-avvik dag:   {a.get('temp_day_anomaly', 'N/A')}°C")
        lines.append(f"  Temp-avvik 7d:    {a.get('temp_7d_anomaly', 'N/A')}°C")
        lines.append(f"  Nedbør uke-avvik: {a.get('precip_anomaly_pct', 'N/A')}%")
        lines.append(f"  Nedbør 30d-avvik: {a.get('precip_30d_anomaly_pct', 'N/A')}%")
        lines.append(f"  Vindkast 7d:      {a.get('wind_gust_max_7d_ms', 'N/A')} m/s")
        lines.append(f"  Risiko total:     {a.get('risk_total', 'N/A')}")
        lines.append(f"  Risiko tørke:     {a.get('risk_drought', 'N/A')}")
        lines.append(f"  Forecast nedbør:  {a.get('forecast_precip_note', 'N/A')}")
        alerts = a.get("alerts_7d", [])
        if alerts:
            lines.append(f"  Varsler 7d:       {'; '.join(alerts)}")
        lines.append("")
    return "\n".join(lines)


def _call_claude_forecast(data_summary: str, week_start: date) -> Optional[str]:
    """
    Kaller Claude API for å generere en kvalifisert meteorologisk tolkning
    av forecast-dataene. Returnerer HTML-streng eller None ved feil.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        logger.info("ANTHROPIC_API_KEY ikke satt — hopper over Claude-tolkning")
        return None

    month = week_start.month
    season = ("vinter" if month in [12, 1, 2, 3]
              else "vår" if month in [4, 5]
              else "sommer" if month in [6, 7, 8]
              else "høst")

    system = """Du er en senior agro-meteorolog med spesialkompetanse på forsyningskjeder for frukt og grønnsaker i Sør-Europa og Nord-Afrika. 
Du tolker meteorologiske data og gir presise, handlingsorienterte vurderinger på norsk.

Stil: faglig men tilgjengelig. Unngå unødvendig alarmisme. Vektlegg sesongkontekst.
Svar BARE med HTML (ingen markdown, ingen forklaringer utenfor HTML).
Bruk disse klassene: forecast-card, forecast-card-header, forecast-rows, forecast-row, forecast-region, forecast-signal, trend-up, trend-down, trend-flat, precision-tag"""

    prompt = f"""Basert på disse meteorologiske dataene fra rapportuken, generer en prognosevurdering for neste 7–14 dager.

{data_summary}

Sesong: {season} (måned {month})

Generer HTML med:
1. Ett kort «Overordnet vurdering» (2–3 setninger) i en <div class="table-explainer"> 
2. En <div class="forecast-grid"> med forecast-cards:
   - Kort 1: «Neste 7 dager per region» — én rad per region med konkret vurdering
   - Kort 2: «Sesong og mønstre» — overordnede trender, forsyningskjede-implikasjoner

Regler:
- Tørke er bare relevant mai–september. Ikke nevn tørkerisiko i vintermåneder.
- Nedbørsunderskudd alene om vinteren er ikke problematisk — si det eksplisitt hvis relevant.
- Fremhev regioner med reell risiko (Høy/Moderat). 
- Legg alltid til en kilde-linje: <div style="font-size:11px;color:var(--muted);padding:0.4rem 1rem;border-top:1px solid var(--border);">Tolkning: Claude AI · Data: Open-Meteo · Oppdatert: [dagens dato]</div>"""

    try:
        import urllib.request, json as _json
        payload = _json.dumps({
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 1500,
            "system": system,
            "messages": [{"role": "user", "content": prompt}]
        }).encode()

        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=payload,
            headers={
                "Content-Type":      "application/json",
                "x-api-key":         api_key,
                "anthropic-version": "2023-06-01",
            },
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=45) as resp:
            result = _json.loads(resp.read().decode())
            text_blocks = [b["text"] for b in result.get("content", []) if b.get("type") == "text"]
            return "\n".join(text_blocks) if text_blocks else None
    except Exception as e:
        logger.warning(f"Claude forecast API feil: {e}")
        return None


def build_forecast_tab(analyses: list, week_start: date, week_end: date) -> str:
    """
    Bygger prognose-fane dynamisk.
    1. Prøver Claude API for kvalifisert tolkning
    2. Faller tilbake til regelbasert HTML
    """
    # Alltid bygg regelbasert som fallback/datainput
    rules_html   = _build_forecast_rules(analyses, week_start, week_end)
    data_summary = _summarize_for_claude(analyses, week_start)

    # Prøv Claude
    claude_html = _call_claude_forecast(data_summary, week_start)

    if claude_html:
        logger.info("Forecast-fane: Claude AI-tolkning injisert")
        # Legg regelbasert datatabell under Claude-tolkning
        return (claude_html + "\n" +
                '<details style="margin:1rem 0;"><summary style="cursor:pointer;font-size:12px;'
                'color:var(--muted);">📊 Vis rådata (regelbasert)</summary>'
                + rules_html + "</details>")
    else:
        logger.info("Forecast-fane: regelbasert HTML (Claude ikke tilgjengelig)")
        return rules_html


# =============================================================================
# REPLACE SECTION HELPER
# =============================================================================

def replace_section(html: str, start_tag: str, end_tag: str, new_content: str) -> str:
    pattern = rf"{re.escape(start_tag)}.*?{re.escape(end_tag)}"
    replacement = f"{start_tag}\n{new_content}\n{end_tag}"
    new_html, n = re.subn(pattern, replacement, html, flags=re.DOTALL)
    if n == 0:
        logger.warning(f"Sentinel ikke funnet: {start_tag}")
    return new_html


# =============================================================================
# UPDATE HTML — calls all build_* functions above
# =============================================================================

def update_html(html: str, analyses: list, week_start: date, week_end: date) -> str:
    week_num = week_start.isocalendar()[1]
    year     = week_start.year

    months_no = ["januar","februar","mars","april","mai","juni",
                 "juli","august","september","oktober","november","desember"]
    data_str  = f"{date.today().day}. {months_no[date.today().month-1]} {date.today().year}"

    cet     = timezone(timedelta(hours=1))
    now_cet = datetime.now(cet)
    time_str = now_cet.strftime("%H:%M")
    last_updated_full = f"Uke {week_num}, {year} &nbsp;·&nbsp; Oppdatert {data_str} kl. {time_str} CET"

    updated_str = f"Uke {week_num}, {year}"
    html = re.sub(r"Uke \d+, \d{4}", updated_str, html)
    html = re.sub(r"Rapport nr\. \d+/\d{4}", f"Rapport nr. {week_num}/{year}", html)

    # ── Last updated ──────────────────────────────────────────────────────────
    html = replace_section(html,
        "<!-- DATA:LAST_UPDATED_START -->", "<!-- DATA:LAST_UPDATED_END -->",
        last_updated_full)

    # ── Table rows ────────────────────────────────────────────────────────────
    html = replace_section(html,
        "<!-- DATA:OVERVIEW_ROWS_START -->", "<!-- DATA:OVERVIEW_ROWS_END -->",
        build_overview_rows(analyses))

    html = replace_section(html,
        "<!-- DATA:TEMP_ROWS_START -->", "<!-- DATA:TEMP_ROWS_END -->",
        build_temp_rows(analyses))

    html = replace_section(html,
        "<!-- DATA:PRECIP_ROWS_START -->", "<!-- DATA:PRECIP_ROWS_END -->",
        build_precip_rows(analyses))

    html = replace_section(html,
        "<!-- DATA:SC_ROWS_START -->", "<!-- DATA:SC_ROWS_END -->",
        build_supply_chain_rows(analyses))

    html = replace_section(html,
        "<!-- DATA:TREND_ROWS_START -->", "<!-- DATA:TREND_ROWS_END -->",
        build_trend_rows(analyses))

    # ── KPI counts ────────────────────────────────────────────────────────────
    high = sum(1 for a in analyses if a.get("risk_total") == "Høy")
    mod  = sum(1 for a in analyses if a.get("risk_total") == "Moderat")
    low  = sum(1 for a in analyses if a.get("risk_total") == "Lav")

    def repl_kpi(h, sentinel, value):
        return re.sub(
            rf"<!-- KPI:{sentinel} -->[^<]*",
            f"<!-- KPI:{sentinel} -->{value}", h
        )

    html = repl_kpi(html, "HIGH_COUNT", str(high))
    html = repl_kpi(html, "MOD_COUNT",  str(mod))
    html = repl_kpi(html, "LOW_COUNT",  str(low))

    html = replace_section(html,
        "<!-- KPI:HIGH_SUB_START -->", "<!-- KPI:HIGH_SUB_END -->",
        build_kpi_sub(analyses, "Høy"))
    html = replace_section(html,
        "<!-- KPI:MOD_SUB_START -->", "<!-- KPI:MOD_SUB_END -->",
        build_kpi_sub(analyses, "Moderat"))
    html = replace_section(html,
        "<!-- KPI:LOW_SUB_START -->", "<!-- KPI:LOW_SUB_END -->",
        build_kpi_sub(analyses, "Lav"))

    # ── Alert boxes ───────────────────────────────────────────────────────────
    html = replace_section(html,
        "<!-- DATA:ALERT_BOX_START -->", "<!-- DATA:ALERT_BOX_END -->",
        build_alert_box(analyses, week_num, year))

    html = replace_section(html,
        "<!-- DATA:SC_CARDS_START -->", "<!-- DATA:SC_CARDS_END -->",
        build_sc_observation_cards(analyses))

    html = replace_section(html,
        "<!-- DATA:PRECIP_ALERT_START -->", "<!-- DATA:PRECIP_ALERT_END -->",
        build_precip_alert(analyses))

    html = replace_section(html,
        "<!-- DATA:CONFIDENCE_START -->", "<!-- DATA:CONFIDENCE_END -->",
        build_confidence_grid(analyses))

    html = replace_section(html,
        "<!-- DATA:PORT_ALERT_START -->", "<!-- DATA:PORT_ALERT_END -->",
        build_port_alert(analyses))

    # ── Section labels ────────────────────────────────────────────────────────
    labels = build_section_labels(week_num, year, week_start, week_end)
    html = replace_section(html,
        "<!-- DATA:SECTION_LABEL_OVERSIKT_START -->", "<!-- DATA:SECTION_LABEL_OVERSIKT_END -->",
        labels["oversikt"])
    html = replace_section(html,
        "<!-- DATA:SECTION_LABEL_TEMP_START -->", "<!-- DATA:SECTION_LABEL_TEMP_END -->",
        labels["oversikt"])   # samme verdi: "Uke X, YYYY"
    html = replace_section(html,
        "<!-- DATA:SECTION_LABEL_PRECIP_START -->", "<!-- DATA:SECTION_LABEL_PRECIP_END -->",
        labels["precip"])
    html = replace_section(html,
        "<!-- DATA:SECTION_LABEL_CHANGE_START -->", "<!-- DATA:SECTION_LABEL_CHANGE_END -->",
        labels["change"])

    # ── Footer date ───────────────────────────────────────────────────────────
    html = replace_section(html,
        "<!-- DATA:FOOTER_DATE_START -->", "<!-- DATA:FOOTER_DATE_END -->",
        build_footer_date(week_num, year, data_str))

    # ── Port cards ────────────────────────────────────────────────────────────
    html = replace_section(html,
        "<!-- DATA:PORT_CARDS_START -->", "<!-- DATA:PORT_CARDS_END -->",
        build_port_cards(analyses))

    # ── Change/endring rows ───────────────────────────────────────────────────
    html = replace_section(html,
        "<!-- DATA:CHANGE_ROWS_START -->", "<!-- DATA:CHANGE_ROWS_END -->",
        build_change_rows(analyses))

    # ── Gibraltar-sundet ──────────────────────────────────────────────────────
    logger.info("Fetching Gibraltar maritime conditions...")
    gibraltar_data = fetch_gibraltar_conditions()
    html = replace_section(html,
        "<!-- DATA:GIBRALTAR_START -->", "<!-- DATA:GIBRALTAR_END -->",
        build_gibraltar_html(gibraltar_data))
    html = replace_section(html,
        "<!-- DATA:GIBRALTAR_BAR_START -->", "<!-- DATA:GIBRALTAR_BAR_END -->",
        build_gibraltar_bar(gibraltar_data))

    # ── Dashboard-fane ────────────────────────────────────────────────────────
    html = replace_section(html,
        "<!-- DATA:DASHBOARD_START -->", "<!-- DATA:DASHBOARD_END -->",
        build_dashboard(analyses, gibraltar_data))

    # ── Prognose-fane ─────────────────────────────────────────────────────────
    html = replace_section(html,
        "<!-- DATA:FORECAST_START -->", "<!-- DATA:FORECAST_END -->",
        build_forecast_tab(analyses, week_start, week_end))

    # ── News ──────────────────────────────────────────────────────────────────
    articles = fetch_news(max_articles=12)
    news_html = render_news_html(articles)
    html = replace_section(html,
        "<!-- DATA:NEWS_START -->", "<!-- DATA:NEWS_END -->",
        news_html)

    return html


def build_historical_banner(week_start: date, week_end: date) -> str:
    """Synlig banner øverst i rapporten når historisk uke vises."""
    months_no = ["januar","februar","mars","april","mai","juni",
                 "juli","august","september","oktober","november","desember"]
    s = f"{week_start.day}. {months_no[week_start.month-1]}"
    e = f"{week_end.day}. {months_no[week_end.month-1]} {week_end.year}"
    week_num = week_start.isocalendar()[1]
    return (
        f'<div style="background:#fef9c3;border:1px solid #fde047;border-radius:8px;'
        f'padding:0.6rem 1rem;margin:0.75rem 0;font-size:13px;color:#713f12;">'
        f'<strong>🕐 Historisk rapport — uke {week_num} ({s} – {e})</strong> &nbsp;·&nbsp; '
        f'Observasjonsdata fra Open-Meteo arkiv. '
        f'«Forecast»-kolonner viser faktiske arkivverdier for uken etter. '
        f'Norge- og ECMWF-data ikke tilgjengelig for historiske uker.'
        f'</div>'
    )


# =============================================================================
# MAIN
# =============================================================================

def main():
    args = parse_args()
    week_start, week_end = get_week_bounds(args.week)
    historical = args.week is not None

    logger.info(f"Generating {'HISTORICAL ' if historical else ''}report for week {week_start} – {week_end}")

    fetched = fetch_all_regions(
        config_path="config/regions.yaml",
        use_cache=not args.no_cache,
        week_start_override=week_start if historical else None,
    )
    analyses   = analyze_all(fetched, config_path="config/regions.yaml")
    narratives = generate_all_narratives(analyses)
    logger.info(f"Narrative mode: {narratives['mode_used']}")

    html_path = Path("index.html")
    if not html_path.exists():
        logger.error("index.html not found.")
        sys.exit(1)

    html = html_path.read_text(encoding="utf-8")
    html = update_html(html, analyses, week_start, week_end)

    # ── Historisk banner injiseres rett etter <main> ──────────────────────────
    if historical:
        banner = build_historical_banner(week_start, week_end)
        html = html.replace('<main class="main-content">',
                            f'<main class="main-content">\n{banner}', 1)

    # ── Norge og ECMWF — kun for live-kjøringer ───────────────────────────────
    if not historical:
        logger.info("Henter norske vaerdata fra met.no...")
        try:
            norway_data = fetch_all_norway_regions()
            html = inject_norway_into_html(html, norway_data)
            logger.info(f"Norge-data injisert for {len(norway_data)} regioner.")
        except Exception as e:
            logger.error(f"Feil ved henting av Norge-data: {e}")

        logger.info("Henter ECMWF Europa-regioner...")
        try:
            europe_ecmwf = fetch_ecmwf_all_regions(EUROPE_REGIONS)
            norway_ecmwf = fetch_ecmwf_all_regions(NORWAY_REGIONS)
            html = inject_ecmwf_into_html(html, europe_ecmwf, norway_ecmwf)
            logger.info("ECMWF-data injisert.")
        except Exception as e:
            logger.error(f"Feil ved henting av ECMWF-data: {e}")
    else:
        logger.info("Historisk modus — hopper over Norge/ECMWF (live-data kun).")

    html_path.write_text(html, encoding="utf-8")
    logger.info("index.html updated successfully.")

    high_regions = [a["region_name"] for a in analyses if a.get("risk_total") == "Høy"]
    if high_regions:
        logger.warning(f"HIGH RISK: {', '.join(high_regions)}")
    if historical:
        logger.info(f"Historisk rapport fullført: uke {week_start.isocalendar()[1]}, {week_start} – {week_end}")


if __name__ == "__main__":
    main()
