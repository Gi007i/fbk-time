# Systemd Service Konfiguration (Debian/Ubuntu)

## Übersicht
Der FBK-Time Gunicorn Application Server wird als systemd-Dienst betrieben, um automatischen Start beim Systemboot und Prozessüberwachung zu gewährleisten.

---

## Voraussetzungen

- Python Virtual Environment unter `/var/www/fbk-time/venv/`
- Gunicorn installiert im Virtual Environment
- `gunicorn.conf.py` im Anwendungsverzeichnis

---

## Service-User

Debian/Ubuntu verwendet den bestehenden `www-data` User:

```bash
# Kein zusätzlicher User nötig, www-data existiert bereits

# Anwendungsverzeichnis dem User zuweisen
sudo chown -R www-data:www-data /var/www/fbk-time
```

---

## Service-Datei installieren

```bash
# Service-Datei kopieren
sudo cp /var/www/fbk-time/config/examples/debian/systemd-debian.service.example \
        /etc/systemd/system/fbk-time.service

# Berechtigungen setzen
sudo chmod 644 /etc/systemd/system/fbk-time.service
```

---

## Service aktivieren und starten

```bash
# Systemd neu laden
sudo systemctl daemon-reload

# Service beim Systemstart aktivieren
sudo systemctl enable fbk-time

# Service starten
sudo systemctl start fbk-time

# Status prüfen
sudo systemctl status fbk-time
```

---

## Service-Befehle

```bash
# Status anzeigen
sudo systemctl status fbk-time

# Service starten
sudo systemctl start fbk-time

# Service stoppen
sudo systemctl stop fbk-time

# Service neustarten
sudo systemctl restart fbk-time

# Graceful Reload (ohne Downtime)
sudo systemctl reload fbk-time

# Logs anzeigen
sudo journalctl -u fbk-time -f

# Logs seit letztem Boot
sudo journalctl -u fbk-time -b
```

---

## Troubleshooting

### Service startet nicht

```bash
# Detaillierte Fehlermeldung anzeigen
sudo systemctl status fbk-time
sudo journalctl -u fbk-time -n 50 --no-pager

# Service-Datei Syntax prüfen
sudo systemd-analyze verify /etc/systemd/system/fbk-time.service
```

### Häufige Ursachen

1. **Virtual Environment nicht vorhanden:**
```bash
# Prüfen
ls -la /var/www/fbk-time/venv/bin/gunicorn

# Falls nicht vorhanden, erstellen
cd /var/www/fbk-time
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

2. **Falsche Berechtigungen:**
```bash
sudo chown -R www-data:www-data /var/www/fbk-time
```

3. **gunicorn.conf.py fehlt oder fehlerhaft:**
```bash
# Syntax prüfen
cd /var/www/fbk-time
source venv/bin/activate
python -c "exec(open('gunicorn.conf.py').read())"
```

### Port bereits belegt

```bash
# Prüfen welcher Prozess Port 6000 nutzt
sudo ss -tulpn | grep :6000

# Prozess beenden (falls nötig)
sudo kill -9 <PID>
```

### Python-Fehler

```bash
# Manuell testen
cd /var/www/fbk-time
source venv/bin/activate
gunicorn --config gunicorn.conf.py app:app

# Bei Import-Fehlern: Dependencies prüfen
pip install -r requirements.txt
```

---

## Checkliste nach Installation

```bash
# 1. Berechtigungen setzen
sudo chown -R www-data:www-data /var/www/fbk-time

# 2. Service-Datei kopieren
sudo cp /var/www/fbk-time/config/examples/debian/systemd-debian.service.example \
        /etc/systemd/system/fbk-time.service

# 3. Service aktivieren
sudo systemctl daemon-reload
sudo systemctl enable --now fbk-time

# 4. Status prüfen
sudo systemctl status fbk-time
```

---

## Hinweise

- Standard-Port für Gunicorn ist 6000 (siehe `gunicorn.conf.py`)
- Logs werden ins systemd Journal geschrieben (`journalctl -u fbk-time`)
- Bei Änderungen an der Service-Datei: `systemctl daemon-reload` nicht vergessen
- Service-User: `www-data`
