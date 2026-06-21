"""
================================================================================
 POC: Automatisierte Investitionsentscheidung mit der OpenAI API
 (Uni-Proof-of-Concept – bewusst MINIMAL, nicht produktionsreif)
================================================================================

ABLAUF
  Schritt 1: Zahlungseingang (CSV) wird "verbucht" -> SQLite-DB wird aktualisiert
  Schritt 2: Prüfen ob Fix-/variable Kosten des Monats gedeckt sind (reine DB-Logik)
  Schritt 3: Wenn Geld übrig ist -> Risikofaktoren async aus dem Web holen
             (Aktienindex + Wirtschafts-News) -> OpenAI wählt 2 Anlagemöglichkeiten
             inkl. Begründung
  Schritt 4: Ergebnis als JSON + PDF speichern

================================================================================
"""

import os
import csv
import json
import time
import sqlite3
import asyncio
from datetime import datetime

import aiohttp
from openai import OpenAI
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

# --------------------------------------------------------------------------- #
# Konfiguration
# --------------------------------------------------------------------------- #
DB_PATH  = "finanzen.db"
CSV_PATH = "zahlungseingang.csv"
DOC_PATH = "interne_lage.txt"
JSON_OUT = "investitionsentscheidung.json"
PDF_OUT  = "investitionsentscheidung.pdf"

MODEL = "gpt-4o-mini"
SEED  = 42              # für reproduzierbare Outputs (ersetzt KEIN Gedächtnis!)

OPENAI_API_KEY = ""   # <-- API Key eintragen!
NEWSAPI_KEY    = ""

client = OpenAI(api_key=OPENAI_API_KEY)


# --------------------------------------------------------------------------- #
# Schmale Retry-Logik – bewusst NUR an dieser einen Stelle im Skript
# (für den teuersten/kritischsten Call: die OpenAI-Anfrage)
# --------------------------------------------------------------------------- #
def call_with_retry(fn, retries=2, *args, **kwargs):
    for attempt in range(retries + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            if attempt == retries:
                raise
            print(f"   ⚠️  OpenAI-Call fehlgeschlagen ({e}) – Retry {attempt+1}/{retries} ...")
            time.sleep(1.5 * (attempt + 1))


# --------------------------------------------------------------------------- #
# SCHRITT 0: Minimaler DB-Aufbau + Beispieldaten (nur falls DB noch leer ist)
# --------------------------------------------------------------------------- #
def setup_db():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
                       CREATE TABLE IF NOT EXISTS kunden (
                                                             kundennummer TEXT PRIMARY KEY,
                                                             name TEXT
                       );
                       CREATE TABLE IF NOT EXISTS rechnungen (
                                                                 rechnungsnummer TEXT PRIMARY KEY,
                                                                 kundennummer TEXT,
                                                                 betrag REAL,
                                                                 monat TEXT,
                                                                 bezahlt INTEGER DEFAULT 0
                       );
                       CREATE TABLE IF NOT EXISTS kosten (
                                                             monat TEXT PRIMARY KEY,
                                                             fixkosten REAL,
                                                             variable_kosten REAL
                       );
                       """)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM kunden")
    if cur.fetchone()[0] == 0:
        monat = datetime.now().strftime("%Y-%m")
        cur.executemany("INSERT INTO kunden VALUES (?,?)", [
            ("K-001", "Müller GmbH"),
            ("K-002", "Schmidt AG"),
        ])
        # Bereits eingegangene Zahlungen diesen Monat (Kosten fast, aber noch
        # nicht ganz gedeckt -> der neue CSV-Zahlungseingang macht den Unterschied)
        cur.executemany("INSERT INTO rechnungen VALUES (?,?,?,?,?)", [
            ("R-1001", "K-001", 7000.0, monat, 1),
            ("R-1002", "K-002", 4000.0, monat, 1),
        ])
        cur.execute("INSERT INTO kosten VALUES (?,?,?)", (monat, 8000.0, 4000.0))
        conn.commit()
        print(f"📦 Beispieldaten angelegt ({monat}): 11.000 € bereits eingegangen, "
              f"Kosten = 12.000 € -> noch nicht ganz gedeckt.")
    conn.close()


# --------------------------------------------------------------------------- #
# SCHRITT 1: CSV (= simulierter Zahlungseingang/Trigger) einlesen + DB updaten
# --------------------------------------------------------------------------- #
def verbuche_zahlungseingang():
    if not os.path.exists(CSV_PATH):
        with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["kundennummer", "rechnungsnummer", "betrag"])
            w.writerow(["K-001", "R-1003", "6000.0"])
        print(f"📄 Beispiel-CSV '{CSV_PATH}' erzeugt.")

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    monat = datetime.now().strftime("%Y-%m")

    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            cur.execute("SELECT name FROM kunden WHERE kundennummer=?", (row["kundennummer"],))
            kunde = cur.fetchone()
            if not kunde:
                print(f"   ❌ Unbekannte Kundennummer {row['kundennummer']} -> übersprungen")
                continue
            betrag = float(row["betrag"])
            cur.execute(
                "INSERT OR REPLACE INTO rechnungen VALUES (?,?,?,?,1)",
                (row["rechnungsnummer"], row["kundennummer"], betrag, monat),
            )
            print(f"   ✅ Zahlung verbucht: {kunde[0]} | {row['rechnungsnummer']} | {betrag:.2f} €")

    conn.commit()
    conn.close()
    return monat


# --------------------------------------------------------------------------- #
# SCHRITT 2.1: Finanzlage prüfen (reine DB-Logik, KEIN LLM-Call -> spart Tokens)
# --------------------------------------------------------------------------- #
def pruefe_finanzlage(monat):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT SUM(betrag) FROM rechnungen WHERE monat=? AND bezahlt=1", (monat,))
    eingaenge = cur.fetchone()[0] or 0.0
    cur.execute("SELECT fixkosten, variable_kosten FROM kosten WHERE monat=?", (monat,))
    row = cur.fetchone()
    conn.close()
    fix, variabel = row if row else (0.0, 0.0)
    ueberschuss = eingaenge - (fix + variabel)

    # "Interne Dokumente" – bewusst nur eine kurze Textdatei, damit nicht
    # unnötig viele Tokens für den späteren LLM-Call anfallen
    if not os.path.exists(DOC_PATH):
        with open(DOC_PATH, "w", encoding="utf-8") as f:
            f.write("Liquiditätsreserve stabil. Keine offenen Kredite. "
                    "Keine größeren geplanten Anschaffungen in den nächsten 6 Monaten.")
    interne_lage = open(DOC_PATH, encoding="utf-8").read()

    print(f"💰 Finanzlage {monat}: Eingänge={eingaenge:.2f} € | "
          f"Kosten={fix+variabel:.2f} € | Überschuss={ueberschuss:.2f} €")
    return ueberschuss, interne_lage


# --------------------------------------------------------------------------- #
# SCHRITT 2.2: Risikofaktoren ASYNCHRON & PARALLEL aus dem Web holen.
# Bewusst nur 2 schlanke, kostenlose Quellen für den POC.
# --------------------------------------------------------------------------- #
async def hole_aktienkurs(session):
    try:
        # stooq.com liefert ohne API-Key eine einfache CSV-Quote (hier: DAX)
        # Hinweis: Symbol ggf. anpassen, je nach Verfügbarkeit bei stooq
        url = "https://stooq.com/q/l/?s=%5Edax&f=sd2t2ohlcv&h&e=csv"
        async with session.get(url, timeout=5) as r:
            text = await r.text()
            return text.strip().splitlines()[-1]
    except Exception as e:
        return f"(Kursdaten nicht verfügbar: {e})"


async def hole_news(session):
    if not NEWSAPI_KEY or "HIER" in NEWSAPI_KEY:
        return ["(kein NEWSAPI_KEY eingetragen – Demo-Headline genutzt)"]
    try:
        # WICHTIG: /v2/top-headlines unterstützt beim 'country'-Parameter laut
        # aktueller Doku nur noch 'us' -> country=de liefert seit einem NewsAPI-
        # Update 0 Artikel (kein Fehler!). Daher hier /v2/everything nutzen,
        # das nicht auf US beschränkt ist und 'language' unterstützt.
        url = ("https://newsapi.org/v2/everything?"
               "q=Wirtschaft%20OR%20Investition%20OR%20Boerse"
               f"&language=de&sortBy=publishedAt&pageSize=3&apiKey={NEWSAPI_KEY}")
        async with session.get(url, timeout=5) as r:
            data = await r.json()
            if data.get("status") != "ok":
                print(f"   ⚠️  NewsAPI-Fehler: {data.get('code')} - {data.get('message')}")
                return [f"(NewsAPI-Fehler: {data.get('message')})"]
            return [a["title"] for a in data.get("articles", [])][:3]
    except Exception as e:
        return [f"(News nicht verfügbar: {e})"]


async def hole_risikofaktoren():
    async with aiohttp.ClientSession() as session:
        kurs, news = await asyncio.gather(hole_aktienkurs(session), hole_news(session))
    print(f"🌐 Marktdaten geholt: 1 Aktienkurs-Quelle + {len(news)} News-Quelle(n)")
    print(f"   News-Inhalt: {news}")  # zeigt direkt, ob echte Headlines oder Fallback/Fehler
    return kurs, news


# --------------------------------------------------------------------------- #
# SCHRITT 3: OpenAI entscheidet sich für 2 Investitionsmöglichkeiten.
# WICHTIG: Es wird bewusst KEIN Gesprächsverlauf gespeichert/wiederverwendet
# -> jeder Call ist "stateless" (Seed nur für Reproduzierbarkeit DIESES Calls,
# kein Kontext-Gedächtnis früherer Entscheidungen).
# --------------------------------------------------------------------------- #
def treffe_investitionsentscheidung(ueberschuss, interne_lage, kurs, news):
    # Statischer System-Prompt vorne -> kann von OpenAI automatisch gecacht
    # werden, solange er sich über mehrere Calls hinweg nicht ändert
    # (Prompt Caching = schneller & günstiger bei wiederholten Testläufen)
    system_prompt = (
        "Du bist ein Investitionsberater für ein kleines Unternehmen. Schlage "
        "KONKRETE Anlagemöglichkeiten vor (keine generischen Floskeln wie "
        "'Tagesgeld' oder 'breit gestreuter ETF' ohne Kontext) - z.B. einen "
        "bestimmten ETF/Branchen-Fonds/Anleihetyp mit Ticker oder Namen, "
        "passend zur aktuellen Marktlage. Begründe jede Option explizit mit: "
        "(a) was die übergebenen News dazu nahelegen, (b) was historisch in "
        "vergleichbaren Marktphasen zu erwarten war, (c) was die aktuelle "
        "finanzielle Lage des Unternehmens für diese Wahl spricht, und (d) "
        "einer nachprüfbaren Quelle (z.B. Name der Nachrichtenquelle, Index, "
        "oder Webseite, wo man mehr dazu lesen kann). Antworte ausschließlich "
        "als JSON mit den Feldern: risiko_einschaetzung (kurz), "
        "option_1 {titel, begruendung, quelle}, "
        "option_2 {titel, begruendung, quelle}. Keine weiteren Erklärungen."
    )
    user_prompt = (
        f"Verfügbarer Überschuss: {ueberschuss:.2f} EUR.\n"
        f"Interne Lage: {interne_lage}\n"
        f"Marktindikator (DAX, roh): {kurs}\n"
        f"Aktuelle Wirtschafts-News: {news}\n"
        "Schlage 2 konkrete, unterschiedliche Anlagemöglichkeiten für den "
        "Überschuss vor (siehe Anweisungen im System-Prompt zur Spezifität "
        "und Begründungstiefe)."
    )

    response = call_with_retry(
        client.chat.completions.create,
        2,  # retries
        model=MODEL,
        seed=SEED,
        temperature=0.3,
        max_tokens=600,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )

    usage = response.usage
    print(f"🤖 OpenAI-Antwort erhalten | Modell={MODEL} | "
          f"Tokens: prompt={usage.prompt_tokens}, "
          f"completion={usage.completion_tokens}, gesamt={usage.total_tokens}")

    entscheidung = json.loads(response.choices[0].message.content)

    print(f"📊 Risiko-Einschätzung: {entscheidung.get('risiko_einschaetzung')}")
    for key in ("option_1", "option_2"):
        opt = entscheidung.get(key, {})
        print(f"   {key}: {opt.get('titel')}")
        print(f"      Begründung: {opt.get('begruendung')}")
        print(f"      Quelle:     {opt.get('quelle')}")

    return entscheidung


# --------------------------------------------------------------------------- #
# SCHRITT 4: Ergebnis als JSON + PDF ausgeben
# --------------------------------------------------------------------------- #
def speichere_ergebnis(entscheidung, ueberschuss):
    daten = {
        "datum": datetime.now().isoformat(timespec="seconds"),
        "ueberschuss_eur": round(ueberschuss, 2),
        **entscheidung,
    }
    with open(JSON_OUT, "w", encoding="utf-8") as f:
        json.dump(daten, f, ensure_ascii=False, indent=2)
    print(f"💾 JSON gespeichert: {JSON_OUT}")

    c = canvas.Canvas(PDF_OUT, pagesize=A4)
    y = 800
    c.setFont("Helvetica-Bold", 14)
    c.drawString(50, y, "Investitionsentscheidung")
    c.setFont("Helvetica", 10)
    y -= 30
    for zeile in json.dumps(daten, ensure_ascii=False, indent=2).split("\n"):
        c.drawString(50, y, zeile[:100])
        y -= 14
        if y < 50:
            c.showPage()
            y = 800
    c.save()
    print(f"📑 PDF gespeichert: {PDF_OUT}")


# --------------------------------------------------------------------------- #
# Hauptablauf
# --------------------------------------------------------------------------- #
def main():
    print("=" * 70)
    setup_db()

    print("\n--- SCHRITT 1: Zahlungseingang verbuchen ---")
    monat = verbuche_zahlungseingang()

    print("\n--- SCHRITT 2.1: Finanzlage prüfen ---")
    ueberschuss, interne_lage = pruefe_finanzlage(monat)

    if ueberschuss <= 0:
        print("ℹ️  Kosten noch nicht gedeckt -> keine Investitionsentscheidung nötig.")
        return

    print("\n--- SCHRITT 2.2: Risikofaktoren holen (async) ---")
    kurs, news = asyncio.run(hole_risikofaktoren())

    print("\n--- SCHRITT 3: Investitionsentscheidung (OpenAI) ---")
    entscheidung = treffe_investitionsentscheidung(ueberschuss, interne_lage, kurs, news)

    print("\n--- SCHRITT 4: Ergebnis speichern ---")
    speichere_ergebnis(entscheidung, ueberschuss)

    print("\n✅ Ablauf abgeschlossen.")
    print("=" * 70)


if __name__ == "__main__":
    main()
