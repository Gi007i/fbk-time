# Changelog

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
