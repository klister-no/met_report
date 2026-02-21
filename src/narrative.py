"""
narrative.py
------------
Generates analytical text for the report.

Two modes (set NARRATIVE_MODE env var or call explicitly):
  "rules"  – deterministic, template-based text (default)
  "claude" – calls Claude API for richer, contextual analysis

The Claude mode requires:
  ANTHROPIC_API_KEY environment variable

Both modes return identical data structure so the report builder
doesn't need to know which mode was used.
"""

import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)

RISK_HIGH     = "Høy"
RISK_MODERATE = "Moderat"
RISK_LOW      = "Lav"

# ── Rules-based narrative ─────────────────────────────────────────────────────

def _temp_sentence(a: dict) -> str:
    anom = a.get("temp_day_anomaly")
    actual = a.get("temp_day_actual")
    status = a.get("temp_status", "🟡")
    frost  = a.get("frost_risk", False)

    if anom is None or actual is None:
        return "Temperaturdata ikke tilgjengelig denne uken."

    if status == "🟢":
        base = f"Temperaturer over normalen (dagsmiddel ca. {actual}°C, avvik {anom:+.1f}°C)."
    elif status == "🔵":
        base = f"Kjøligere enn normalt (dagsmiddel ca. {actual}°C, avvik {anom:+.1f}°C)."
    else:
        base = f"Temperaturer nær klimanormal (dagsmiddel ca. {actual}°C, avvik {anom:+.1f}°C)."

    if frost:
        base += " Nattfrost observert – risiko for frostskade på sensitive kulturer."
    return base


def _precip_sentence(a: dict) -> str:
    actual = a.get("precip_actual_mm")
    pct    = a.get("precip_anomaly_pct")
    risk   = a.get("risk_precip", RISK_LOW)

    if actual is None:
        return "Nedbørsdata ikke tilgjengelig."

    if risk == RISK_HIGH:
        return (f"Ekstremt høy nedbør ({actual:.0f} mm siste uke, "
                f"{pct:+.0f}% avvik fra normalt). "
                "Flomsituasjon medfører alvorlig produksjons- og transportrisiko.")
    elif risk == RISK_MODERATE:
        return (f"Mer nedbør enn normalt ({actual:.0f} mm, {pct:+.0f}%). "
                "Økt risiko for forsinkelser og redusert høstetempo.")
    else:
        return f"Nedbør nær normalt ({actual:.0f} mm)."


def _forecast_sentence(a: dict) -> str:
    direction = a.get("forecast_temp_direction", "→")
    precip    = a.get("forecast_precip_note", "")
    dir_text  = {"↑": "stigende temperaturtendens", "↓": "fallende temperaturtendens",
                 "→": "stabile temperaturer"}.get(direction, "stabile temperaturer")
    return f"Prognose neste 7 dager: {dir_text}. {precip}."


def _risk_sentence(a: dict) -> str:
    total = a.get("risk_total", RISK_LOW)
    crops = a.get("region", {}).get("key_crops", [])
    crop_str = ", ".join(crops[:3]) if crops else "aktuelle kulturer"
    if total == RISK_HIGH:
        return (f"SAMLET RISIKO: HØY. Umiddelbar oppfølging anbefales. "
                f"Kritisk påvirkning på {crop_str}.")
    elif total == RISK_MODERATE:
        return f"Samlet risiko: Moderat. Overvåk situasjonen for {crop_str}."
    else:
        return f"Samlet risiko: Lav. Normale forsyningsforhold for {crop_str}."


def generate_region_text_rules(a: dict) -> str:
    """Combines sentences into a short paragraph for each region."""
    return " ".join([
        _temp_sentence(a),
        _precip_sentence(a),
        _forecast_sentence(a),
        _risk_sentence(a),
    ])


def generate_status_section_rules(analyses: list[dict]) -> str:
    """Builds section 1 (Status nå) as plain text."""
    parts = []
    for a in analyses:
        parts.append(f"**{a['region_name']}**: {generate_region_text_rules(a)}")
    return "\n\n".join(parts)


def generate_strategic_note_rules(analyses: list[dict]) -> str:
    """Generates supply chain strategic note based on risk levels."""
    high_risk = [a for a in analyses if a.get("risk_total") == RISK_HIGH]
    mod_risk  = [a for a in analyses if a.get("risk_total") == RISK_MODERATE]
    low_risk  = [a for a in analyses if a.get("risk_total") == RISK_LOW]

    lines = []

    if high_risk:
        names = ", ".join(a["region_name"] for a in high_risk)
        lines.append(
            f"KRITISKE REGIONER: {names}. "
            "Disse bør prioriteres i innkjøpsoppfølging umiddelbart. "
            "Vurder alternativ sourcing og prisforhandlinger."
        )

    if mod_risk:
        names = ", ".join(a["region_name"] for a in mod_risk)
        lines.append(f"Moderate risikoregioner under overvåkning: {names}.")

    if low_risk:
        names = ", ".join(a["region_name"] for a in low_risk)
        lines.append(f"Normale forsyningsforhold: {names}.")

    # Simultaneous high-risk alert
    if len(high_risk) >= 3:
        lines.append(
            f"\nVARSEL: {len(high_risk)} regioner i høy risiko samtidig. "
            "Simultan forsyningskrise pågår. Vurder beredskapslager og "
            "leverandørdiversifisering snarest mulig."
        )

    return "\n".join(lines)


# ── Claude API narrative ───────────────────────────────────────────────────────

def generate_region_text_claude(a: dict, client=None) -> str:
    """
    Uses Claude API to generate a richer, context-aware analysis paragraph.
    Falls back to rules-based if API call fails.
    """
    if client is None:
        return generate_region_text_rules(a)

    system_prompt = """Du er en analytisk meteorolog og supply chain-rådgiver
for en nordisk frukt- og grøntgrossist. Skriv en kort, presis analyse
(3–5 setninger) for én region basert på dataene du mottar.

Regler:
- Analytisk og faktabasert. Ingen spekulasjon.
- Oppgi avvik fra normal eksplisitt der det er relevant.
- Avslutt med én konkret supply chain-implikasjon.
- Norsk bokmål. Ikke bruk markdown eller lister."""

    user_content = f"""Region: {a['region_name']}
Dagtemperatur: {a.get('temp_day_actual')}°C (normal: {a.get('temp_day_normal')}°C, avvik: {a.get('temp_day_anomaly'):+.1f}°C)
Natttemperatur: {a.get('temp_night_actual')}°C (normal: {a.get('temp_night_normal')}°C, avvik: {a.get('temp_night_anomaly'):+.1f}°C)
Nedbør siste uke: {a.get('precip_actual_mm')} mm (normalt: {a.get('precip_normal_mm')} mm, avvik: {a.get('precip_anomaly_pct'):+.0f}%)
Nattfrost: {a.get('frost_risk')}
Samlet risiko: {a.get('risk_total')}
Prognose temperaturretning: {a.get('forecast_temp_direction')}
Prognose nedbør: {a.get('forecast_precip_note')}"""

    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=300,
            system=system_prompt,
            messages=[{"role": "user", "content": user_content}]
        )
        return response.content[0].text.strip()
    except Exception as e:
        logger.warning(f"Claude API failed for {a['region_name']}, falling back to rules: {e}")
        return generate_region_text_rules(a)


def generate_strategic_note_claude(analyses: list[dict], client=None) -> str:
    """Uses Claude API to generate strategic supply chain note."""
    if client is None:
        return generate_strategic_note_rules(analyses)

    system_prompt = """Du er supply chain-rådgiver for en nordisk grossist
av frukt og grønnsaker. Basert på risikodata for alle regioner, skriv
en konsis strategisk note (5–8 setninger) til ledelsen.

Fokuser på: hvilke regioner som er kritiske, hva det betyr for
varetilgang, og konkrete anbefalinger. Norsk bokmål. Direkte tone."""

    # Build compact summary for each region
    summaries = []
    for a in analyses:
        summaries.append(
            f"{a['region_name']}: Temp-avvik {a.get('temp_day_anomaly', 'N/A')}, "
            f"Nedbørsavvik {a.get('precip_anomaly_pct', 'N/A')}%, "
            f"Risiko {a.get('risk_total', 'N/A')}"
        )

    user_content = "Regioner:\n" + "\n".join(summaries)

    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=500,
            system=system_prompt,
            messages=[{"role": "user", "content": user_content}]
        )
        return response.content[0].text.strip()
    except Exception as e:
        logger.warning(f"Claude API failed for strategic note, falling back: {e}")
        return generate_strategic_note_rules(analyses)


# ── Public entry point ────────────────────────────────────────────────────────

def generate_all_narratives(analyses: list[dict]) -> dict:
    """
    Generates all narrative text needed for the report.

    Respects NARRATIVE_MODE env var:
      "claude" → uses Anthropic API  (requires ANTHROPIC_API_KEY)
      anything else → rules-based (default, no API key needed)

    Returns:
    {
      "region_texts":    {region_id: str, ...},
      "strategic_note":  str,
      "mode_used":       str,
    }
    """
    mode = os.getenv("NARRATIVE_MODE", "rules").lower()
    client = None

    if mode == "claude":
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if api_key:
            try:
                import anthropic
                client = anthropic.Anthropic(api_key=api_key)
                logger.info("Narrative mode: Claude API")
            except ImportError:
                logger.warning("anthropic package not installed. Falling back to rules.")
                mode = "rules"
        else:
            logger.warning("ANTHROPIC_API_KEY not set. Falling back to rules.")
            mode = "rules"

    if mode != "claude":
        logger.info("Narrative mode: rules-based")

    region_texts = {}
    for a in analyses:
        if mode == "claude":
            region_texts[a["region_id"]] = generate_region_text_claude(a, client)
        else:
            region_texts[a["region_id"]] = generate_region_text_rules(a)

    if mode == "claude":
        strategic_note = generate_strategic_note_claude(analyses, client)
    else:
        strategic_note = generate_strategic_note_rules(analyses)

    return {
        "region_texts":   region_texts,
        "strategic_note": strategic_note,
        "mode_used":      mode,
    }
