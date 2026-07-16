# Upgrade v1.6.0

**From:** v1.5.x **To:** v1.6.0
**Reversible:** Ja, über Backup-Restore (das Schema ist nach dem Spalten-Rename nicht abwärtskompatibel)
**Benötigt:** Python 3.11+, SQLite 3.25+
**Downtime:** Erforderlich

## Was macht das Upgrade

Ändert die `users`-Tabelle:

- `last_login` → `last_login_at` umbenannt
- `previous_login_at` (DATETIME, nullable) neu
- `credential_version` (INTEGER, Standard `0`) neu — invalidiert bei
  Passwortänderung alle bestehenden Sitzungen und Remember-Me-Cookies

Bestehende Zeilen erhalten `NULL` für `previous_login_at` — korrekte
Ausgangslage, da bisher kein vorheriger Login erfasst wurde.
`credential_version` startet für alle bestehenden Nutzer bei `0`.

Ergänzt die `settings.json` der Installation unter
`system.security.session`, falls Schlüssel fehlen:

- `idle_timeout_minutes` (Standard `30`)
- `idle_warning_seconds` (Standard `60`) — Vorwarnzeit vor dem Leerlauf-Ablauf

Der Schritt ist idempotent (vorhandene Werte bleiben unangetastet),
schreibt die Datei atomar und erstellt vorher ein eigenes Backup.

## Ablauf

```bash
# 1. Anwendung stoppen
systemctl stop fbk-time

# 2. Schema-Zustand prüfen
python upgrade.py verify --app-path /var/www/fbk-time

# 3. Upgrade ausführen
python upgrade.py upgrade --app-path /var/www/fbk-time

# 4. Anwendung starten
systemctl start fbk-time
```

Pre-Checks (vor dem Backup):

- SQLite-Version ≥ 3.25 (bei älterer Version wird das gebündelte Binary vorgeschlagen und nach Bestätigung verwendet)
- `PRAGMA integrity_check` auf der Live-DB (bricht bei Korruption ab)
- Kurzcheck: Schema **und** alle Session-Einstellungen bereits vorhanden → Exit ohne Änderungen

Backups (im DB-Verzeichnis bzw. in `--backup-dir`):

- DB: `fbk-time.backup-v1.6.0-<UTC>.db`, Permissions `0600` auf POSIX
- Settings: `settings.json.backup-v1.6.0-<UTC>` (nur wenn Schlüssel ergänzt werden)

## Verfügbare Kommandos

| Kommando | Zweck |
|---|---|
| `verify` | Prüft ohne Änderung, ob Schema und Session-Einstellungen bereits auf v1.6.0 sind |
| `upgrade` | Führt das Upgrade durch (Backup + Transaktion + Verifikation + Settings) |
| `restore` | Spielt eine Backup-Datei über die Live-DB |

## Flags

| Flag | Zweck |
|---|---|
| `--app-path DIR` | Installationsverzeichnis (z. B. `/var/www/fbk-time`) |
| `--db FILE` | Alternative: direkter Pfad zur DB-Datei |
| `--backup-dir DIR` | Zielverzeichnis für das Upgrade-Backup (optional) |
| `--backup-file FILE` | Pflichtangabe für `restore` – die einzuspielende Backup-Datei |
| `--sqlite-binary FILE` | Pfad zu einem statischen sqlite3-Binary (überspringt Auto-Erkennung) |
| `--force` | Überspringt die interaktive Bestätigung |
| `--quiet` | Unterdrückt Info-Ausgaben |

`--app-path` und `--db` schließen sich gegenseitig aus, eines von beiden
ist immer Pflicht.

## Rollback nach erfolgreichem Upgrade

Wenn im laufenden Betrieb ein Problem auffällt, spielst du das Backup
mit `restore` zurück:

```bash
systemctl stop fbk-time

python upgrade.py restore \
    --app-path /var/www/fbk-time \
    --backup-file /var/www/fbk-time/data/fbk-time.backup-v1.6.0-20260506_120000.db

# Alte Anwendungsversion (vor v1.6.0) redeployen
systemctl start fbk-time
```

`restore`-Ablauf:

- `PRAGMA integrity_check` auf `--backup-file`
- Pre-Restore-Snapshot `fbk-time.pre-restore-<UTC>.db` im DB-Verzeichnis
- `YES`-Bestätigung (mit `--force` überspringbar)
- Überschreibt die Live-DB, danach WAL-Checkpoint

Das DB-Verzeichnis muss schreibbar sein (Pre-Restore-Snapshot landet dort,
unabhängig von `--backup-dir`).

## Rollback während des Upgrades

Bei jedem Fehler innerhalb der Upgrade-Transaktion erfolgt automatisch
`ROLLBACK`. Die Live-DB bleibt unberührt, das vorher erstellte Backup
muss nicht eingespielt werden.

## Beispiele

```bash
# Non-interaktiv (Deployment-Pipeline)
python upgrade.py upgrade --app-path /var/www/fbk-time --force

# Backup auf separates Volume
python upgrade.py upgrade \
    --app-path /var/www/fbk-time \
    --backup-dir /mnt/backups/fbk-time

# Exotische Lokation (nur DB, ohne settings.json-Schritt)
python upgrade.py upgrade --db /tmp/restored.db

# Vollständige Hilfe
python upgrade.py --help
```

## Standort und Voraussetzungen

Das Skript benötigt `sqlite_runner.py` und `bin/` aus dem
übergeordneten `upgrades/`-Verzeichnis. Es müssen nur diese drei
Teile auf den Server kopiert werden — nicht der gesamte
`upgrades/`-Ordner (siehe `upgrades/README.md` für Details).
