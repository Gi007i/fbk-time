# Upgrade v2.0.0

**From:** v1.6.x **To:** v2.0.0
**Upgrade-Skript:** Erforderlich — Schemaänderungen und Datenbereinigung (siehe unten)
**Benötigt:** Python 3.11.4 oder höher, Zugriff auf die Nginx-Konfiguration (root)
**Downtime:** Für das Deployment der Anwendungsdateien und den Datenbank-Schritt

## Ablauf

```bash
# 1. Anwendung stoppen
systemctl stop fbk-time

# 2. Neue Anwendungsdateien deployen, dann Abhängigkeiten aktualisieren
pip install -r requirements.txt
pip uninstall flask-login flask-sqlalchemy pip-licenses prettytable

# 3. settings.json anpassen: Sitzungswerte, Sicherungsverzeichnis, Datenbank- und Log-Pfade (siehe unten)

# 4. Sicherungsverzeichnis anlegen und Dienst-Einheit prüfen (siehe unten)

# 5. Datenbank-Schema aktualisieren (prüft zuerst settings.json, legt vorher
#    automatisch ein Backup an)
python upgrades/v2.0.0/upgrade.py upgrade --app-path /var/www/fbk-time

# 6. Nginx-CSP und Rate-Limit anpassen (siehe unten), prüfen und neu laden
nginx -t
systemctl reload nginx

# 7. Anwendung starten
systemctl start fbk-time
```

`prettytable` kam nur als Abhängigkeit von `pip-licenses` in die Umgebung.
Nutzt ein anderes Paket in derselben virtuellen Umgebung es ebenfalls, zeigt
`pip show prettytable` das unter `Required-by`; dann `prettytable` aus dem
Befehl streichen.

Das Upgrade-Skript läuft als root. `cli/backup.py` und `cli/manage_user.py`
dagegen starten ab v2.0.0 nur noch unter dem Dienst-Benutzer, dem das
Sicherungsverzeichnis gehört; als root brechen sie mit
`BACKUP_DIR gehört einem anderen Benutzer` ab:

```bash
# Debian/Ubuntu (auf RHEL: nginx statt www-data)
sudo -u www-data /var/www/fbk-time/venv/bin/python cli/backup.py list
```

## Datenbank: fünf Änderungen

**1. `users.start_page`** (`VARCHAR(20)`, `NOT NULL`, Standard `dashboard`).
Damit wählt jedes Konto die Seite, die direkt nach der Anmeldung erscheint.
Bestehende Konten erhalten `dashboard` und verhalten sich unverändert.

**2. `users.view_scope`** (`VARCHAR(20)`, `NOT NULL`, Standard `all`).
Legt fest, ob Kalender, Team-Übersicht und Liste mit allen Einträgen oder nur
den eigenen starten. Bestehende Konten erhalten `all`, also das bisherige
Verhalten.

**3. `login_attempts.identifier_type`** (`VARCHAR(10)`, `NOT NULL`, Standard
`USERNAME`) — sicherheitsrelevant. Zähler für Konten und für IP-Adressen
teilten sich bisher eine Spalte, Adressen nur durch das Präfix `ip:`
gekennzeichnet. Da Benutzernamen keiner Zeichenbeschränkung unterliegen,
ließ sich dieses Präfix nachbilden und damit eine fremde Adresse aussperren.
Beide Arten liegen jetzt in getrennten Namensräumen; die Eindeutigkeit gilt
über Kennung **und** Art.

Beim Upgrade werden vorhandene Zeilen mit `ip:`-Präfix **gelöscht** statt
umgeschrieben. Sie sind flüchtiger Drosselungszustand mit einer Aufbewahrung
von Stunden; ein nachgebildeter Eintrag würde sonst als echter Adresszähler
weiterleben. Praktische Folge: Laufende Sperren aus Fehlanmeldungen sind nach
dem Upgrade zurückgesetzt.

**4. Verwaiste Kategorie-Übersteuerungen** in `recurrence_exceptions`. Beim
Löschen einer Kategorie blieb bei geänderten Serienterminen die Markierung
`modified_category_overridden` stehen, obwohl die Zielkategorie fehlte. Das
Upgrade setzt diese Markierung zurück; der Termin zeigt wieder die Kategorie
seiner Serie. Ab v2.0.0 bereinigt die Anwendung das beim Löschen selbst.

**5. `users.credential_version`** erhält für jedes Konto einen neuen
Zufallswert. Bis v1.6.x wurde der Wert hochgezählt; ein gelöschtes Konto,
dessen Nummer die Datenbank später neu vergab, hätte so mit einem alten
Sitzungs- oder „Angemeldet bleiben"-Cookie das neue Konto übernehmen können.
Der Wechsel beendet alle bestehenden Sitzungen.

Alle Schritte laufen in einer einzigen Transaktion, erstellen vorher ein
zeitgestempeltes Backup der Datenbank und rollen bei jedem Fehler zurück.
Ist das Schema bereits auf dem Stand von v2.0.0, passiert nichts.

Das Skript prüft zuerst die startrelevanten Werte in `settings.json`
(Sitzungswerte, Lage des Sicherungsverzeichnisses, siehe
unten). Ist einer ungültig, nennt es ihn und bricht ab, ohne etwas zu
ändern oder ein Backup zu schreiben. Mit `--db` statt `--app-path` entfällt
diese Prüfung. Danach prüft es, ob der Dienst noch läuft, und bricht dann
ab, ohne die Datenbank zu lesen oder zu schreiben. Anschließend prüft es, ob die Datenbank
auf dem Stand von v1.6.0 ist (Spalten `last_login_at`, `previous_login_at`
und `credential_version` in `users`); fehlt eine davon, bricht es ohne
Backup ab. In diesem Fall zuerst `upgrades/v1.6.0/upgrade.py` ausführen.

```bash
# Schema-Stand prüfen (nur lesend)
python upgrades/v2.0.0/upgrade.py verify --app-path /var/www/fbk-time

# Aus einem Backup wiederherstellen
python upgrades/v2.0.0/upgrade.py restore --app-path /var/www/fbk-time \
    --backup-file /var/www/fbk-time/data/fbk-time.backup-v2.0.0-<zeitstempel>.db
```

Die zugehörigen Vorbelegungen `user_default_start_page` und
`user_default_view_scope` werden beim Start der Anwendung automatisch aus der
Einstellungs-Vorlage ergänzt; hierfür ist kein manueller Schritt nötig.

## settings.json: Sitzungswerte prüfen (startrelevant)

Die Anwendung prüft unter `system.security.session` beim Start und bricht
bei einem Verstoß mit einer Meldung ab:

- `idle_timeout_minutes` größer als 0; der Wert 0 schaltete bis v1.6.x die
  Leerlauf-Abmeldung ab, das ist nicht mehr möglich
- `lifetime_hours` länger als `idle_timeout_minutes`; sonst läuft die
  Sitzung trotz Aktivität ab, weil die absolute Dauer sich nicht verlängert
- `idle_warning_seconds` mindestens `20` und kürzer als das Leerlauf-Fenster
- `remember_cookie_days` größer als 0

Abweichende Werte vor dem Update anpassen.

Empfohlen ist ein Leerlauf-Fenster deutlich unterhalb der absoluten
Dauer und eine Vorwarnzeit von etwa einer Minute:

```json
"security": {
  "session": {
    "lifetime_hours": 10,
    "idle_timeout_minutes": 60,
    "idle_warning_seconds": 60,
    "remember_cookie_days": 7
  }
}
```

## settings.json: Sicherungsverzeichnis prüfen (startrelevant)

`system.backup.directory` muss außerhalb des Anwendungsverzeichnisses
liegen. Die Anwendung prüft das beim Start zusammen mit Besitzer und
Rechten des Verzeichnisses und bricht andernfalls mit einer Meldung ab.

Installationen, die mit v1.6.0 den Vorgabewert `/tmp/fbk-time-backups`
erhalten haben, verlegen das Verzeichnis vor dem Update auf einen
dauerhaften Pfad, etwa den neuen Vorgabewert `/var/backups/fbk-time`. Unter
`/tmp` überstehen Sicherungen weder einen Neustart noch die Bereinigung durch
das System.

`/var/backups` gehört root, deshalb kann die Anwendung das Verzeichnis dort
nicht selbst anlegen. Es wird vorab mit dem Dienst-Benutzer als Besitzer
erstellt:

```bash
# Debian/Ubuntu
install -d -m 700 -o www-data -g www-data /var/backups/fbk-time

# RHEL (zusätzlich SELinux-Kontext)
install -d -m 700 -o nginx -g nginx /var/backups/fbk-time
semanage fcontext -a -t httpd_sys_rw_content_t "/var/backups/fbk-time(/.*)?"
restorecon -Rv /var/backups/fbk-time
```

Archive, die im alten Verzeichnis noch liegen, dorthin verschieben; der
Abgleich beim Start übernimmt den neuen Speicherort.

Ist die Dienst-Einheit mit `ProtectSystem=strict` gehärtet, muss der Pfad
zusätzlich in `ReadWritePaths` stehen, sonst startet die Anwendung nicht.
Die Beispiel-Einheiten in `config/examples/*/systemd-*.service.example`
enthalten `/var/backups/fbk-time` bereits. Für eingehängte Laufwerke kommt
`RequiresMountsFor` hinzu; die auskommentierten Zeilen dort dienen als
Vorlage.

## settings.json: Datenbank- und Log-Pfade prüfen (startrelevant)

Die Anwendung verweigert ab v2.0.0 den Start, wenn die Datenbankdatei
(`system.database.path`) oder das Verzeichnis der Datenbank bzw. der Logs
(`system.logs.access_log`, `system.logs.error_log`) ein symbolischer Link
ist. Die Meldung lautet dann `Datenbankdatei ist ein Symlink (…)` bzw.
`Verzeichnis ist ein Symlink (…)`. Hintergrund: Die Anwendung setzt beim Start
die Rechte dieser Pfade; über einen Link träfe das beim Aufruf der
Kommandozeilen-Werkzeuge als root das Linkziel.

Installationen, die Daten oder Logs per Symlink umgeleitet haben, tragen in
`settings.json` stattdessen die echten Pfade ein (absolute Pfade sind
zulässig) und entfernen den Link. Liegen diese Pfade außerhalb von
`/var/www/fbk-time`, gehören sie bei `ProtectSystem=strict` zusätzlich in
`ReadWritePaths` der Dienst-Einheit, auf RHEL mit passendem SELinux-Kontext.

## Nginx: Content-Security-Policy erweitern

Zwei Änderungen an **jeder** `add_header Content-Security-Policy`-Zeile der
Site-Konfiguration (5 Vorkommen: HTTPS-Server-Block, CSS/JS-Location,
Bilder-Location, Login-Location, Standard-Location):

1. `base-uri 'self'` → `base-uri 'none'`
2. `require-trusted-types-for 'script'; trusted-types` ergänzen (erzwingt
   Trusted Types; ältere Browser ignorieren die Direktiven)

Vollständige Zeile nach der Änderung:

```nginx
add_header Content-Security-Policy "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'; require-trusted-types-for 'script'; trusted-types" always;
```

Referenz sind die aktualisierten Beispiel-Konfigurationen:

- `config/examples/debian/nginx-fbk-time-debian.conf.example`
- `config/examples/rhel/nginx-fbk-time-rhel.conf.example`

## Nginx: Rate-Limit für die Passwortbestätigung

Neu ist die Seite `/auth/reauthenticate`, auf der die Anwendung vor
folgenreichen Aktionen das Passwort erneut abfragt. Sie gehört in dieselbe
streng begrenzte Location wie Anmeldung und Passwortwechsel:

```nginx
location ~ ^/auth/(login|register|change-password|reauthenticate)$ {
```

## Weitere Hinweise

- Alle bestehenden Sitzungen und „Angemeldet bleiben"-Cookies werden
  mit dem Update ungültig; Benutzer melden sich einmalig neu an.
- Die Sperre nach wiederholten Fehlanmeldungen gilt weiter für das Konto.
  Neu ausgenommen sind Browser, in denen die Anmeldung mit diesem
  Benutzernamen schon einmal gelungen ist; so kann niemand die Person von
  außen aussperren. Schwellenwert und Sperrdauer in den Systemeinstellungen
  bleiben unverändert.
- Bei mehrtägigen Einzelterminen mit halbem Tag war die Dauerangabe
  fehlerhaft; sie wird ab v2.0.0 korrekt berechnet und fällt dadurch bei
  bestehenden Einträgen anders aus. Serien und eintägige Einträge waren
  nie betroffen, die Daten selbst bleiben unverändert.
- `cli/backup.py restore` lehnt Archive aus v1.6.x ab. Nach dem Upgrade
  deshalb gleich eine neue Sicherung anlegen; ältere Archive lassen sich nur
  in ihrer eigenen Version oder mit `--allow-version-mismatch` einspielen.
- Rollback: alte Anwendungsversion redeployen, deren `requirements.txt`
  neu installieren (sie braucht Flask-Login und Flask-SQLAlchemy) und die
  beiden CSP-Änderungen zurücknehmen. Die zusätzlichen Spalten `start_page` und
  `view_scope` stören ältere Versionen nicht und können bleiben.
  **Nicht harmlos ist der Rollback für `login_attempts`:** Eine ältere
  Version sucht Adresszähler weiterhin am Präfix `ip:`, das es nach dem
  Upgrade nicht mehr gibt — die IP-seitige Sperre bliebe wirkungslos, bis
  die Zähler neu aufgebaut sind. Für einen sauberen Vorzustand deshalb das
  vor dem Schema-Schritt erstellte Backup per `upgrade.py restore`
  einspielen. Der Dienst muss dafür gestoppt sein; das Skript prüft es.
