# IT-Infrastruktur C1 API

## Beschreibung
Ablauf der in dem Paper beschrieben API.\
  Schritt 1: Zahlungseingang (CSV) wird "verbucht" -> SQLite-DB wird aktualisiert\
  Schritt 2: Prüfen ob Fix-/variable Kosten des Monats gedeckt sind (reine DB-Logik)\
  Schritt 3: Wenn Geld übrig ist -> Risikofaktoren async aus dem Web holen
             (Aktienindex + Wirtschafts-News) -> OpenAI wählt 2 Anlagemöglichkeiten
             inkl. Begründung\
  Schritt 4: Ergebnis als JSON + PDF speichern \
\
## Installation\
    1. API.py kopieren und in ein neues Python Projekt einfügen.
    2. Auf Fehlermeldung klicken und alle nötigen Dinge importieren.
    3. API/NewsAPI.org key einfügen. Diese schicke ich Ihnen gerne auf Nachfrage per Mail)
    3. API2.py ausführen. 
Im Anschluss werden alle nötigen Dateien generiert und die OpenAI API ausgeführt.\
