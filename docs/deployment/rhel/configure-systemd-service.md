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

## Backup-Verzeichnis anlegen

Die Anwendung legt Backups standardmäßig unter `/var/backups/fbk-time` ab
(`system.backup.directory` in `settings.json`). Das Verzeichnis muss vor dem
ersten Start existieren und dem Service-User gehören: `/var/backups` gehört
root, und die Service-Datei gibt den Pfad per `ReadWritePaths` frei — fehlt es,
startet der Dienst nicht. Den SELinux-Kontext setzt der folgende Abschnitt.

```bash
sudo install -d -m 700 -o nginx -g nginx /var/backups/fbk-time
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

# Schreibzugriff auf das Backup-Verzeichnis
sudo semanage fcontext -a -t httpd_sys_rw_content_t "/var/backups/fbk-time(/.*)?"

# Kontexte anwenden
sudo restorecon -Rv /var/www/fbk-time
sudo restorecon -Rv /var/backups/fbk-time
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

4. **Symlink statt echtem Pfad** (Meldung `Verzeichnis ist ein Symlink (…)`,
   `Datenbankdatei ist ein Symlink (…)` oder `BACKUP_DIR ist ein Symlink (…)`):
   Datenbankdatei, Datenbank-, Log- und Backup-Verzeichnis dürfen keine
   symbolischen Links sein. Die echten Pfade in `settings.json` eintragen, den
   Link entfernen und Pfade außerhalb von `/var/www/fbk-time` in
   `ReadWritePaths` aufnehmen, jeweils mit SELinux-Kontext.
```bash
# Links im Anwendungsverzeichnis finden
sudo find /var/www/fbk-time -maxdepth 2 -type l
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

Die RHEL Service-Datei entzieht dem Dienst alle nicht benötigten Systemrechte:

| Option | Beschreibung |
|--------|-------------|
| `NoNewPrivileges=true` | Verhindert Privilege Escalation |
| `PrivateTmp=true` | Isoliertes /tmp Verzeichnis |
| `ProtectSystem=strict` | Dateisystem read-only (außer explizite Ausnahmen) |
| `ProtectHome=true` | Kein Zugriff auf /home |
| `ReadWritePaths=/var/www/fbk-time /var/backups/fbk-time` | Anwendungsverzeichnis (DB, Logs, Cache) und Backup-Verzeichnis beschreibbar |
| `ProtectKernelTunables=true` | Kein Zugriff auf /proc/sys |
| `ProtectKernelModules=true` | Keine Kernel-Module ladbar |
| `ProtectControlGroups=true` | Kein Zugriff auf cgroups |
| `RestrictSUIDSGID=true` | Keine SUID/SGID Dateien erstellbar |
| `RestrictNamespaces=true` | Keine neuen Namespaces erstellbar |
| `RestrictRealtime=true` | Kein Realtime-Scheduling |
| `LockPersonality=true` | Ausführungsdomäne festgelegt |
| `MemoryDenyWriteExecute=true` | Kein W+X Memory Mapping |

---

## Backup auf externen Speicher

`ProtectSystem=strict` macht das gesamte Dateisystem read-only, außer den unter
`ReadWritePaths` aufgeführten Pfaden. Ein anderes Backup-Ziel als
`/var/backups/fbk-time` (z. B. ein gemountetes Volume) muss deshalb explizit
freigegeben werden — sonst bricht die Anwendung bereits beim Start ab, weil sie
das Backup-Verzeichnis beim Start anlegt und Besitzer und Rechte (`0700`)
prüft. Wegen `PrivateTmp=true` darf das Backup-Verzeichnis zudem **nicht unter
`/tmp`** liegen: Der Dienst erhält ein eigenes, flüchtiges `/tmp`, sodass dort
abgelegte Backups beim Neustart verloren gingen und für `cli/backup.py`
unsichtbar wären.

Für ein Backup-Verzeichnis unter `/mnt/backup`:

```bash
# 1. Backup-Pfad in settings.json setzen: system.backup.directory = /mnt/backup

# 2. Die in der Service-Datei auskommentierten Zeilen per Drop-in aktivieren
#    (Drop-in bleibt bei Updates der Haupt-Unit erhalten)
sudo systemctl edit fbk-time
# im Editor eintragen:
#   [Unit]
#   RequiresMountsFor=/mnt/backup
#   [Service]
#   ReadWritePaths=
#   ReadWritePaths=/var/www/fbk-time /mnt/backup
#   (die leere Zeile setzt die Liste der Haupt-Unit zurück, sonst müsste
#   /var/backups/fbk-time weiterhin existieren)

# 3. SELinux-Kontext für das Backup-Ziel setzen
sudo semanage fcontext -a -t httpd_sys_rw_content_t "/mnt/backup(/.*)?"
sudo restorecon -Rv /mnt/backup

# 4. Eigentümer und Rechte
sudo chown nginx:nginx /mnt/backup
sudo chmod 700 /mnt/backup

# 5. Neu laden und starten
sudo systemctl daemon-reload
sudo systemctl restart fbk-time
```

---

## Schlüssel wechseln (SECRET_KEY)

Der `SECRET_KEY` in `/var/www/fbk-time/.env` signiert Sitzungen sowie die
Cookies für „Angemeldet bleiben“ und bekannte Browser. Wechseln Sie ihn bei
Verdacht auf Kompromittierung sofort, sonst in geplanten Abständen. Damit
beim geplanten Wechsel niemand abgemeldet wird, bleibt der alte Schlüssel
übergangsweise in `SECRET_KEY_FALLBACKS` gültig:

```bash
# 1. Neuen Schlüssel erzeugen
python3 -c "import secrets; print(secrets.token_hex(32))"

# 2. In .env den bisherigen Wert nach SECRET_KEY_FALLBACKS verschieben
#    und den neuen als SECRET_KEY eintragen:
#      SECRET_KEY=<neuer Schlüssel>
#      SECRET_KEY_FALLBACKS=<alter Schlüssel>
sudoedit /var/www/fbk-time/.env

# 3. Dienst neu starten
sudo systemctl restart fbk-time
```

- Mehrere alte Schlüssel stehen kommagetrennt in einer Zeile. Jeder muss
  mindestens 32 Zeichen lang sein, sonst startet die Anwendung nicht.
- Ist `SECRET_KEY` als Umgebungsvariable gesetzt, liest die Anwendung auch
  `SECRET_KEY_FALLBACKS` nur aus der Umgebung.
- Entfernen Sie den alten Schlüssel nach Ablauf von `remember_cookie_days`.
  Browser, deren Gerätecookie noch mit ihm signiert ist, gelten danach bis zur
  nächsten erfolgreichen Anmeldung wieder als unbekannt.
- Formulare, die vor dem Neustart geöffnet wurden, müssen einmal neu geladen
  werden.
- Bei Verdacht auf Kompromittierung tragen Sie den alten Schlüssel nicht als
  Fallback ein; damit enden alle Sitzungen und „Angemeldet bleiben“ sofort.

---

## Checkliste nach Installation

```bash
# 1. Berechtigungen setzen
sudo chown -R nginx:nginx /var/www/fbk-time

# 2. Backup-Verzeichnis anlegen
sudo install -d -m 700 -o nginx -g nginx /var/backups/fbk-time

# 3. Service-Datei kopieren
sudo cp /var/www/fbk-time/config/examples/rhel/systemd-rhel.service.example \
        /etc/systemd/system/fbk-time.service

# 4. SELinux konfigurieren
sudo semanage fcontext -a -t httpd_sys_rw_content_t "/var/www/fbk-time(/.*)?"
sudo semanage fcontext -a -t httpd_sys_script_exec_t "/var/www/fbk-time/venv/bin(/.*)?"
sudo semanage fcontext -a -t httpd_sys_rw_content_t "/var/backups/fbk-time(/.*)?"
sudo restorecon -Rv /var/www/fbk-time
sudo restorecon -Rv /var/backups/fbk-time
sudo semanage port -a -t http_port_t -p tcp 6000
sudo setsebool -P httpd_can_network_connect on

# 5. Service aktivieren
sudo systemctl daemon-reload
sudo systemctl enable --now fbk-time

# 6. Status prüfen
sudo systemctl status fbk-time
```

---

## Hinweise

- Standard-Port für Gunicorn ist 6000 (siehe `gunicorn.conf.py`)
- Logs werden ins systemd Journal geschrieben (`journalctl -u fbk-time`)
- Bei Änderungen an der Service-Datei: `systemctl daemon-reload` nicht vergessen
- Service-User: `nginx` (wird bei Nginx-Installation erstellt)
