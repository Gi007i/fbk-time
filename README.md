# FBK-Time — Fehlzeiten-Buchung und Koordination

![Python Version](https://img.shields.io/badge/python-3.11%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Platform](https://img.shields.io/badge/platform-Linux-lightgrey)

Ein webbasiertes Abwesenheitsmanagement mit rollenbasierter Zugriffskontrolle (RBAC), Vertretungsplanung und Feiertags-Integration. Konzipiert für Intranet-Umgebungen ohne Internet-Zugriff.

## Funktionen

- **Multi-User-System** - Rollenbasierte Zugriffskontrolle (admin, manager, user) mit flexiblen Betriebsmodi (Single/Multi-User)
- **Abwesenheitsverwaltung** - Ganztags-, Halbtags- und benutzerdefinierte Zeiträume mit Vertretungsplanung
- **Wiederkehrende Abwesenheiten** - Automatische Erstellung sich wiederholender Muster mit Ausnahmen
- **Konflikt-Validierung** - Automatische Erkennung überlappender Abwesenheiten und Vertretungs-Konflikte
- **Kategorien-System** - Anpassbare Abwesenheitstypen mit Farben, Icons und Vertretungs-Anforderungen
- **Account-Sicherheit** - Automatische Sperrung, Passwort-Richtlinien, Inaktivitäts-Erkennung
- **Benutzerpräferenzen** - Individuelles Datumsformat, Theme, Paginierung und Feiertagsregion
- **Team-Kalender** - Monatsbasierte Übersicht mit deutscher Feiertags-Integration (alle 16 Bundesländer)
- **Team-Matrix** - Übersicht aller Benutzer-Abwesenheiten auf einen Blick
- **PDF & iCal Export** - Reports mit visuellen Halbtags-Indikatoren
- **Datensicherung** - Manuelle und geplante Backups (Datenbank, `settings.json`, `.env`) mit Integritätsprüfung und CLI-gestützter Wiederherstellung

## Anforderungen

- Python 3.11 oder höher
- SQLite3
- Linux-Server (Debian/Ubuntu oder RHEL/CentOS/Rocky/Alma) für Production
- Nginx (für Production-Deployment)

## Quick Start

Schneller lokaler Test der Anwendung (keine Production-Konfiguration):

```bash
# Repository klonen (GitHub oder GitLab)
git clone https://github.com/Gi007i/fbk-time.git
# oder
git clone https://gitlab.com/Gi007i/fbk-time.git
cd fbk-time

# Virtual Environment erstellen
python3 -m venv venv
source venv/bin/activate

# Abhängigkeiten installieren
pip install -r requirements.txt

# Setup ausführen (erstellt settings.json und .env)
python cli/setup.py init

# Admin-Benutzer erstellen
python cli/manage_user.py create-user admin --role admin

# Gunicorn starten (lokaler Test)
gunicorn --bind 127.0.0.1:5000 --workers 1 app:app

# Browser öffnen: http://localhost:5000
```

**Hinweis:** Für Production-Deployment siehe unten.

## Installation (Production)

### Production-Setup

1. **Repository klonen:**
   ```bash
   # GitHub oder GitLab
   git clone https://github.com/Gi007i/fbk-time.git
   # oder
   git clone https://gitlab.com/Gi007i/fbk-time.git
   cd fbk-time
   ```

2. **Virtual Environment erstellen:**
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```

3. **Abhängigkeiten installieren:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Setup ausführen:**
   ```bash
   python cli/setup.py init
   ```

5. **Admin-Benutzer erstellen:**
   ```bash
   python cli/manage_user.py create-user admin --role admin
   ```

6. **Production-Deployment:**

   Für die Einrichtung von Systemd-Service, Nginx Reverse Proxy und SSL-Zertifikaten siehe [Deployment-Anleitung](docs/deployment/README.md).

### Deployment-Anleitungen

Plattform-spezifische Anleitungen (Systemd, Nginx, SSL, Firewall, SELinux):
- [Debian/Ubuntu](docs/deployment/debian/)
- [RHEL/CentOS/Rocky/Alma](docs/deployment/rhel/)
- [Übersicht](docs/deployment/README.md)

## Verwendung

### Erste Schritte

1. **Anmelden** mit den erstellten Zugangsdaten
2. **Kategorien hinzufügen** (Einstellungen → Kategorien) - Abwesenheitstypen definieren
3. **Benutzer anlegen** - Weitere Benutzer erstellen (Multi-User: mit Login, Single-User: ohne Login)
4. **Abwesenheiten erstellen** - Abwesenheiten mit optionalen Vertretungen erfassen
5. **Kalender anzeigen** - Monatsübersicht mit Feiertagen einsehen
6. **Reports erstellen** - PDF- oder iCal-Dateien exportieren

### Verwaltung

**Benutzerverwaltung über CLI:**

```bash
python cli/manage_user.py create-user <username>              # User erstellen (Standard: user-Rolle)
python cli/manage_user.py create-user <username> --role admin # User mit Admin-Rolle erstellen
python cli/manage_user.py reset-password <username>           # Passwort zurücksetzen
python cli/manage_user.py delete-user <username>              # User löschen
python cli/manage_user.py list-users                          # Alle User auflisten

# Für weitere Optionen:
python cli/manage_user.py --help
```

**Datensicherung über CLI:**

Manuelle Sicherungen, Verifikation und Wiederherstellung der Datenbank inklusive `settings.json` und `.env`. Geplante Sicherungen werden in den Systemeinstellungen aktiviert.

```bash
python cli/backup.py list                                     # Alle Backups anzeigen
python cli/backup.py create --description "Vor Update"        # Backup erstellen
python cli/backup.py verify --all                             # Integrität aller Backups prüfen
python cli/backup.py cleanup                                  # Alte Backups gemäß Aufbewahrung entfernen
python cli/backup.py restore <ID>                             # Datenbank wiederherstellen (Dienst muss gestoppt sein)

# Für weitere Optionen:
python cli/backup.py --help
```

## Konfiguration

**`settings.json`** - System-Einstellungen (Datenbank-Pfad, Server-Einstellungen, Sicherheits-Parameter)

**`.env`** - Geheimer Schlüssel für Session-Verschlüsselung

**Betriebsmodi:**
- `single_user` - Admin verwaltet Benutzer ohne separate Logins (Standard)
- `multi_user` - Jeder Benutzer hat eigenen Login-Zugang

Detaillierte Konfigurations-Optionen: Siehe [Deployment-Dokumentation](docs/deployment/README.md)

## Dokumentation

- [Deployment-Anleitung](docs/deployment/README.md) - Production-Deployment-Anleitungen
- [Konfigurations-Beispiele](config/README.md) - Config-Datei-Templates

## Lizenz

MIT License - siehe [LICENSE](LICENSE)

## Danksagungen

- [Pico CSS](https://picocss.com/) - Minimales CSS-Framework
- [Flask](https://flask.palletsprojects.com/) - Python Web-Framework
- [holidays library](https://pypi.org/project/holidays/) - Feiertagsdaten
- [ReportLab](https://www.reportlab.com/) - PDF-Generierung

## Support

Für Deployment-Probleme siehe die Troubleshooting-Abschnitte in den [Deployment-Anleitungen](docs/deployment/).
