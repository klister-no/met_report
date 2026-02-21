# Klimarisikorapport — Frukt & Grønt Supply Chain

Ukentlig, automatisk oppdatert klimarisikorapport for frukt- og grøntgrossister
med innkjøp fra Sør-Europa og Nord-Afrika. Publisert via **GitHub Pages**.

**Live rapport:** `https://DIN-ORG.github.io/klimarisiko-rapport/`

---

## Hvordan det fungerer

```
Open-Meteo API ──┐
AEMET API      ──┼──▶  generate_html.py  ──▶  index.html  ──▶  GitHub Pages
                 │         (Python)              (HTML)           (Nettside)
Historikk-cache ─┘
```

Hvert **mandag kl. 07:00 CET** kjøres en GitHub Actions-workflow som:
1. Henter ferske værdata for alle 12 regioner
2. Beregner temperatur- og nedbørsavvik mot klimanormaler
3. Klassifiserer risiko (Lav / Moderat / Høy) per region
4. Injiserer data i `index.html`
5. Pusher oppdatert fil tilbake til repoet
6. GitHub Pages publiserer automatisk den nye versjonen

---

## Oppsett

### 1. Fork / klon repoet til din GitHub-organisasjon

```bash
git clone https://github.com/DIN-ORG/klimarisiko-rapport.git
cd klimarisiko-rapport
```

### 2. Aktiver GitHub Pages

Gå til: **Settings → Pages → Source: Deploy from a branch → Branch: main / (root)**

Rapporten blir tilgjengelig på `https://DIN-ORG.github.io/klimarisiko-rapport/`

### 3. Legg inn GitHub Secrets (valgfritt, men anbefalt for Spania)

**Settings → Secrets and variables → Actions → New repository secret**

| Secret | Beskrivelse |
|--------|-------------|
| `AEMET_API_KEY` | Gratis API-nøkkel fra [opendata.aemet.es](https://opendata.aemet.es). Forbedrer datakvalitet for spanske regioner (Huelva, Sevilla, Murcia, Almería, Madrid). |
| `ANTHROPIC_API_KEY` | Valgfritt: aktiverer Claude-generert analysetekst. |

Uten `AEMET_API_KEY` brukes Open-Meteo for alle regioner (fortsatt god kvalitet).

### 4. Verifiser at Actions kjører

Gå til **Actions → Weekly Climate Risk Report → Run workflow** for å teste manuelt.

---

## Filstruktur

```
klimarisiko-rapport/
├── .github/
│   └── workflows/
│       └── weekly_report.yml    # GitHub Actions cron
├── src/
│   ├── data_fetcher.py          # Open-Meteo + AEMET
│   ├── analyzer.py              # Avvik og risikoklassifisering
│   └── narrative.py             # Tekst (regler eller Claude API)
├── config/
│   └── regions.yaml             # 12 regioner med normaler og terskler
├── data/
│   └── history/                 # Historikk for trendanalyse (gitignored)
├── generate_html.py             # Injiserer data i index.html
├── index.html                   # Rapporten — GitHub Pages serverer denne
├── requirements.txt
└── README.md
```

---

## Legge til eller endre regioner

Rediger `config/regions.yaml`. **Ikke endre ID eller rekkefølge** på eksisterende
regioner — dette bryter trendserien.

```yaml
- id: ES_VALENCIA
  name: "ES – Valencia"
  country: Spain
  lat: 39.47
  lon: -0.38
  aemet_station: "8416Y"
  key_crops: [oranges, clementines]
  normals:
    temp_day:   [15,16,18,21,25,29,32,33,30,24,19,15]
    temp_night: [ 6, 7, 9,11,15,19,22,22,19,14,10, 7]
    precip_mm:  [38,32,28,34,36,22, 8,14,42,58,44,40]
```

---

## Aktivere Claude-analyse (valgfritt)

Sett `ANTHROPIC_API_KEY` som GitHub Secret og velg `claude` i workflow-parameteret.
Systemet faller automatisk tilbake til regelbasert tekst hvis API-kallet feiler.

---

## Sammenligning med emballasjeprisrapporten

| Feature | Emballasjeprisrapport | Klimarisikorapport |
|---------|----------------------|-------------------|
| Oppdateringsfrekvens | Månedlig / manuell | Ukentlig / automatisk |
| Datakilde | Manuell innsamling | Open-Meteo API (automatisk) |
| Tab-navigasjon | ✓ | ✓ |
| Tabeller med fargesignaler | ✓ | ✓ |
| GitHub Pages | ✓ | ✓ |
| Historisk trendsporing | — | ✓ (data/history/) |
