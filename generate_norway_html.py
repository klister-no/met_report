"""
generate_norway_html.py
Genererer HTML-innhold for Norge-fanen og injiserer det i index.html via sentinelkommentarer.
Kjøres som del av generate_html.py.

Sentineler som må finnes i index.html:
  <!-- DATA:NORWAY_TAB_BUTTON -->     — faneknapp i navigasjonen
  <!-- DATA:NORWAY_SECTION_START --> / <!-- DATA:NORWAY_SECTION_END -->  — seksjonsinnhold
  <!-- DATA:LAST_UPDATED_START --> / <!-- DATA:LAST_UPDATED_END -->      — klokkeslett
"""

from datetime import datetime, timezone, timedelta
import logging

logger = logging.getLogger(__name__)

CET = timezone(timedelta(hours=1))


def format_temp(t):
    if t is None:
        return "–"
    return f"{t:+.1f}°C" if t != 0 else "0°C"


def temp_color(avvik):
    if avvik is None:
        return "#888"
    if avvik >= 2:
        return "#e74c3c"
    if avvik >= 0.5:
        return "#e67e22"
    if avvik <= -2:
        return "#2980b9"
    if avvik <= -0.5:
        return "#5dade2"
    return "#27ae60"


def frost_badge(risk):
    color_map = {"red": "#e74c3c", "orange": "#e67e22", "green": "#27ae60", "blue": "#2980b9", "gray": "#888"}
    color = color_map.get(risk.get("color", "gray"), "#888")
    return f'<span style="background:{color};color:#fff;padding:2px 8px;border-radius:10px;font-size:0.78em;font-weight:600">{risk.get("emoji","")} {risk.get("level","")}</span>'


def season_badge(status):
    color_map = {"green": "#27ae60", "orange": "#e67e22", "red": "#e74c3c", "blue": "#2980b9", "gray": "#888"}
    color = color_map.get(status.get("color", "gray"), "#888")
    return f'<span style="background:{color};color:#fff;padding:2px 8px;border-radius:10px;font-size:0.78em;font-weight:600">{status.get("emoji","")} {status.get("label","")}</span>'


def symbol_to_emoji(symbol: str) -> str:
    """Konverterer met.no symbolkode til emoji."""
    s = symbol.lower().split("_")[0] if symbol else ""
    mapping = {
        "clearsky": "☀️", "fair": "🌤️", "partlycloudy": "⛅",
        "cloudy": "☁️", "fog": "🌫️", "lightrain": "🌦️",
        "rain": "🌧️", "heavyrain": "⛈️", "sleet": "🌨️",
        "snow": "❄️", "heavysnow": "🌨️❄️", "rainandthunder": "⛈️",
        "snowandthunder": "⛈️❄️", "lightrainshowers": "🌦️",
        "rainshowers": "🌧️", "snowshowers": "🌨️",
    }
    for key, emoji in mapping.items():
        if key in s:
            return emoji
    return "🌡️"


def generate_temp_sparkline(forecast_days: list) -> str:
    """CSS-based temperature sparkline for 10-day forecast."""
    if not forecast_days:
        return ""
    days = forecast_days[:10]
    # Normalize temperatures to bar heights (4-32px range)
    temps = [d.get("temp_max", 0) for d in days]
    mn, mx = min(temps), max(temps)
    span = mx - mn if mx != mn else 1
    bars = []
    for d, t in zip(days, temps):
        has_frost = d.get("has_frost", False)
        avvik = d.get("temp_avvik", 0)
        h = int(4 + (t - mn) / span * 28)
        color = "#2563eb" if has_frost else ("#ef4444" if avvik > 2 else "#22c55e" if avvik > -1 else "#3b82f6")
        date_short = d.get("date_display", "")[:5]
        bars.append(
            f'<div style="display:flex;flex-direction:column;align-items:center;gap:2px;">'
            f'<div style="width:18px;height:{h}px;background:{color};border-radius:3px 3px 0 0;" title="{date_short}: {t}°C"></div>'
            f'<div style="font-size:9px;color:#888;white-space:nowrap;">{date_short}</div>'
            f'</div>'
        )
    return (
        f'<div style="margin-top:12px;">'
        f'<div style="font-size:11px;font-weight:600;color:#64748b;margin-bottom:6px;">📊 Temperatur 10 dager (maks °C)</div>'
        f'<div style="display:flex;align-items:flex-end;gap:4px;height:50px;padding-bottom:18px;">'
        + "".join(bars) +
        f'</div>'
        f'<div style="font-size:10px;color:#94a3b8;margin-top:2px;">🔵 Frost &nbsp; 🔴 +2°C over normalt &nbsp; 🟢 Normal</div>'
        f'</div>'
    )


def generate_last_updated_html() -> str:
    """Genererer 'Sist oppdatert'-streng med klokkeslett."""
    now_cet = datetime.now(CET)
    week_no = now_cet.isocalendar()[1]
    date_str = now_cet.strftime("%-d. %b %Y")
    time_str = now_cet.strftime("%H:%M")
    return (
        f'Uke {week_no}, {now_cet.year} &nbsp;·&nbsp; '
        f'Oppdatert {date_str} kl. {time_str} CET'
    )


def generate_norway_overview_table(regions_data: list) -> str:
    """Genererer oversiktstabell for alle norske regioner."""
    rows = ""
    for r in regions_data:
        region = r["region"]
        name = region["name"]
        temp_now = r.get("temp_now")
        temp_min = r.get("temp_min_24h")
        temp_max = r.get("temp_max_24h")
        avvik = r.get("temp_avvik")
        precip = r.get("precip_24h")
        frost = r.get("frost_risk", {})
        season = r.get("season_status", {})

        temp_display = f"{temp_min}–{temp_max}°C" if temp_min is not None and temp_max is not None else "–"
        avvik_color = temp_color(avvik)
        avvik_str = f"{avvik:+.1f}°C" if avvik is not None else "–"
        precip_str = f"{precip} mm" if precip is not None else "–"

        rows += f"""
        <tr>
            <td style="font-weight:600">{name}</td>
            <td style="font-size:0.85em;color:#666">{', '.join(region['key_products'][:3])}</td>
            <td>{temp_display}</td>
            <td style="color:{avvik_color};font-weight:600">{avvik_str}</td>
            <td>{precip_str}</td>
            <td>{frost_badge(frost)}</td>
            <td>{season_badge(season)}</td>
        </tr>"""

    return f"""
    <div class="norway-overview">
        <h3 style="margin-bottom:12px">📊 Oversikt — alle regioner</h3>
        <div style="overflow-x:auto">
        <table class="data-table" style="width:100%;border-collapse:collapse;font-size:0.9em">
            <thead>
                <tr style="background:#1a3a2a;color:#fff">
                    <th style="padding:8px 12px;text-align:left">Region</th>
                    <th style="padding:8px 12px;text-align:left">Nøkkelprodukter</th>
                    <th style="padding:8px 12px;text-align:center">Temp min–maks (24t)</th>
                    <th style="padding:8px 12px;text-align:center">Avvik fra normal</th>
                    <th style="padding:8px 12px;text-align:center">Nedbør (24t)</th>
                    <th style="padding:8px 12px;text-align:center">Frostrisiko</th>
                    <th style="padding:8px 12px;text-align:center">Sesongstatus</th>
                </tr>
            </thead>
            <tbody>
                {rows}
            </tbody>
        </table>
        </div>
        <p style="font-size:0.78em;color:#888;margin-top:8px">
            Temperaturer fra met.no Locationforecast 2.0. Avvik beregnet mot WMO-normaler 1991–2020.
        </p>
    </div>"""


def generate_region_cards(regions_data: list) -> str:
    """Genererer detaljerte regionkort med 10-dagersprognose."""
    cards = ""
    for r in regions_data:
        region = r["region"]
        season = r.get("season_status", {})
        frost = r.get("frost_risk", {})
        forecast = r.get("forecast_days", [])[:10]
        avvik = r.get("temp_avvik")
        avvik_color = temp_color(avvik)
        avvik_str = f"{avvik:+.1f}°C" if avvik is not None else "–"

        # Prognosetabell
        if forecast:
            prog_rows = ""
            for day in forecast:
                frost_mark = " ❄️" if day.get("has_frost") else ""
                avvik_d = day.get("temp_avvik", 0)
                avvik_d_color = temp_color(avvik_d)
                sym_emoji = symbol_to_emoji(day.get("symbol", ""))
                prog_rows += f"""
                <tr style="border-bottom:1px solid #eee">
                    <td style="padding:4px 8px">{day['date_display']}</td>
                    <td style="padding:4px 8px;text-align:center">{sym_emoji}</td>
                    <td style="padding:4px 8px;text-align:center">{day['temp_min']}°C</td>
                    <td style="padding:4px 8px;text-align:center">{day['temp_max']}°C{frost_mark}</td>
                    <td style="padding:4px 8px;text-align:center;color:{avvik_d_color};font-weight:600">{avvik_d:+.1f}°C</td>
                    <td style="padding:4px 8px;text-align:center">{day['precip']} mm</td>
                </tr>"""

            prognose_html = f"""
            <div style="margin-top:16px">
                <h4 style="margin-bottom:8px;font-size:0.95em">📅 10-dagersprognose</h4>
                <div style="overflow-x:auto">
                <table style="width:100%;border-collapse:collapse;font-size:0.85em">
                    <thead>
                        <tr style="background:#f5f5f5;font-size:0.82em">
                            <th style="padding:4px 8px;text-align:left">Dato</th>
                            <th style="padding:4px 8px;text-align:center">Vær</th>
                            <th style="padding:4px 8px;text-align:center">Min</th>
                            <th style="padding:4px 8px;text-align:center">Maks</th>
                            <th style="padding:4px 8px;text-align:center">Avvik</th>
                            <th style="padding:4px 8px;text-align:center">Nedbør</th>
                        </tr>
                    </thead>
                    <tbody>{prog_rows}</tbody>
                </table>
                </div>
            </div>"""
        else:
            prognose_html = "<p style='color:#888;font-size:0.85em'>Prognosedata ikke tilgjengelig.</p>"

        wind_str = f"{r.get('wind_now', '–')} m/s" if r.get('wind_now') is not None else "–"
        precip_str = f"{r.get('precip_24h', '–')} mm" if r.get('precip_24h') is not None else "–"
        frost_h = r.get("frost_hours_24h", 0)

        cards += f"""
        <div class="norway-card" style="background:#fff;border:1px solid #e0e0e0;border-radius:12px;padding:20px;margin-bottom:20px;box-shadow:0 2px 6px rgba(0,0,0,0.06)">
            <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:8px;margin-bottom:12px">
                <div>
                    <h3 style="margin:0;font-size:1.15em">🇳🇴 {region['name']}</h3>
                    <p style="margin:4px 0 0;color:#666;font-size:0.85em">{region['focus']}</p>
                </div>
                <div style="display:flex;gap:8px;flex-wrap:wrap">
                    {season_badge(season)}
                    {frost_badge(frost)}
                </div>
            </div>

            <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:12px;margin-bottom:12px">
                <div style="background:#f8f9fa;border-radius:8px;padding:10px;text-align:center">
                    <div style="font-size:0.75em;color:#888;margin-bottom:4px">Temp nå</div>
                    <div style="font-size:1.4em;font-weight:700">{r.get('temp_now', '–')}°C</div>
                </div>
                <div style="background:#f8f9fa;border-radius:8px;padding:10px;text-align:center">
                    <div style="font-size:0.75em;color:#888;margin-bottom:4px">Avvik fra normal</div>
                    <div style="font-size:1.4em;font-weight:700;color:{avvik_color}">{avvik_str}</div>
                </div>
                <div style="background:#f8f9fa;border-radius:8px;padding:10px;text-align:center">
                    <div style="font-size:0.75em;color:#888;margin-bottom:4px">Nedbør (24t)</div>
                    <div style="font-size:1.4em;font-weight:700">{precip_str}</div>
                </div>
                <div style="background:#f8f9fa;border-radius:8px;padding:10px;text-align:center">
                    <div style="font-size:0.75em;color:#888;margin-bottom:4px">Frosttimer (24t)</div>
                    <div style="font-size:1.4em;font-weight:700">{frost_h} t</div>
                </div>
                <div style="background:#f8f9fa;border-radius:8px;padding:10px;text-align:center">
                    <div style="font-size:0.75em;color:#888;margin-bottom:4px">Vind</div>
                    <div style="font-size:1.4em;font-weight:700">{wind_str}</div>
                </div>
            </div>

            <div style="background:#fffbf0;border-left:3px solid #f39c12;padding:8px 12px;border-radius:4px;margin-bottom:12px;font-size:0.875em">
                {season['emoji']} <strong>Sesong:</strong> {season['detail']}<br>
                {frost['emoji']} <strong>Frost:</strong> {frost['detail']}
            </div>

            {generate_temp_sparkline(forecast)}
            {prognose_html}
        </div>"""

    return cards


def generate_norway_section_html(regions_data: list) -> str:
    """Genererer komplett HTML for Norge-seksjonen."""
    now_cet = datetime.now(CET)
    date_str = now_cet.strftime("%-d. %b %Y kl. %H:%M CET")

    overview_table = generate_norway_overview_table(regions_data)
    region_cards = generate_region_cards(regions_data)

    return f"""
    <div class="section-header">
        <h2>🇳🇴 Norsk Produksjon — Vær &amp; Sesong</h2>
        <p style="color:#666;margin-top:4px;font-size:13px;">
            Temperatur, nedbør, frostrisiko og 10-dagersprognose for norske frukt- og grøntregioner.<br>
            Kilde: <a href="https://api.met.no" target="_blank">met.no Locationforecast 2.0</a> ·
            Avvik mot WMO-normaler 1991–2020 · Oppdatert {date_str}
        </p>
    </div>

    <div style="background:#e8f5e9;border:1px solid #a5d6a7;border-radius:10px;padding:14px 18px;margin-bottom:24px">
        <strong>🗓️ Sesongstatus:</strong>
        Overvåking av vinterforhold, frostdybde og sesongprognose er relevant for planlegging
        av innkjøp, lagervolum og tidspunkt for norsk sesongstart.
        Vestfold og Rogaland er tidligst ute (jordbær fra slutten av mai / juni).
    </div>

    {overview_table}

    <h3 style="margin:28px 0 16px">📍 Regioner — detaljer og 10-dagersprognose</h3>
    {region_cards}

    <div style="background:#f5f5f5;border-radius:8px;padding:14px 18px;margin-top:16px;font-size:0.82em;color:#666">
        <strong>Datakilder:</strong>
        met.no Locationforecast 2.0 · Klimanormaler WMO 1991–2020 · Oppdateres kl. 06:00 og 21:00 CET.
    </div>"""


def inject_norway_into_html(html_content: str, regions_data: list) -> str:
    """
    Injiserer Norge-data i index.html via sentinelkommentarer.
    Håndterer: <!-- DATA:NORWAY_SECTION_START --> ... <!-- DATA:NORWAY_SECTION_END -->
    """
    import re

    norway_html = generate_norway_section_html(regions_data)
    html_content = re.sub(
        r'<!-- DATA:NORWAY_SECTION_START -->.*?<!-- DATA:NORWAY_SECTION_END -->',
        f'<!-- DATA:NORWAY_SECTION_START -->{norway_html}<!-- DATA:NORWAY_SECTION_END -->',
        html_content, flags=re.DOTALL
    )

    return html_content
