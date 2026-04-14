# Upgrade v1.4.0

**From:** v1.3.x **To:** v1.4.0
**Reversible:** Nein (alte Spalten werden entfernt – nur Restore aus Backup möglich)
**Benötigt:** Python 3.11+, SQLite 3.35+ (gebündelt falls System-Version zu alt)
**Downtime:** Erforderlich

## Was macht das Upgrade

Restrukturiert `recurrence_exceptions`:

- `modified_time_type` (Enum: `all_day` | `morning` | `afternoon`) neu
- `modified_substitute_overridden` (Boolean) neu
- `modified_notes_overridden` (Boolean) neu
- `modified_is_half_day_morning` entfernt
- `modified_is_half_day_afternoon` entfernt

Alle bestehenden Daten werden verlustfrei in das neue Layout übertragen.

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

- SQLite-Version ≥ 3.35 (automatischer Fallback auf gebündeltes Binary)
- `PRAGMA integrity_check` auf der Live-DB (bricht bei Korruption ab)
- Schema-Kurzcheck: bereits v1.4.0 → Exit ohne Änderungen

Backup-Datei: `fbk-time.db.backup-v1.4.0-<UTC>.db` im DB-Verzeichnis
(bzw. in `--backup-dir`), Permissions `0600` auf POSIX.

## Verfügbare Kommandos

| Kommando | Zweck |
|---|---|
| `verify` | Prüft ohne Änderung, ob das Schema bereits auf v1.4.0 ist |
| `upgrade` | Führt das Upgrade durch (Backup + Transaktion + Verifikation) |
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
    --backup-file /var/www/fbk-time/data/fbk-time.db.backup-v1.4.0-20260411_120000.db

# Alte Anwendungsversion (vor v1.4.0) redeployen
systemctl start fbk-time
```

`restore`-Ablauf:

- `PRAGMA integrity_check` auf `--backup-file`
- Pre-Restore-Snapshot `fbk-time.db.pre-restore-<UTC>.db` im DB-Verzeichnis
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

# Exotische Lokation (kein settings.json)
python upgrade.py upgrade --db /tmp/restored.db

# Vollständige Hilfe
python upgrade.py --help
```

## Standort und Voraussetzungen

Das Skript benötigt `sqlite_runner.py` und `bin/` aus dem
übergeordneten `upgrades/`-Verzeichnis. Es müssen nur diese drei
Teile auf den Server kopiert werden — nicht der gesamte
`upgrades/`-Ordner (siehe `upgrades/README.md` für Details).

**Wichtig:** Die gebündelten SQLite-Binaries in `upgrades/bin/`
müssen auf dem Server ausführbar sein:

```bash
chmod +x upgrades/bin/linux-x86_64/sqlite3
```
