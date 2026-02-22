"""
generate_html.py
----------------
Henter værdata, analyserer og skriver oppdatert index.html.
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


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--week", type=str, default=None,
                   help="Uke-startdato YYYY-MM-DD. Standard: siste fullførte uke.")
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


# ── HTML helpers ──────────────────────────────────────────────────────────────

def risk_pill(level: str) -> str:
    cls = {"Høy": "pill-high", "Moderat": "pill-mod", "Lav": "pill-low"}.get(level, "pill-low")
    return f'<span class="pill {cls}">{level}</span>'


def status_icon(status: str) -> str:
    return f'<span class="status-icon">{status}</span>'


def anom_class(val):
    if val is None: return "anom-neu"
    return "anom-pos" if val > 0 else "anom-neg" if val < 0 else "anom-neu"


def fmt(val, suffix="°C", decimals=1):
    if val is None: return "N/A"
    return f"{val:+.{decimals}f}{suffix}" if val != 0 else f"0{suffix}"


def fmt_range(val, suffix="°C"):
    if val is None: return "N/A"
    return f"{val:.1f}{suffix}"


# ── KPI sub-texts ─────────────────────────────────────────────────────────────

def build_kpi_sub(analyses: list[dict], level: str) -> str:
    """Return comma-separated short region names for a given risk level."""
    regions = [a for a in analyses if a.get("risk_total") == level]
    if not regions:
        if level == "Høy":     return "Ingen høy-risiko regioner"
        if level == "Moderat": return "Ingen moderat-risiko regioner"
        if level == "Lav":     return "Alle regioner normale"
    names = []
    for a in regions:
        rn = a["region_name"]
        short = rn.split("–")[-1].strip() if "–" in rn else rn
        # Shorten long names
        short = short.replace(" / Amalfi", "").replace(" (nord)", "").replace(" (ref.)", "")
        names.append(short)
    return ", ".join(names)


# ── Alert box ─────────────────────────────────────────────────────────────────

def build_alert_box(analyses: list[dict], week_num: int, year: int) -> str:
    """Generate dynamic alert box based on current risk levels."""
    high = [a for a in analyses if a.get("risk_total") == "Høy"]
    mod  = [a for a in analyses if a.get("risk_total") == "Moderat"]

    if not high and not mod:
        return '''<div class="alert-box success">
    <strong>✅ NORMAL FORSYNINGSSITUASJON — UKE {week}/{year}</strong>
    Alle regioner er innenfor normale parametere. Ingen aktive værvarsler eller forstyrrelser
    i produksjon eller transport.
  </div>'''.format(week=week_num, year=year)

    if not high and mod:
        mod_names = ", ".join(
            a["region_name"].split("–")[-1].strip() if "–" in a["region_name"]
            else a["region_name"] for a in mod
        )
        return f'''<div class="alert-box warning">
    <strong>⚡ MODERAT FORSYNINGSRISIKO — UKE {week_num}/{year}</strong>
    {len(mod)} region(er) med moderat risiko: {mod_names}.
    Følg med på utvikling. Ingen kritiske forstyrrelser registrert.
  </div>'''

    # High risk exists — build detailed alert
    high_names = ", ".join(
        a["region_name"].split("–")[-1].strip() if "–" in a["region_name"]
        else a["region_name"] for a in high
    )
    count = len(high)
    simultaneous = "simultant " if count >= 3 else ""

    # Collect key observations from high-risk regions
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

    return f'''<div class="alert-box {box_class}">
    <strong>⚠️ {severity} FORSYNINGSSITUASJON — UKE {week_num}/{year}</strong>
    {count} region(er) {simultaneous}med høy risiko: {high_names}.
    {obs_text}
  </div>'''


# ── Supply chain observation cards ───────────────────────────────────────────

def build_sc_observation_cards(analyses: list[dict]) -> str:
    """
    Generate observation cards based purely on data — no product assumptions.
    One card per high/moderate risk region, plus one summary card if all-low.
    """
    high = [a for a in analyses if a.get("risk_total") == "Høy"]
    mod  = [a for a in analyses if a.get("risk_total") == "Moderat"]
    low  = [a for a in analyses if a.get("risk_total") == "Lav"]

    cards = []

    # Cards for high-risk regions
    for a in high:
        rn  = a["region_name"].split("–")[-1].strip() if "–" in a["region_name"] else a["region_name"]
        rid = a["region_id"]
        cc  = rid.split("_")[0]
        flag = {"IT": "🇮🇹", "ES": "🇪🇸", "PT": "🇵🇹", "MA": "🇲🇦"}.get(cc, "🌍")

        obs = []
        pct = a.get("precip_anomaly_pct")
        td  = a.get("temp_day_anomaly")
        tn  = a.get("temp_night_anomaly")
        rt  = a.get("risk_transport", "Lav")
        rp  = a.get("risk_precip", "Lav")

        if pct is not None:
            obs.append(f"Nedbørsavvik: {pct:+.0f}% vs. normalperiode")
        if td is not None:
            obs.append(f"Dagtemperatur: {td:+.1f}°C vs. normal")
        if tn is not None:
            obs.append(f"Nattetemperatur: {tn:+.1f}°C vs. normal")
        if rt == "Høy":
            obs.append("Transportrisiko: Høy")
        if rp == "Høy":
            obs.append("Nedbørsrisiko: Høy")

        note = a.get("forecast_precip_note", "")
        if note:
            obs.append(note)

        obs_html = "".join(f"<li>{o}</li>" for o in obs) if obs else "<li>Ingen detaljdata tilgjengelig</li>"

        cards.append(f'''<div class="info-card" style="border-left:3px solid #ef4444;">
      <span class="info-card-icon">{flag} ⚠️</span>
      <div class="info-card-title">{rn} — Høy risiko</div>
      <div class="info-card-body"><ul style="margin:0 0 0 1rem;font-size:12px;">{obs_html}</ul></div>
    </div>''')

    # Cards for moderate-risk regions
    for a in mod:
        rn  = a["region_name"].split("–")[-1].strip() if "–" in a["region_name"] else a["region_name"]
        rid = a["region_id"]
        cc  = rid.split("_")[0]
        flag = {"IT": "🇮🇹", "ES": "🇪🇸", "PT": "🇵🇹", "MA": "🇲🇦"}.get(cc, "🌍")

        obs = []
        pct = a.get("precip_anomaly_pct")
        td  = a.get("temp_day_anomaly")

        if pct is not None:
            obs.append(f"Nedbørsavvik: {pct:+.0f}% vs. normalperiode")
        if td is not None:
            obs.append(f"Dagtemperatur: {td:+.1f}°C vs. normal")

        obs_html = "".join(f"<li>{o}</li>" for o in obs) if obs else "<li>Ingen detaljdata</li>"

        cards.append(f'''<div class="info-card" style="border-left:3px solid #f97316;">
      <span class="info-card-icon">{flag} ⚡</span>
      <div class="info-card-title">{rn} — Moderat risiko</div>
      <div class="info-card-body"><ul style="margin:0 0 0 1rem;font-size:12px;">{obs_html}</ul></div>
    </div>''')

    # Low-risk summary card (only if some are low)
    if low:
        low_names = ", ".join(
            a["region_name"].split("–")[-1].strip() if "–" in a["region_name"]
            else a["region_name"] for a in low
        )
        cards.append(f'''<div class="info-card" style="border-left:3px solid #22c55e;">
      <span class="info-card-icon">✅</span>
      <div class="info-card-title">Normale regioner ({len(low)})</div>
      <div class="info-card-body" style="font-size:12px;">{low_names}</div>
    </div>''')

    # Multi-crisis summary card
    if len(high) >= 2:
        cards.append(f'''<div class="info-card" style="border-left:3px solid #dc2626;background:#fff5f5;">
      <span class="info-card-icon">⚠️</span>
      <div class="info-card-title">Simultankrise — {len(high)} regioner</div>
      <div class="info-card-body" style="font-size:12px;">
        {len(high)} høy-risiko regioner aktive samtidig. Se temperatur- og nedbørstabellene for detaljerte avvik per region.
      </div>
    </div>''')

    if not cards:
        cards.append('''<div class="info-card" style="border-left:3px solid #22c55e;">
      <span class="info-card-icon">✅</span>
      <div class="info-card-title">Alle regioner — Normal status</div>
      <div class="info-card-body" style="font-size:12px;">Ingen avvik registrert. Normale forhold i alle overvåkede regioner.</div>
    </div>''')

    return f'<div class="info-grid mt-2">\n' + "\n".join(cards) + "\n</div>"


# ── Build table rows ──────────────────────────────────────────────────────────

def build_overview_rows(analyses: list[dict]) -> str:
    rows = []
    for a in analyses:
        rn = a["region_name"]
        rid = a["region_id"]
        country_flag = {"IT": "🇮🇹 Italia", "ES": "🇪🇸 Spania",
                        "PT": "🇵🇹 Portugal", "MA": "🇲🇦 Marokko"}
        cc = rid.split("_")[0]
        flag = country_flag.get(cc, cc)
        status = a.get("temp_status", "🟢")
        pct = a.get("precip_anomaly_pct")
        pct_str = f"{pct:+.0f}%" if pct is not None else "N/A"
        pct_cls = "anom-pos" if pct and pct > 150 else "anom-neg" if pct and pct < -20 else "anom-neu"
        bg = ' style="background:#fff5f5;"' if a.get("risk_total") == "Høy" else ""

        rows.append(f"""<tr{bg}>
          <td><span class="region-name">{rn.split("–")[-1].strip() if "–" in rn else rn}</span>
              <span class="region-country">{cc}</span></td>
          <td>{flag}</td>
          <td>{status_icon(status)}</td>
          <td class="{pct_cls}">{pct_str}</td>
          <td>{risk_pill(a.get("risk_temp","Lav"))}</td>
          <td>{risk_pill(a.get("risk_precip","Lav"))}</td>
          <td>{risk_pill(a.get("risk_transport","Lav"))}</td>
          <td>{risk_pill(a.get("risk_production","Lav"))}</td>
          <td>{risk_pill(a.get("risk_total","Lav"))}</td>
        </tr>""")
    return "\n".join(rows)


def build_temp_rows(analyses: list[dict]) -> str:
    rows = []
    for a in analyses:
        rn = a["region_name"]
        rid = a["region_id"]
        cc = rid.split("_")[0]
        da = a.get("temp_day_actual")
        dn = a.get("temp_day_normal")
        dd = a.get("temp_day_anomaly")
        na = a.get("temp_night_actual")
        nn = a.get("temp_night_normal")
        nd = a.get("temp_night_anomaly")
        status = a.get("temp_status", "🟢")

        rows.append(f"""<tr>
          <td><span class="region-name">{rn.split("–")[-1].strip() if "–" in rn else rn}</span>
              <span class="region-country">{cc}</span></td>
          <td class="mono">{fmt_range(da)}</td>
          <td class="mono">{fmt_range(dn)}</td>
          <td class="{anom_class(dd)}">{fmt(dd)}</td>
          <td class="mono">{fmt_range(na)}</td>
          <td class="mono">{fmt_range(nn)}</td>
          <td class="{anom_class(nd)}">{fmt(nd)}</td>
          <td>{status_icon(status)}</td>
        </tr>""")
    return "\n".join(rows)


def build_precip_rows(analyses: list[dict]) -> str:
    rows = []
    for a in analyses:
        rn = a["region_name"]
        rid = a["region_id"]
        cc = rid.split("_")[0]
        obs  = a.get("precip_actual_mm")
        norm = a.get("precip_normal_mm")
        anom = a.get("precip_anomaly_mm")
        pct  = a.get("precip_anomaly_pct")
        rp   = a.get("risk_precip", "Lav")
        note = a.get("forecast_precip_note", "")
        pct_cls = "pill-high" if pct and pct > 150 else "pill-mod" if pct and pct > 50 else "pill-cool" if pct and pct < -20 else "pill-low"
        bg = ' style="background:#fff5f5;"' if rp == "Høy" else ""
        obs_str  = f"{obs:.1f}"   if obs  is not None else "N/A"
        norm_str = f"{norm:.1f}"  if norm is not None else "N/A"
        anom_str = f"{anom:+.1f}" if anom is not None else "N/A"
        pct_str  = f"{pct:+.0f}%" if pct  is not None else "N/A"

        rows.append(f"""<tr{bg}>
          <td><span class="region-name">{rn.split("–")[-1].strip() if "–" in rn else rn}</span>
              <span class="region-country">{cc}</span></td>
          <td class="mono">{obs_str}</td>
          <td class="mono">{norm_str}</td>
          <td class="{anom_class(anom)}">{anom_str}</td>
          <td><span class="pill {pct_cls}">{pct_str}</span></td>
          <td>{risk_pill(rp)}</td>
          <td style="font-size:11px; text-align:left;">{note}</td>
        </tr>""")
    return "\n".join(rows)


def build_supply_chain_rows(analyses: list[dict]) -> str:
    rows = []
    for a in analyses:
        rn = a["region_name"]
        rid = a["region_id"]
        cc = rid.split("_")[0]
        bg = ' style="background:#fff5f5;"' if a.get("risk_total") == "Høy" else ""
        rows.append(f"""<tr{bg}>
          <td><span class="region-name">{rn.split("–")[-1].strip() if "–" in rn else rn}</span>
              <span class="region-country">{cc}</span></td>
          <td>{risk_pill(a.get("risk_temp","Lav"))}</td>
          <td>{risk_pill(a.get("risk_precip","Lav"))}</td>
          <td>{risk_pill(a.get("risk_transport","Lav"))}</td>
          <td>{risk_pill(a.get("risk_production","Lav"))}</td>
          <td>{risk_pill(a.get("risk_total","Lav"))}</td>
        </tr>""")
    return "\n".join(rows)


def build_trend_rows(analyses: list[dict]) -> str:
    rows = []
    for a in analyses:
        rn = a["region_name"]
        rid = a["region_id"]
        cc = rid.split("_")[0]
        trend = a.get("trend_direction", "→")
        history = a.get("trend_history", [])
        trend_cls = "trend-up" if trend == "↑" else "trend-down" if trend == "↓" else "trend-flat"
        trend_label = {"↑": "↑ Stigende", "↓": "↓ Fallende", "→": "→ Stabil"}.get(trend, "→ Stabil")

        n = len(history)
        if n >= 2:
            risks = [h.get("risk_total", "Lav") for h in history]
            consensus = ("Vedvarende høy risiko" if risks.count("Høy") >= 2
                         else "Risiko øker" if "Høy" in risks[-2:]
                         else "Stabil / lav risiko")
        else:
            consensus = "Baseline (uke 1)"

        anom_d = a.get("temp_day_anomaly")
        avvik = f"{anom_d:+.1f}°C vs norm" if anom_d is not None else "Baseline"
        avc = anom_class(anom_d)

        spark = build_sparkline(history)
        rows.append(f"""<tr>
          <td><span class="region-name">{rn.split("–")[-1].strip() if "–" in rn else rn}</span>
              <span class="region-country">{cc}</span></td>
          <td><span class="{trend_cls}">{trend_label}</span></td>
          <td>{spark}</td>
          <td>{consensus}</td>
          <td class="{avc}">{avvik}</td>
        </tr>""")
    return "\n".join(rows)


# ── Update HTML ───────────────────────────────────────────────────────────────

def update_html(html: str, analyses: list[dict],
                week_start: date, week_end: date) -> str:

    week_num = week_start.isocalendar()[1]
    year     = week_start.year

    months_no = ["januar","februar","mars","april","mai","juni",
                 "juli","august","september","oktober","november","desember"]
    updated_str = f"Uke {week_num}, {year}"
    data_str    = f"{date.today().day}. {months_no[date.today().month-1]} {date.today().year}"

    cet = timezone(timedelta(hours=1))
    now_cet = datetime.now(cet)
    time_str = now_cet.strftime("%H:%M")
    last_updated_full = f"Uke {week_num}, {year} &nbsp;·&nbsp; Oppdatert {data_str} kl. {time_str} CET"

    # Simple text replacements
    html = re.sub(r"Uke \d+, \d{4}", updated_str, html)
    html = re.sub(r"Rapport nr\. \d+/\d{4}", f"Rapport nr. {week_num}/{year}", html)

    def replace_section(html, start_tag, end_tag, new_content):
        pattern = rf"{re.escape(start_tag)}.*?{re.escape(end_tag)}"
        replacement = f"{start_tag}\n{new_content}\n{end_tag}"
        new_html, n = re.subn(pattern, replacement, html, flags=re.DOTALL)
        if n == 0:
            logger.warning(f"Sentinel not found: {start_tag}")
        return new_html

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

    # ── Last updated ──────────────────────────────────────────────────────────
    html = replace_section(html,
        "<!-- DATA:LAST_UPDATED_START -->", "<!-- DATA:LAST_UPDATED_END -->",
        last_updated_full)

    # ── KPI counts + sub-texts ────────────────────────────────────────────────
    high = sum(1 for a in analyses if a.get("risk_total") == "Høy")
    mod  = sum(1 for a in analyses if a.get("risk_total") == "Moderat")
    low  = sum(1 for a in analyses if a.get("risk_total") == "Lav")

    def repl_kpi(h, sentinel, value):
        return re.sub(rf"<!-- KPI:{sentinel} -->[^<]*",
                      f"<!-- KPI:{sentinel} -->{value}", h)

    html = repl_kpi(html, "HIGH_COUNT", str(high))
    html = repl_kpi(html, "MOD_COUNT",  str(mod))
    html = repl_kpi(html, "LOW_COUNT",  str(low))

    # ── KPI sub-texts (region name lists) ─────────────────────────────────────
    html = replace_section(html,
        "<!-- KPI:HIGH_SUB_START -->", "<!-- KPI:HIGH_SUB_END -->",
        build_kpi_sub(analyses, "Høy"))

    html = replace_section(html,
        "<!-- KPI:MOD_SUB_START -->", "<!-- KPI:MOD_SUB_END -->",
        build_kpi_sub(analyses, "Moderat"))

    html = replace_section(html,
        "<!-- KPI:LOW_SUB_START -->", "<!-- KPI:LOW_SUB_END -->",
        build_kpi_sub(analyses, "Lav"))

    # ── Alert box ─────────────────────────────────────────────────────────────
    html = replace_section(html,
        "<!-- DATA:ALERT_BOX_START -->", "<!-- DATA:ALERT_BOX_END -->",
        build_alert_box(analyses, week_num, year))

    # ── Supply chain observation cards ────────────────────────────────────────
    html = replace_section(html,
        "<!-- DATA:SC_CARDS_START -->", "<!-- DATA:SC_CARDS_END -->",
        build_sc_observation_cards(analyses))

    # ── Confidence grid ──────────────────────────────────────────────────────
    html = replace_section(html,
        "<!-- DATA:CONFIDENCE_START -->", "<!-- DATA:CONFIDENCE_END -->",
        build_confidence_grid(analyses))

    # ── Port/ferry alert ──────────────────────────────────────────────────────
    html = replace_section(html,
        "<!-- DATA:PORT_ALERT_START -->", "<!-- DATA:PORT_ALERT_END -->",
        build_port_alert(analyses))

    # ── News ──────────────────────────────────────────────────────────────────
    articles = fetch_news(max_articles=12)
    news_html = render_news_html(articles)
    html = replace_section(html,
        "<!-- DATA:NEWS_START -->", "<!-- DATA:NEWS_END -->",
        news_html)

    return html


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    args = parse_args()
    week_start, week_end = get_week_bounds(args.week)

    logger.info(f"Generating report for week {week_start} – {week_end}")

    fetched = fetch_all_regions(
        config_path="config/regions.yaml",
        use_cache=not args.no_cache,
    )

    analyses = analyze_all(fetched, config_path="config/regions.yaml")

    narratives = generate_all_narratives(analyses)
    logger.info(f"Narrative mode: {narratives['mode_used']}")

    html_path = Path("index.html")
    if not html_path.exists():
        logger.error("index.html not found.")
        sys.exit(1)

    html = html_path.read_text(encoding="utf-8")
    html = update_html(html, analyses, week_start, week_end)

    logger.info("Henter norske værdata fra met.no...")
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


# ── Confidence grid (dynamic) ─────────────────────────────────────────────────

def build_confidence_grid(analyses: list[dict]) -> str:
    """
    Generate confidence cards based on data availability and risk levels.
    Confidence = Høy if we have actual precip + temp data.
    Sub-seasonal always = Lav (inherent model uncertainty).
    """
    cards = []
    for a in analyses:
        rn  = a["region_name"].split("–")[-1].strip() if "–" in a["region_name"] else a["region_name"]
        rid = a["region_id"]
        cc  = rid.split("_")[0]

        has_precip = a.get("precip_actual_mm") is not None
        has_temp   = a.get("temp_day_actual")  is not None
        risk       = a.get("risk_total", "Lav")

        if has_precip and has_temp:
            level = "Høy"
            cls   = "conf-high"
            sources = {"IT": "ECMWF reanalyse + Open-Meteo",
                       "ES": "AEMET + Open-Meteo",
                       "PT": "IPMA + Open-Meteo",
                       "MA": "DMN + Open-Meteo"}.get(cc, "Open-Meteo")
            note = f"{sources}. Temperatur og nedbør bekreftet."
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

        cards.append(f'''    <div class="confidence-card">
      <div class="confidence-region">{rn} ({cc})</div>
      <span class="confidence-level {cls}">{level}</span>
      <div class="confidence-note">{note}</div>
    </div>''')

    # Always add sub-seasonal card at end
    cards.append('''    <div class="confidence-card">
      <div class="confidence-region">Sub-seasonal (uke 3–6)</div>
      <span class="confidence-level conf-low">Lav</span>
      <div class="confidence-note">Ensemble-spredning øker markant utover uke 2–3. Kun sannsynlighetstrender — ikke hendelsesprognoser.</div>
    </div>''')

    return '<div class="confidence-grid">\n' + "\n".join(cards) + "\n</div>"


# ── Sparkline bars for trend table ───────────────────────────────────────────

def build_sparkline(history: list[dict]) -> str:
    """Build a small bar chart from risk history (last 4 weeks)."""
    if not history:
        return '<div class="sparkbar-wrap"><span style="font-size:10px;color:var(--muted);">Baseline</span></div>'

    bars = []
    max_weeks = 4
    padded = ([None] * (max_weeks - len(history))) + history[-max_weeks:]

    for h in padded:
        if h is None:
            bars.append('<div class="sparkbar sparkbar-neu" style="height:6px;" title="Ingen data"></div>')
        else:
            risk = h.get("risk_total", "Lav")
            pct  = h.get("precip_anomaly_pct", 0) or 0
            height = min(28, max(4, int(abs(pct) / 20)))
            cls = {"Høy": "sparkbar-high", "Moderat": "sparkbar-mod", "Lav": "sparkbar-low"}.get(risk, "sparkbar-neu")
            week = h.get("week", "")
            bars.append(f'<div class="sparkbar {cls}" style="height:{height}px;" title="Uke {week}: {risk}, nedbør {pct:+.0f}%"></div>')

    return '<div class="sparkbar-wrap">' + "".join(bars) + "</div>"


# ── Port / ferry alert box ────────────────────────────────────────────────────

def build_port_alert(analyses: list[dict]) -> str:
    """
    Generate port/ferry status summary from transport risk data.
    Regions with high transport risk → alert.
    """
    high_transport = [a for a in analyses if a.get("risk_transport") == "Høy"]
    mod_transport  = [a for a in analyses if a.get("risk_transport") == "Moderat"]

    if not high_transport and not mod_transport:
        return '''  <div class="alert-box success">
    <strong>✅ Alle transportkorridorer operative</strong>
    Ingen værbetingede transportrisikoer registrert i dag. Normale forhold ved alle overvåkede havner og fergeoverganger.
  </div>'''

    affected = high_transport or mod_transport
    names = ", ".join(
        a["region_name"].split("–")[-1].strip() if "–" in a["region_name"]
        else a["region_name"] for a in affected
    )

    severity = "warning" if not high_transport else "critical"
    icon = "⚡" if not high_transport else "⚠️"
    level = "Moderat" if not high_transport else "Høy"

    return f'''  <div class="alert-box {severity}">
    <strong>{icon} Transportrisiko {level} — regioner berørt: {names}</strong>
    Transportrisiko basert på nedbørsavvik, veistengninger og havneforhold i produksjonsregionene.
    Se tabellene under for detaljer per region.
  </div>'''
