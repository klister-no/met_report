"""
generate_html.py
----------------
Henter værdata, analyserer og skriver oppdatert index.html.

Logikk:
  1. Hent data for alle 12 regioner (Open-Meteo + evt. AEMET)
  2. Analyser avvik og risiko
  3. Last inn index.html
  4. Erstatt dynamiske seksjoner (markert med HTML-kommentarer)
  5. Oppdater dato/uke-metadata i header
  6. Skriv tilbake til index.html

HTML-templaten bruker kommentarer for å markere dynamiske seksjoner:
  <!-- DATA:REGION_TABLE_START --> ... <!-- DATA:REGION_TABLE_END -->
  <!-- DATA:LAST_UPDATED --> etc.
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
                   help="Uke-startdato YYYY-MM-DD (mandag). Standard: siste fullførte uke.")
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


# ── Pill HTML helpers ─────────────────────────────────────────────────────────

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
        obs_str  = f"{obs:.1f}"  if obs  is not None else "N/A"
        norm_str = f"{norm:.1f}" if norm is not None else "N/A"
        anom_str = f"{anom:+.1f}" if anom is not None else "N/A"
        pct_str  = f"{pct:+.0f}%" if pct is not None else "N/A"

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

        rows.append(f"""<tr>
          <td><span class="region-name">{rn.split("–")[-1].strip() if "–" in rn else rn}</span>
              <span class="region-country">{cc}</span></td>
          <td><span class="{trend_cls}">{trend_label}</span></td>
          <td>{consensus}</td>
          <td class="{avc}">{avvik}</td>
        </tr>""")
    return "\n".join(rows)


# ── Update metadata in HTML ───────────────────────────────────────────────────

def update_html(html: str, analyses: list[dict],
                week_start: date, week_end: date) -> str:
    """Inject computed data into HTML via placeholder comments."""

    week_num = week_start.isocalendar()[1]
    year     = week_start.year

    months_no = ["januar","februar","mars","april","mai","juni",
                 "juli","august","september","oktober","november","desember"]
    updated_str = f"Uke {week_num}, {year}"
    data_str    = f"{date.today().day}. {months_no[date.today().month-1]} {date.today().year}"

    # ── Klokkeslett ──────────────────────────────────────────────────────────
    from datetime import timezone, timedelta
    cet = timezone(timedelta(hours=1))
    now_cet = datetime.now(cet)
    time_str = now_cet.strftime("%H:%M")
    last_updated_full = f"Uke {week_num}, {year} &nbsp;·&nbsp; Oppdatert {data_str} kl. {time_str} CET"

    # ── Simple text replacements ──────────────────────────────────────────────
    html = re.sub(r"Uke \d+, \d{4}", updated_str, html)
    html = re.sub(r"Rapport nr\. \d+/\d{4}", f"Rapport nr. {week_num}/{year}", html)
    html = re.sub(r"Sist oppdatert: [^\|]+\|", f"Sist oppdatert: {updated_str}  |", html)

    # ── Table row injections (via sentinel comments) ──────────────────────────
    def replace_section(html, start_tag, end_tag, new_content):
        pattern = rf"{re.escape(start_tag)}.*?{re.escape(end_tag)}"
        replacement = f"{start_tag}\n{new_content}\n{end_tag}"
        new_html, n = re.subn(pattern, replacement, html, flags=re.DOTALL)
        if n == 0:
            logger.warning(f"Sentinel not found: {start_tag}")
        return new_html

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

    # ── Klokkeslett-sentinel ──────────────────────────────────────────────────
    html = replace_section(html,
        "<!-- DATA:LAST_UPDATED_START -->", "<!-- DATA:LAST_UPDATED_END -->",
        last_updated_full)

    # ── KPI cards ─────────────────────────────────────────────────────────────
    high = sum(1 for a in analyses if a.get("risk_total") == "Høy")
    mod  = sum(1 for a in analyses if a.get("risk_total") == "Moderat")
    low  = sum(1 for a in analyses if a.get("risk_total") == "Lav")

    def repl_kpi(html, sentinel, value):
        return re.sub(rf"<!-- KPI:{sentinel} -->[^<]*",
                      f"<!-- KPI:{sentinel} -->{value}", html)

    html = repl_kpi(html, "HIGH_COUNT", str(high))
    html = repl_kpi(html, "MOD_COUNT",  str(mod))
    html = repl_kpi(html, "LOW_COUNT",  str(low))

    # ── News section ──────────────────────────────────────────────────────────
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

    # 1. Fetch Europa-data
    fetched = fetch_all_regions(
        config_path="config/regions.yaml",
        use_cache=not args.no_cache,
    )

    # 2. Analyze
    analyses = analyze_all(fetched, config_path="config/regions.yaml")

    # 3. Narratives
    narratives = generate_all_narratives(analyses)
    logger.info(f"Narrative mode: {narratives['mode_used']}")

    # 4. Load HTML template
    html_path = Path("index.html")
    if not html_path.exists():
        logger.error("index.html not found. Place this script in the repo root.")
        sys.exit(1)

    html = html_path.read_text(encoding="utf-8")

    # 5. Inject Europa-data
    html = update_html(html, analyses, week_start, week_end)

    # 6. Hent og injiser Norge-data (met.no)
    logger.info("Henter norske værdata fra met.no...")
    try:
        norway_data = fetch_all_norway_regions()
        html = inject_norway_into_html(html, norway_data)
        logger.info(f"Norge-data injisert for {len(norway_data)} regioner.")
    except Exception as e:
        logger.error(f"Feil ved henting av Norge-data: {e}")

    # 7. Hent og injiser ECMWF-data
    logger.info("Henter ECMWF Europa-regioner...")
    try:
        europe_ecmwf = fetch_ecmwf_all_regions(EUROPE_REGIONS)
        logger.info("Henter ECMWF Norge-regioner...")
        norway_ecmwf = fetch_ecmwf_all_regions(NORWAY_REGIONS)
        html = inject_ecmwf_into_html(html, europe_ecmwf, norway_ecmwf)
        logger.info("ECMWF-data injisert.")
    except Exception as e:
        logger.error(f"Feil ved henting av ECMWF-data: {e}")

    # 8. Write back
    html_path.write_text(html, encoding="utf-8")
    logger.info("index.html updated successfully.")

    # Summary
    high_regions = [a["region_name"] for a in analyses if a.get("risk_total") == "Høy"]
    if high_regions:
        logger.warning(f"HIGH RISK: {', '.join(high_regions)}")


if __name__ == "__main__":
    main()
