# Upgrades

Upgrade-Skripte und gebündelte SQLite-Binaries für FBK-Time Versionswechsel.

## Verzeichnisstruktur

```
upgrades/
├── README.md              ← Diese Datei
├── sqlite_runner.py       ← Plattform-Erkennung + SQLite CLI-Wrapper
├── bin/
│   ├── linux-x86_64/
│   │   └── sqlite3        ← Statisch kompiliert, SQLite 3.49.1
│   └── linux-aarch64/
│       └── sqlite3        ← Statisch kompiliert, SQLite 3.49.1
├── v1.4.0/
│   ├── upgrade.py
│   └── README.md
└── v1.6.0/
    ├── upgrade.py
    └── README.md
```

## Deployment auf dem Server

### 1. Dateien kopieren

Für ein Upgrade werden nur drei Teile benötigt:

- `sqlite_runner.py` (gemeinsames Modul)
- `bin/` (gebündelte SQLite-Binaries)
- Der **Versionsordner** des Ziel-Releases (z. B. `v1.x.x/`)

Ältere oder andere Versionsordner werden nicht benötigt und müssen
nicht mit kopiert werden.

```bash
# Nur die benötigten Dateien kopieren
scp upgrades/sqlite_runner.py user@server:/var/www/fbk-time/upgrades/
scp -r upgrades/bin/ user@server:/var/www/fbk-time/upgrades/bin/
scp -r upgrades/v1.x.x/ user@server:/var/www/fbk-time/upgrades/v1.x.x/
```

Die resultierende Struktur auf dem Server:

```
/var/www/fbk-time/
├── app.py
├── settings.json
├── data/
│   └── fbk-time.db
└── upgrades/
    ├── sqlite_runner.py
    ├── bin/
    │   └── linux-x86_64/
    │       └── sqlite3
    └── v1.4.0/
        ├── upgrade.py
        └── README.md
```

### 2. SQLite-Binary ausführbar machen

Die gebündelten Binaries sind statisch kompilierte Linux-ELF-Dateien.
Nach dem Kopieren muss das Execute-Bit gesetzt werden:

```bash
chmod +x /var/www/fbk-time/upgrades/bin/linux-x86_64/sqlite3
```

Oder für ARM-Server:

```bash
chmod +x /var/www/fbk-time/upgrades/bin/linux-aarch64/sqlite3
```

**Prüfung:**

```bash
/var/www/fbk-time/upgrades/bin/linux-x86_64/sqlite3 --version
# Erwartete Ausgabe: 3.49.1 ...
```

### 3. Upgrade ausführen

```bash
# Anwendung stoppen
systemctl stop fbk-time

# Upgrade starten
cd /var/www/fbk-time/upgrades/v1.x.x
python3 upgrade.py upgrade --app-path /var/www/fbk-time

# Anwendung starten
systemctl start fbk-time
```

Das Upgrade-Skript erkennt automatisch:
- **System-SQLite >= 3.35:** Verwendet Pythons eingebautes `sqlite3`-Modul
- **System-SQLite < 3.35 (z. B. RHEL 9):** Erkennt Plattform und Architektur,
  schlägt das passende gebündelte Binary vor und fragt nach Bestätigung

### 4. Aufräumen

Nach erfolgreichem Upgrade kann der `upgrades/`-Ordner gelöscht werden:

```bash
rm -rf /var/www/fbk-time/upgrades/
```

## SQLite-Binary Override

Falls das gebündelte Binary nicht passt (andere Architektur, eigener Build),
kann der Pfad manuell angegeben werden:

```bash
python3 upgrade.py upgrade \
    --app-path /var/www/fbk-time \
    --sqlite-binary /opt/sqlite3/bin/sqlite3
```

Mit `--force` wird die interaktive Bestätigung übersprungen (z. B. für
Deployment-Pipelines):

```bash
python3 upgrade.py upgrade \
    --app-path /var/www/fbk-time \
    --force
```

## Gebündelte Binaries

| Plattform | Architektur | Pfad | SQLite-Version |
|-----------|-------------|------|----------------|
| Linux | x86_64 (AMD/Intel) | `bin/linux-x86_64/sqlite3` | 3.49.1 |
| Linux | aarch64 (ARM) | `bin/linux-aarch64/sqlite3` | 3.49.1 |

Die Binaries sind **statisch gegen musl/glibc gelinkt** und haben keine
externen Abhängigkeiten. Sie laufen auf jeder Linux-Distribution
unabhängig von der installierten glibc-Version (Ubuntu, RHEL, Debian,
Alpine, etc.).

## Hintergrund

RHEL 9 liefert SQLite 3.34.1 aus. Schema-Operationen wie
`ALTER TABLE ... DROP COLUMN` erfordern jedoch SQLite 3.35+. Anstatt
eine externe Python-Abhängigkeit wie `pysqlite3-binary` einzuführen,
löst `sqlite_runner.py` das Problem mit gebündelten statischen Binaries
und einem CLI-basierten Verbindungs-Wrapper.
