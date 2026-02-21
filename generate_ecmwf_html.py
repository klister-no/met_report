"""
generate_ecmwf_html.py
Genererer HTML-blokker fra ECMWF-data for injeksjon i index.html.

Brukes av generate_html.py via sentineler:
  <!-- DATA:ECMWF_EUROPE_START --> ... <!-- DATA:ECMWF_EUROPE_END -->
  <!-- DATA:ECMWF_NORWAY_START --> ... <!-- DATA:ECMWF_NORWAY_END -->
"""

import re
import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

CET = timezone(timedelta(hours=1))


# ── Stil-hjelpere ──────────────────────────────────────────────────────────────

def _conf_badge(level: str, color: str) -> str:
    colors = {"green": "#27ae60", "orange": "#e67e22", "red": "#e74c3c", "blue": "#2980b9"}
    c = colors.get(color, "#888")
    return f'<span style="background:{c};color:#fff;padding:1px 7px;border-radius:8px;font-size:0.78em;font-weight:600">{level}</span>'


def _temp_color(avvik: float) -> str:
    if avvik is None: return "#888"
    if avvik >= 3:   return "#c0392b"
    if avvik >= 1:   return "#e67e22"
    if avvik <= -3:  return "#1a6fa8"
    if avvik <= -1:  return "#2980b9"
    return "#27ae60"


def _precip_color(val: float, normal: float = 50) -> str:
    if val is None or normal == 0: return "#888"
    ratio = val / normal
    if ratio >= 2.0:  return "#1a6fa8"
    if ratio >= 1.3:  return "#5dade2"
    if ratio <= 0.5:  return "#e67e22"
    if ratio <= 0.7:  return "#f39c12"
    return "#27ae60"


# ── HRES 15-dagers prognose ────────────────────────────────────────────────────

def _hres_table(hres: dict, climate_normals: dict = None) -> str:
    """Genererer kompakt 15-dagers HRES-tabell."""
    if not hres or not hres.get("forecast"):
        return '<p style="color:#888;font-size:0.85em">HRES-data ikke tilgjengelig.</p>'

    rows = ""
    for day in hres["forecast"]:
        frost_mark = " ❄️" if day.get("has_frost") else ""
        rows += f"""
        <tr style="border-bottom:1px solid #f0f0f0">
            <td style="padding:4px 10px;color:#666">{day['weekday']}</td>
            <td style="padding:4px 10px;font-weight:500">{day['date_display']}</td>
            <td style="padding:4px 10px;text-align:center">{day['temp_min']}°C</td>
            <td style="padding:4px 10px;text-align:center;font-weight:600">{day['temp_max']}°C{frost_mark}</td>
            <td style="padding:4px 10px;text-align:center">{day['precip']} mm</td>
        </tr>"""

    return f"""
    <div style="overflow-x:auto">
    <table style="width:100%;border-collapse:collapse;font-size:0.85em">
        <thead>
            <tr style="background:#f5f5f5;font-size:0.8em;color:#555">
                <th style="padding:5px 10px;text-align:left">Dag</th>
                <th style="padding:5px 10px;text-align:left">Dato</th>
                <th style="padding:5px 10px;text-align:center">Min</th>
                <th style="padding:5px 10px;text-align:center">Maks</th>
                <th style="padding:5px 10px;text-align:center">Nedbør</th>
            </tr>
        </thead>
        <tbody>{rows}</tbody>
    </table>
    </div>
    <p style="font-size:0.75em;color:#aaa;margin-top:4px">
        Kilde: ECMWF IFS HRES 9km · {hres.get('updated','')}
    </p>"""


# ── ENS Ensemble-vifte ────────────────────────────────────────────────────────

def _ens_chart(ens: dict) -> str:
    """Genererer HTML-visning av ensemble p10/p50/p90."""
    if not ens or not ens.get("days"):
        return '<p style="color:#888;font-size:0.85em">Ensemble-data ikke tilgjengelig.</p>'

    conf_color = {"green": "#27ae60", "orange": "#e67e22", "red": "#e74c3c"}.get(
        ens.get("confidence_color", "gray"), "#888")

    rows = ""
    for d in ens["days"]:
        spread = d.get("temp_spread", 0) or 0
        spread_w = min(100, int(spread * 12))  # visuell bredde
        rows += f"""
        <tr style="border-bottom:1px solid #f0f0f0">
            <td style="padding:4px 8px;font-size:0.82em;color:#666">{d['date_display']}</td>
            <td style="padding:4px 8px;text-align:right;color:#2980b9">{d.get('temp_p10','–')}°C</td>
            <td style="padding:4px 8px;text-align:center;font-weight:600">{d.get('temp_p50','–')}°C</td>
            <td style="padding:4px 8px;text-align:left;color:#e74c3c">{d.get('temp_p90','–')}°C</td>
            <td style="padding:4px 8px">
                <div style="background:#eee;border-radius:3px;height:8px;width:100px">
                    <div style="background:{conf_color};height:8px;border-radius:3px;width:{spread_w}px"></div>
                </div>
            </td>
            <td style="padding:4px 8px;text-align:center">{d.get('precip_p50','–')} mm</td>
        </tr>"""

    return f"""
    <div style="display:flex;align-items:center;gap:12px;margin-bottom:10px;flex-wrap:wrap">
        <span style="font-size:0.85em;color:#555">Ensemble-confidence:</span>
        {_conf_badge(ens.get('confidence','?'), ens.get('confidence_color','gray'))}
        <span style="font-size:0.8em;color:#888">Gj.snitt spredning: {ens.get('avg_spread','–')}°C ({ens.get('days',[{}])[0].get('n_members','?')} members)</span>
    </div>
    <div style="overflow-x:auto">
    <table style="width:100%;border-collapse:collapse;font-size:0.83em">
        <thead>
            <tr style="background:#f5f5f5;color:#555;font-size:0.8em">
                <th style="padding:4px 8px;text-align:left">Dato</th>
                <th style="padding:4px 8px;text-align:right">P10 (kald)</th>
                <th style="padding:4px 8px;text-align:center">P50 (median)</th>
                <th style="padding:4px 8px;text-align:left">P90 (varm)</th>
                <th style="padding:4px 8px;text-align:left">Usikkerhet</th>
                <th style="padding:4px 8px;text-align:center">Nedbør P50</th>
            </tr>
        </thead>
        <tbody>{rows}</tbody>
    </table>
    </div>
    <p style="font-size:0.75em;color:#aaa;margin-top:4px">
        Kilde: ECMWF ENS 51 members · {ens.get('updated','')}
    </p>"""


# ── EC46 Sub-seasonal ukestrender ─────────────────────────────────────────────

def _ec46_weeks(subseasonal: dict) -> str:
    """Genererer EC46 ukesvis prognose-visning."""
    if not subseasonal or not subseasonal.get("weeks"):
        return '<p style="color:#888;font-size:0.85em">EC46 sub-seasonal data ikke tilgjengelig.</p>'

    weeks = subseasonal["weeks"]
    efi   = subseasonal.get("efi", {})

    week_cards = ""
    for w in weeks:
        t_min = w.get("temp_min")
        t_max = w.get("temp_max")
        t_avg = w.get("temp_avg")
        prec  = w.get("precip_total")

        week_num = w["week"]
        opacity = max(0.4, 1.0 - (week_num - 1) * 0.12)  # avtar med usikkerhet

        week_cards += f"""
        <div style="background:#fff;border:1px solid #e0e0e0;border-radius:8px;padding:12px;
                    min-width:120px;opacity:{opacity:.2f};flex:1">
            <div style="font-size:0.72em;color:#888;margin-bottom:4px">
                Uke {week_num} · {w['date_from']}–{w['date_to']}
            </div>
            <div style="font-size:1.1em;font-weight:700">
                {f'{t_min}–{t_max}°C' if t_min is not None and t_max is not None else '–'}
            </div>
            <div style="font-size:0.8em;color:#666">
                {f'Snitt {t_avg}°C' if t_avg is not None else ''}
            </div>
            <div style="font-size:0.8em;color:#2980b9;margin-top:4px">
                {f'💧 {prec} mm' if prec is not None else ''}
            </div>
        </div>"""

    efi_html = ""
    if efi and efi.get("available"):
        efi_html = f"""
        <div style="background:#fff8e1;border-left:3px solid #f39c12;padding:8px 12px;
                    border-radius:4px;margin-top:12px;font-size:0.85em">
            <strong>⚡ Ensemble-signal:</strong> {efi.get('interpretation','–')}<br>
            <span style="color:#888;font-size:0.85em">{efi.get('note','')}</span>
        </div>"""

    return f"""
    <div style="display:flex;gap:10px;flex-wrap:wrap;overflow-x:auto">
        {week_cards}
    </div>
    {efi_html}
    <p style="font-size:0.75em;color:#aaa;margin-top:8px">
        {subseasonal.get('note','')} · {subseasonal.get('updated','')}
    </p>"""


# ── SEAS5 Sesongprognose ───────────────────────────────────────────────────────

def _seas5_months(seasonal: dict) -> str:
    """Genererer SEAS5 månedlig sesongprognose-visning."""
    if not seasonal or not seasonal.get("months"):
        return '<p style="color:#888;font-size:0.85em">SEAS5 sesongdata ikke tilgjengelig.</p>'

    month_cards = ""
    for i, m in enumerate(seasonal["months"]):
        opacity = max(0.35, 1.0 - i * 0.13)
        t_min = m.get("temp_min")
        t_max = m.get("temp_max")
        prec  = m.get("precip")

        month_cards += f"""
        <div style="background:#fff;border:1px solid #e0e0e0;border-radius:8px;padding:12px;
                    min-width:100px;opacity:{opacity:.2f};flex:1;text-align:center">
            <div style="font-size:0.75em;color:#888;margin-bottom:6px;font-weight:600">
                {m['month_short']}
            </div>
            <div style="font-size:0.95em;font-weight:700">
                {f'{t_min}–{t_max}°C' if t_min is not None and t_max is not None else '–'}
            </div>
            <div style="font-size:0.8em;color:#2980b9;margin-top:4px">
                {f'💧 {prec} mm' if prec is not None else '–'}
            </div>
        </div>"""

    return f"""
    <div style="display:flex;gap:10px;flex-wrap:wrap">
        {month_cards}
    </div>
    <p style="font-size:0.75em;color:#aaa;margin-top:8px">
        {seasonal.get('note','')} · {seasonal.get('updated','')}
    </p>"""


# ── Komplett regionkort med alle ECMWF-lag ────────────────────────────────────

def _ecmwf_region_card(ecmwf_result: dict) -> str:
    """Genererer ett regionkort med alle fire ECMWF-lag."""
    region = ecmwf_result["region"]
    flag = {"IT": "🇮🇹", "ES": "🇪🇸", "PT": "🇵🇹", "MA": "🇲🇦", "NO": "🇳🇴"}.get(
        region.get("country", ""), "🌍")

    hres_html   = _hres_table(ecmwf_result.get("hres"))
    ens_html    = _ens_chart(ecmwf_result.get("ensemble"))
    ec46_html   = _ec46_weeks(ecmwf_result.get("subseasonal"))
    seas5_html  = _seas5_months(ecmwf_result.get("seasonal"))

    ens  = ecmwf_result.get("ensemble") or {}
    conf = ens.get("confidence", "–")
    conf_color = ens.get("confidence_color", "gray")

    return f"""
    <div class="ecmwf-card" style="background:#fff;border:1px solid #e0e0e0;border-radius:12px;
                padding:0;margin-bottom:24px;box-shadow:0 2px 6px rgba(0,0,0,0.06);overflow:hidden">

        <!-- Korthodet -->
        <div style="background:#1a3a2a;color:#fff;padding:14px 20px;
                    display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px">
            <h3 style="margin:0;font-size:1.05em">{flag} {region['name']}</h3>
            <div style="display:flex;gap:8px;align-items:center">
                {_conf_badge(f'ENS: {conf}', conf_color)}
                <span style="font-size:0.78em;opacity:0.7">ECMWF IFS HRES + ENS + EC46 + SEAS5</span>
            </div>
        </div>

        <div style="padding:18px 20px">

            <!-- HRES 15d -->
            <details open>
                <summary style="font-weight:600;cursor:pointer;margin-bottom:10px;font-size:0.95em">
                    📊 ECMWF IFS HRES 9km — 15 dagers prognose
                </summary>
                {hres_html}
            </details>

            <hr style="border:none;border-top:1px solid #f0f0f0;margin:14px 0">

            <!-- ENS -->
            <details>
                <summary style="font-weight:600;cursor:pointer;margin-bottom:10px;font-size:0.95em">
                    🎯 ECMWF ENS — Ensemble usikkerhetsspenn (P10/P50/P90)
                </summary>
                {ens_html}
            </details>

            <hr style="border:none;border-top:1px solid #f0f0f0;margin:14px 0">

            <!-- EC46 Sub-seasonal -->
            <details>
                <summary style="font-weight:600;cursor:pointer;margin-bottom:10px;font-size:0.95em">
                    📅 ECMWF EC46 — Sub-seasonal prognose (uke 1–6)
                </summary>
                {ec46_html}
            </details>

            <hr style="border:none;border-top:1px solid #f0f0f0;margin:14px 0">

            <!-- SEAS5 -->
            <details>
                <summary style="font-weight:600;cursor:pointer;margin-bottom:10px;font-size:0.95em">
                    🌍 ECMWF SEAS5 — Sesongprognose (1–6 måneder)
                </summary>
                {seas5_html}
            </details>

        </div>
    </div>"""


# ── Komplett seksjons-HTML ────────────────────────────────────────────────────

def generate_ecmwf_section_html(ecmwf_results: list, region_type: str = "europe") -> str:
    """
    Genererer komplett HTML for ECMWF-seksjonen.
    region_type: 'europe' eller 'norway'
    """
    now_cet = datetime.now(CET)
    date_str = now_cet.strftime("%-d. %b %Y kl. %H:%M CET")

    title = {
        "europe": "🌍 ECMWF-prognose — Sør-Europa og Nord-Afrika",
        "norway": "🇳🇴 ECMWF-prognose — Norske produksjonsregioner",
    }.get(region_type, "ECMWF-prognose")

    description = {
        "europe": "ECMWF IFS HRES 9km (15 dager) · ENS 51 members · EC46 sub-seasonal (46 dager) · SEAS5 sesongprognose",
        "norway": "ECMWF IFS HRES 9km (15 dager) · ENS ensemble · EC46 (46 dager) · SEAS5 — norske frukt- og grøntregioner",
    }.get(region_type, "")

    # Modell-info-boks
    info_html = f"""
    <div style="background:#e3f2fd;border:1px solid #90caf9;border-radius:10px;
                padding:14px 18px;margin-bottom:24px;font-size:0.88em">
        <strong>ℹ️ Om ECMWF-dataene i denne rapporten</strong><br><br>
        <strong>IFS HRES 9km</strong> — ECMWFs høyoppløsningsmodell. Oppdateres 2× daglig.
        Mest pålitelig dag 1–7, god indikasjon dag 8–15.<br>
        <strong>ENS (51 members)</strong> — Ensemble-system som viser usikkerhetsintervallet (P10–P90).
        Smal spredning = høy confidence. Bred spredning = lav confidence.<br>
        <strong>EC46</strong> — Sub-seasonal prognose opp til 46 dager. Kun sannsynlighetstrender fra uke 3+.<br>
        <strong>SEAS5</strong> — Sesongmodell 1–6 måneder frem. Viser om måneder forventes varmere/kaldere/våtere enn normalt.<br>
        <span style="color:#888">Alle data via <a href="https://open-meteo.com" target="_blank">Open-Meteo</a>
        fra ECMWF Open Data (CC-BY 4.0). Oppdatert {date_str}.</span>
    </div>"""

    cards_html = "".join(_ecmwf_region_card(r) for r in ecmwf_results)

    return f"""
    <div class="ecmwf-section">
        <div class="section-header">
            <h2>{title}</h2>
            <p style="color:#666;margin-top:4px">{description}</p>
        </div>
        {info_html}
        {cards_html}
    </div>"""


# ── Injeksjon i index.html ────────────────────────────────────────────────────

def inject_ecmwf_into_html(html_content: str,
                            europe_results: list,
                            norway_results: list) -> str:
    """
    Injiserer ECMWF-data i index.html via sentinelkommentarer.

    Sentineler som må finnes i index.html:
      <!-- DATA:ECMWF_EUROPE_START --> ... <!-- DATA:ECMWF_EUROPE_END -->
      <!-- DATA:ECMWF_NORWAY_START --> ... <!-- DATA:ECMWF_NORWAY_END -->
    """
    # Europa
    if europe_results:
        europe_html = generate_ecmwf_section_html(europe_results, "europe")
        html_content = re.sub(
            r'<!-- DATA:ECMWF_EUROPE_START -->.*?<!-- DATA:ECMWF_EUROPE_END -->',
            f'<!-- DATA:ECMWF_EUROPE_START -->{europe_html}<!-- DATA:ECMWF_EUROPE_END -->',
            html_content, flags=re.DOTALL
        )
        logger.info(f"Injiserte ECMWF Europa ({len(europe_results)} regioner)")
    else:
        logger.warning("Ingen Europa ECMWF-data å injisere")

    # Norge
    if norway_results:
        norway_html = generate_ecmwf_section_html(norway_results, "norway")
        html_content = re.sub(
            r'<!-- DATA:ECMWF_NORWAY_START -->.*?<!-- DATA:ECMWF_NORWAY_END -->',
            f'<!-- DATA:ECMWF_NORWAY_START -->{norway_html}<!-- DATA:ECMWF_NORWAY_END -->',
            html_content, flags=re.DOTALL
        )
        logger.info(f"Injiserte ECMWF Norge ({len(norway_results)} regioner)")
    else:
        logger.warning("Ingen Norge ECMWF-data å injisere")

    return html_content
