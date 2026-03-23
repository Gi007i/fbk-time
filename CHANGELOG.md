# Changelog

## [1.3.0] - 2026-03-22

### Changed
- Abhängigkeiten aktualisiert (Flask, SQLAlchemy, holidays, reportlab, icalendar, gunicorn, pip-licenses)

### Added
- Kalender: Tooltip bei „+X weitere" zeigt versteckte Einträge
- Versionsanzeige im Footer auf allen Seiten
- Pagination für Dashboard-Wochenansicht
- Einstellung „5 Einträge pro Seite" für alle Listenansichten

### Fixed
- Login-Redirect-Loop bei defektem Session-Cookie (z.B. voller Cookie-Speicher im Browser)
- Vertretungs-Konflikte ignorierten Anwesend-Kategorien (z.B. Home Office fälschlich als Konflikt gemeldet)
- Dashboard-Warnungen für Vertretungskonflikte und Doppelbelegung bei Anwesend-Kategorien unterdrückt
- iCal-Export: Anwesend-Kategorien als „Frei", Abwesend-Kategorien als „Außer Haus" in Outlook
- Dashboard „Heute" und „Diese Woche" zeigten mehrtägige Abwesenheiten nur mit Startdatum statt tageweise
- Dashboard ignorierte wiederkehrende Abwesenheiten
- Pagination-Optionen zwischen User- und Admin-Einstellungen nicht identisch
- Pagination-Infotext nicht zentriert unter Navigation
- E-Mail-Validierung verursachte Fehler 500 (fehlende `email_validator` Abhängigkeit durch eigenen Regex-Validator ersetzt)
- CSRF-Token lief nach 1 Stunde ab — Anmeldung zeigte Fehler 400 statt hilfreicher Meldung
- Dynamische Kategorie-CSS lieferte veralteten `max-age=3600` Cache-Header
- Feiertagsnamen auf Servern mit englischer Locale in Englisch statt Deutsch angezeigt
- Team-Übersicht: Feiertage und Wochenenden ohne Hintergrundfarbe bei Überlappung mit Today- oder Holiday-Klassen (CSS-Spezifität)

## [1.2.0] - 2026-03-09

### Added
- Dual-Plattform-Verfügbarkeit integriert.

## [1.1.1] - 2026-02-26

### Added
- CI/CD-Pipelines für automatische Releases

## [1.1.0] - 2026-02-20

### Added
- Fresh-Session-Prüfung für sensible Operationen
- POST-basierter Logout mit CSRF-Schutz
- Serverseitige Abwesenheits-Konfliktvalidierung
- IP-basiertes Login-Throttling (Defense-in-depth)
- ProxyFix für korrekte IP-Auflösung hinter Nginx

### Fixed
- Model-Import vor `db.create_all()` korrigiert (alle Models werden nun korrekt registriert)
- Race-Conditions bei gleichzeitiger Abwesenheits-Erstellung
- XSS-Schwachstellen in Kalender und Toast-Komponente
- Input-Validierung für URL-Parameter gehärtet
- User-Enumeration bei Registrierung verhindert
- Login-Throttling blockiert keine Worker-Threads mehr
- Bulk-Delete prüft nun Eigentümerschaft pro Eintrag
- Bulk-Delete Antwort-Zähler im Frontend korrigiert

### Changed
- Views/Services-Architektur standardisiert (strikte Trennung HTTP-Handling vs. Business-Logik)
- Nginx Security-Headers gehärtet und vereinheitlicht
- Login-Fehlermeldungen geben keine Versuchsanzahl mehr preis
- PID-File nach `data/gunicorn.pid` verschoben

### Removed
- Paket-Versionsinformationen aus Lizenzanzeige entfernt

## [1.0.0] - 2026-01-24

- Erste stabile Veröffentlichung
