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
    if val is None:
        return "anom-neu"
    return "anom-pos" if val > 0 else "anom-neg" if val < 0 else "anom-neu"


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
        bg = ' style="background:#fff5f5;"' if a.get("risk_total") == "Høy" else ""
        short = rn.split("–")[-1].strip() if "–" in rn else rn
        rows.append(
            f'<tr{bg}>'
            f'<td><span class="region-name">{short}</span><span class="region-country">{cc}</span></td>'
            f'<td>{flag}</td>'
            f'<td>{status_icon(status)}</td>'
            f'<td class="{pct_cls}">{pct_str}</td>'
            f'<td>{risk_pill(a.get("risk_temp","Lav"))}</td>'
            f'<td>{risk_pill(a.get("risk_precip","Lav"))}</td>'
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
            f'<td class="{anom_class(dd)}">{fmt(dd)}</td>'
            f'<td class="mono">{fmt_range(na)}</td>'
            f'<td class="mono">{fmt_range(nn)}</td>'
            f'<td class="{anom_class(nd)}">{fmt(nd)}</td>'
            f'<td class="mono">{t7_str}</td>'
            f'<td class="{anom_class(t7d)}">{t7d_str}</td>'
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
        rows.append(
            f'<tr{bg}>'
            f'<td><span class="region-name">{short}</span><span class="region-country">{cc}</span></td>'
            f'<td>{risk_pill(a.get("risk_temp","Lav"))}</td>'
            f'<td>{risk_pill(a.get("risk_precip","Lav"))}</td>'
            f'<td>{risk_pill(a.get("risk_transport","Lav"))}</td>'
            f'<td>{risk_pill(a.get("risk_production","Lav"))}</td>'
            f'<td>{risk_pill(a.get("risk_total","Lav"))}</td>'
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
            consensus = ("Vedvarende høy risiko" if risks.count("Høy") >= 2
                         else "Risiko øker" if "Høy" in risks[-2:]
                         else "Stabil / lav risiko")
        else:
            consensus = "Baseline (uke 1)"

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
    """Generate confidence cards based on data availability."""
    cards = []
    for a in analyses:
        rn  = a["region_name"].split("–")[-1].strip() if "–" in a["region_name"] else a["region_name"]
        cc  = a["region_id"].split("_")[0]
        has_precip = a.get("precip_actual_mm") is not None
        has_temp   = a.get("temp_day_actual")  is not None
        risk       = a.get("risk_total", "Lav")
        if has_precip and has_temp:
            level   = "Høy"
            cls     = "conf-high"
            sources = {"IT": "ECMWF + Open-Meteo", "ES": "AEMET + Open-Meteo",
                       "PT": "IPMA + Open-Meteo",  "MA": "DMN + Open-Meteo"}.get(cc, "Open-Meteo")
            note    = f"{sources}. Temperatur og nedbør bekreftet."
        elif has_precip or has_temp:
            level = "Moderat"
            cls   = "conf-mod"
            note  = "Kun én datakilde tilgjengelig. Delvis usikkerhet."
        else:
            level = "Lav"
            cls   = "conf-low"
            note  = "Manglende data. Basert på klimanormaler alene."
        if risk == "Høy":
            note += " Høyrisikoregion — økt overvåking."
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
        '<span class="confidence-level conf-low">Lav</span>'
        '<div class="confidence-note">Ensemble-spredning øker markant utover uke 2–3. Kun sannsynlighetstrender.</div>'
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
            "country": "🇪🇸 Valencia — Eksport Murcia/Levante",
            "regions": ["ES_MURCIA"],
            "type": "export",
        },
        {
            "name": "⚓ Puerto de Cartagena",
            "country": "🇪🇸 Murcia — Regional eksport",
            "regions": ["ES_MURCIA", "ES_ALMERIA"],
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
    huelva  = next((x for x in analyses if x["region_id"] == "ES_HUELVA"),  None)
    sevilla = next((x for x in analyses if x["region_id"] == "ES_SEVILLA"), None)
    ma_north = next((x for x in analyses if x["region_id"] == "MA_NORTH"),  None)

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

    # ── News ──────────────────────────────────────────────────────────────────
    articles = fetch_news(max_articles=12)
    news_html = render_news_html(articles)
    html = replace_section(html,
        "<!-- DATA:NEWS_START -->", "<!-- DATA:NEWS_END -->",
        news_html)

    return html


# =============================================================================
# MAIN
# =============================================================================

def main():
    args = parse_args()
    week_start, week_end = get_week_bounds(args.week)

    logger.info(f"Generating report for week {week_start} – {week_end}")

    fetched = fetch_all_regions(
        config_path="config/regions.yaml",
        use_cache=not args.no_cache,
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

    html_path.write_text(html, encoding="utf-8")
    logger.info("index.html updated successfully.")

    high_regions = [a["region_name"] for a in analyses if a.get("risk_total") == "Høy"]
    if high_regions:
        logger.warning(f"HIGH RISK: {', '.join(high_regions)}")


if __name__ == "__main__":
    main()
