# Changelog

## [1.6.1] - 2026-07-16

### Fixed
- Upgrade auf v1.6.0: Das Upgrade-Skript ergänzt in der `settings.json` jetzt auch `system.backup.directory` sowie alle weiteren von der Anwendung vorausgesetzten `system`-Pflichtfelder, sodass der Start nach dem Upgrade nicht mehr mit „KeyError: 'backup'" abbricht
- Eine unvollständige `settings.json` meldet beim Start jetzt alle fehlenden Pflichtfelder in einer klaren Fehlermeldung statt eines technischen Fehlerabbruchs
- Das Konto-Menü in der Kopfzeile wurde in Chromium-basierten Browsern (Chrome, Edge) abgeschnitten und über den rechten Bildschirmrand hinaus dargestellt; es klappt jetzt browserübergreifend einheitlich rechtsbündig auf

## [1.6.0] - 2026-07-16

### Added
- Automatische Abmeldung bei Inaktivität mit Countdown in der Kopfzeile, Vorwarnung vor Ablauf und Verlängern-Möglichkeit
- Datensicherung per Web-GUI (Admin) und `cli/backup.py`: sichern, prüfen, wiederherstellen
- Geplante tägliche Datensicherung mit Aufbewahrung, Verzeichnis-Sync und Sicherheits-Snapshot vor der Wiederherstellung
- Wählbare Anwendungs-Zeitzone (Admin)
- Dashboard „Heute" und „Diese Woche": Filter nach Name und Kategorie
- Team-Übersicht: Filter nach Person, Kategorie und Vertretung
- Mehrfachauswahl bei den Filtern für Person und Kategorie in Kalender, Team-Übersicht und Liste (auch in den Exporten wirksam)
- PDF-Export weist die angewendeten Filter im Fußbereich aus
- Upgrade-Skript für v1.5.x → v1.6.0

### Changed
- „Letzter Login" zeigt die vorherige statt der aktuellen Sitzung
- Wartungsaufgaben über einen gemeinsamen Scheduler; Einstellungen wirken ohne Neustart
- Fehlende Pflichtfelder in `settings.json` (z. B. `runtime_path`) brechen den Start jetzt mit klarer Fehlermeldung ab
- Login-Felder in der Datenbank umbenannt und ergänzt (Upgrade-Skript übernimmt die Anpassung)
- Dashboard „Diese Woche": nach Wochentag gruppiert und eingeklappt
- Filter (Person, Kategorie, Vertretung) bleiben beim Blättern erhalten und werden samt Zeitausschnitt beim Wechsel zwischen Kalender, Team-Übersicht und Liste übernommen
- Exporte (PDF, iCal, Matrix) übernehmen in allen Ansichten die aktiven Filter
- Einheitliche Breadcrumbs mit Rücksprung zur Herkunft auf Detail-, Bearbeiten- und Erstellen-Seiten
- Lange Namen in Team-Übersicht und Liste einzeilig gekürzt, vollständiger Name als Tooltip
- Listenansicht auf schmalen Bildschirmen als Karten statt Tabelle

### Fixed
- „Datenbank gesperrt" unter Last: Absturz beim Start und Fehler bei gleichzeitigen Schreibzugriffen im Betrieb behoben
- Tooltips wurden am Bildschirmrand abgeschnitten; sie bleiben jetzt vollständig im Bild und brechen sauber um
- Vertretungsprüfung berücksichtigt jetzt Halbtage: Vor- und Nachmittag werden getrennt bewertet, sodass eine halbtägige Abwesenheit keine falschen Vertretungskonflikte für die andere Tageshälfte mehr meldet oder blockiert
- Beim Bearbeiten oder Zurücksetzen einzelner Serientermine wird nun ebenfalls gewarnt, wenn die Vertretung am selben Tag bereits eine andere Person vertritt
- „Abmelden" konnte je nach Umständen mit „Method Not Allowed" (Fehler 405) fehlschlagen; die Abmeldung erfolgt jetzt zuverlässig über ein CSRF-geschütztes Formular ohne JavaScript-Abhängigkeit

### Removed
- Seitenweise Blätterung der Dashboard-Wochenansicht

### Security
- Passwortänderung und Passwort-Reset invalidieren alle bestehenden Sitzungen und „Angemeldet bleiben"-Cookies des betroffenen Kontos
- Reaktivierung eines gesperrten oder verwalteten Kontos invalidiert dessen bestehende Sitzungen und „Angemeldet bleiben"-Cookies
- Sitzungen werden zusätzlich an IP und Browser des Geräts gebunden, sodass ein entwendetes Sitzungs-Cookie auf einem anderen Gerät abgewiesen wird
- Anzeigenamen dürfen keine Steuer- oder Textrichtungs-Zeichen mehr enthalten (Schutz vor Anzeige-Manipulation in Listen, Kalender und Exporten)

## [1.5.0] - 2026-05-04

### Added
- Handbuch: Integrierte Online-Hilfe
- Tooltips an allen Formularfeldern in Anwendung und Handbuch
- Profil: Anzeigename und E-Mail selbst bearbeitbar
- Automatischer Logout bei abgelaufener Sitzung

### Changed
- Anwendungsstruktur: cross-cutting concerns aus `app.py` in eigene `core/`-Module aufgeteilt (Jinja-Filter, Context-Processors, Error-Handler, Session-Hooks, Versions-Konstante)

### Fixed
- Admin konnte Systemeinstellungen nach Remember-Me-Wiederanmeldung nicht öffnen
- Listenansicht-Filter „Mit/Ohne Vertretung" wirkte nicht auf den Export
- „Angemeldet bleiben" für Mitarbeitende führte beim nächsten Aufruf zum sofortigen Abmelden
- Sitzungsablauf wirkte nicht auf Sitzungen, die per „Angemeldet bleiben" wiederhergestellt wurden

## [1.4.1] - 2026-04-14

### Added
- Gunicorn: Konfigurierbarer Runtime-Pfad für PID-Datei und Control Socket
- Upgrade-Skripte: Gebündelte statische SQLite-Binaries (x86_64, aarch64) für Systeme mit SQLite < 3.35

## [1.4.0] - 2026-04-12

### Added
- Kombinierte Halbtage: VM und NM mit verschiedenen Kategorien auf einem Tag darstellbar (Team-Übersicht, PDF-Matrix, Split-Klick auf jeweilige Hälfte)
- Dashboard „Heute": Einträge klickbar zur Detailansicht
- Export in Listenansicht mit Übernahme aller aktiven Filter
- Rollenbasierte Exports: User exportiert nur eigene Daten, Manager/Admin alle
- Änderungshistorie: Admin sieht den Namen des Ändernden
- PDF-Export: Monatliche Seitenaufteilung bei großen Zeiträumen (Liste und Matrix)
- Serientermine: Gelöschte Termine wiederherstellen und geänderte Termine auf Serienwerte zurücksetzen
- Systemeinstellungen: Konfigurierbarer Planungshorizont (1–24 Monate) und Massenlöschungs-Limit
- Upgrade-Script mit Backup, Verify und Restore für v1.3.x → v1.4.0

### Changed
- Tooltips: Konsistentes Verhalten in Firefox und Chrome, Halbtag-Abkürzungen vereinheitlicht (VM/NM)
- Kalender-Legende: Visueller Trenner zwischen Kategorien und Erklärung
- Serien-Formular: Wiederholungsfelder ohne eigene Hintergrundfarbe (article → div)
- PDF-Export: Aufsteigende Sortierung nach Datum
- Recurrence-Exceptions: Konsistentes Override-Flag-Modell (category, substitute, notes)
- Abhängigkeiten aktualisiert (SQLAlchemy, holidays, gunicorn, pip-licenses)

### Fixed
- Tooltips: Zeilenumbrüche in Chrome nicht dargestellt
- Export: Fremde Abwesenheitsdaten über URL-Manipulation exportierbar (IDOR)
- Vertretungsprüfung berücksichtigt jetzt Occurrence-Level Overrides bei Serien
- Occurrence-Bearbeitung: Vertretung wurde im Formular nicht vorausgewählt
- Formular-Validierung: Doppelte validate_end_date überschrieb Enddatum-Prüfung
- Validierung: Wiederkehrende Abwesenheiten ohne Serien-Enddatum wurden falsch als nicht-wiederkehrend behandelt

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
