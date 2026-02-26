# Systemd Service Konfiguration (RHEL)

## Übersicht
Der FBK-Time Gunicorn Application Server wird als systemd-Dienst betrieben, um automatischen Start beim Systemboot und Prozessüberwachung zu gewährleisten.

---

## Voraussetzungen

- Python Virtual Environment unter `/var/www/fbk-time/venv/`
- Gunicorn installiert im Virtual Environment
- `gunicorn.conf.py` im Anwendungsverzeichnis

---

## Berechtigungen setzen

FBK-Time läuft unter dem `nginx`-User, der bei der Nginx-Installation automatisch erstellt wird:

```bash
# Anwendungsverzeichnis dem Nginx-User zuweisen
sudo chown -R nginx:nginx /var/www/fbk-time
```

---

## Service-Datei installieren

```bash
# Service-Datei kopieren
sudo cp /var/www/fbk-time/config/examples/rhel/systemd-rhel.service.example \
        /etc/systemd/system/fbk-time.service

# Berechtigungen setzen
sudo chmod 644 /etc/systemd/system/fbk-time.service
```

---

## SELinux konfigurieren

SELinux ist auf RHEL standardmäßig aktiv und erfordert explizite Konfiguration.

**Hinweis:** Falls `semanage` nicht verfügbar:
```bash
sudo dnf install policycoreutils-python-utils -y
```

### SELinux-Kontexte setzen

```bash
# Schreibzugriff auf gesamtes Anwendungsverzeichnis (DB, Logs, Cache)
sudo semanage fcontext -a -t httpd_sys_rw_content_t "/var/www/fbk-time(/.*)?"

# Ausführungsrecht für Virtual Environment Binaries (Gunicorn, Python)
sudo semanage fcontext -a -t httpd_sys_script_exec_t "/var/www/fbk-time/venv/bin(/.*)?"

# Kontexte anwenden
sudo restorecon -Rv /var/www/fbk-time
```

### SELinux-Port freigeben

Gunicorn lauscht auf Port 6000, der standardmäßig nicht als HTTP-Port registriert ist:

```bash
sudo semanage port -a -t http_port_t -p tcp 6000
```

### SELinux-Booleans setzen

```bash
# Nginx-Verbindung zu Gunicorn (Reverse Proxy) erlauben
sudo setsebool -P httpd_can_network_connect on
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
sudo chown -R nginx:nginx /var/www/fbk-time
```

3. **gunicorn.conf.py fehlt oder fehlerhaft:**
```bash
# Syntax prüfen
cd /var/www/fbk-time
source venv/bin/activate
python -c "exec(open('gunicorn.conf.py').read())"
```

### SELinux blockiert

```bash
# Audit-Log prüfen
sudo ausearch -m AVC -ts recent | grep fbk

# Kontexte prüfen
ls -Z /var/www/fbk-time/
ls -Z /var/www/fbk-time/venv/bin/

# Kontexte neu setzen (siehe Abschnitt "SELinux konfigurieren")
sudo semanage fcontext -a -t httpd_sys_rw_content_t "/var/www/fbk-time(/.*)?"
sudo semanage fcontext -a -t httpd_sys_script_exec_t "/var/www/fbk-time/venv/bin(/.*)?"
sudo restorecon -Rv /var/www/fbk-time

# Port und Boolean prüfen
sudo semanage port -l | grep 6000
sudo getsebool httpd_can_network_connect
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

## Security Hardening

Die RHEL Service-Datei enthält erweiterte Sicherheitsoptionen:

| Option | Beschreibung |
|--------|-------------|
| `NoNewPrivileges=true` | Verhindert Privilege Escalation |
| `PrivateTmp=true` | Isoliertes /tmp Verzeichnis |
| `ProtectSystem=strict` | Dateisystem read-only (außer explizite Ausnahmen) |
| `ProtectHome=true` | Kein Zugriff auf /home |
| `ReadWritePaths=/var/www/fbk-time` | Anwendungsverzeichnis beschreibbar (DB, Logs, Cache) |
| `ProtectKernelTunables=true` | Kein Zugriff auf /proc/sys |
| `ProtectKernelModules=true` | Keine Kernel-Module ladbar |
| `ProtectControlGroups=true` | Kein Zugriff auf cgroups |
| `RestrictSUIDSGID=true` | Keine SUID/SGID Dateien erstellbar |
| `MemoryDenyWriteExecute=true` | Kein W+X Memory Mapping |

---

## Checkliste nach Installation

```bash
# 1. Berechtigungen setzen
sudo chown -R nginx:nginx /var/www/fbk-time

# 2. Service-Datei kopieren
sudo cp /var/www/fbk-time/config/examples/rhel/systemd-rhel.service.example \
        /etc/systemd/system/fbk-time.service

# 3. SELinux konfigurieren
sudo semanage fcontext -a -t httpd_sys_rw_content_t "/var/www/fbk-time(/.*)?"
sudo semanage fcontext -a -t httpd_sys_script_exec_t "/var/www/fbk-time/venv/bin(/.*)?"
sudo restorecon -Rv /var/www/fbk-time
sudo semanage port -a -t http_port_t -p tcp 6000
sudo setsebool -P httpd_can_network_connect on

# 4. Service aktivieren
sudo systemctl daemon-reload
sudo systemctl enable --now fbk-time

# 5. Status prüfen
sudo systemctl status fbk-time
```

---

## Hinweise

- Standard-Port für Gunicorn ist 6000 (siehe `gunicorn.conf.py`)
- Logs werden ins systemd Journal geschrieben (`journalctl -u fbk-time`)
- Bei Änderungen an der Service-Datei: `systemctl daemon-reload` nicht vergessen
- Service-User: `nginx` (wird bei Nginx-Installation erstellt)
