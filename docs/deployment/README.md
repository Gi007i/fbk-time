# FBK-Time Deployment-Anleitungen

Deployment-Anleitungen für FBK-Time auf verschiedenen Linux-Distributionen.

## Unterstützte Plattformen

### Debian-Familie
- Ubuntu 22.04 LTS, 24.04 LTS
- Debian 11, 12
- Linux Mint

### Red Hat-Familie
- RHEL 9
- CentOS Stream 9
- Rocky Linux 9
- AlmaLinux 9

## Dokumentationsstruktur

Deployment-Anleitungen sind nach Plattform-Familien organisiert:

- **[debian/](debian/)** - Debian/Ubuntu Deployment-Anleitungen
- **[rhel/](rhel/)** - RHEL/CentOS/Rocky/Alma Deployment-Anleitungen

## Plattform-spezifische Anleitungen

### Debian/Ubuntu

- [setup-nginx.md](debian/setup-nginx.md) - Nginx Reverse Proxy Setup
- [configure-systemd-service.md](debian/configure-systemd-service.md) - Systemd Service-Konfiguration
- [create-self-signed-ssl.md](debian/create-self-signed-ssl.md) - Self-Signed SSL-Zertifikate (Intranet)
- [install-ca-signed-ssl.md](debian/install-ca-signed-ssl.md) - CA-signierte SSL-Zertifikate (Production)

### RHEL/CentOS/Rocky/Alma

- [setup-nginx.md](rhel/setup-nginx.md) - Nginx Reverse Proxy Setup
- [configure-systemd-service.md](rhel/configure-systemd-service.md) - Systemd Service-Konfiguration
- [create-self-signed-ssl.md](rhel/create-self-signed-ssl.md) - Self-Signed SSL-Zertifikate (Intranet)
- [install-ca-signed-ssl.md](rhel/install-ca-signed-ssl.md) - CA-signierte SSL-Zertifikate (Production)

## Konfigurations-Beispiele

Alle Konfigurations-Beispiele befinden sich unter [config/examples/](../../config/examples/):

### Debian/Ubuntu
- `config/examples/debian/nginx-debian.conf.example` - Nginx Hauptkonfiguration
- `config/examples/debian/nginx-fbk-time-debian.conf.example` - FBK-Time Site-Konfiguration
- `config/examples/debian/systemd-debian.service.example` - Systemd Service-Datei

### RHEL
- `config/examples/rhel/nginx-rhel.conf.example` - Nginx Hauptkonfiguration
- `config/examples/rhel/nginx-fbk-time-rhel.conf.example` - FBK-Time Site-Konfiguration
- `config/examples/rhel/systemd-rhel.service.example` - Systemd Service-Datei

## Deployment-Workflow

### Debian/Ubuntu

1. **Nginx installieren und konfigurieren**
   ```bash
   # Siehe debian/setup-nginx.md
   ```

2. **SSL-Zertifikat erstellen**
   ```bash
   # Siehe debian/create-self-signed-ssl.md (Intranet)
   # ODER debian/install-ca-signed-ssl.md (Production)
   ```

3. **Systemd-Service einrichten**
   ```bash
   # Siehe debian/configure-systemd-service.md
   ```

### RHEL/CentOS/Rocky/Alma

1. **Nginx installieren und konfigurieren**
   ```bash
   # Siehe rhel/setup-nginx.md
   ```

2. **SSL-Zertifikat erstellen**
   ```bash
   # Siehe rhel/create-self-signed-ssl.md (Intranet)
   # ODER rhel/install-ca-signed-ssl.md (Production)
   ```

3. **Systemd-Service einrichten**
   ```bash
   # Siehe rhel/configure-systemd-service.md
   ```

## Wichtige Unterschiede zwischen Distributionen

| Komponente | Debian/Ubuntu | RHEL |
|-----------|---------------|------|
| **Paketmanager** | `apt` | `dnf` |
| **Nginx-Konfiguration** | `/etc/nginx/sites-available/` + Symlink | `/etc/nginx/conf.d/` |
| **SSL-Pfad** | `/etc/ssl/certs/`, `/etc/ssl/private/` | `/etc/pki/tls/certs/`, `/etc/pki/tls/private/` |
| **Nginx-User** | `www-data` | `nginx` |
| **Service-User** | `www-data` (existiert bereits) | `nginx` (existiert bereits) |
| **Firewall** | UFW (`ufw`) | firewalld (`firewall-cmd`) |
| **SELinux** | Nicht aktiv | **Aktiv** - Kontexte & Booleans erforderlich! |

## Wichtige Hinweise

- Alle Anleitungen sind für **Offline-Betrieb** ohne Internet-Zugriff konzipiert
- Produktionsumgebung: Gunicorn läuft auf Port 6000 (siehe `gunicorn.conf.py`)
- RHEL: SELinux-Kontexte und Booleans sind kritisch für den Betrieb
- Konfigurations-Beispiele müssen an die spezifische Umgebung angepasst werden
- Konfigurationen immer vor Produktiv-Deployment testen

## Troubleshooting

Jede plattform-spezifische Anleitung enthält einen "Troubleshooting"-Abschnitt für häufige Probleme.

## Verwandte Dokumentation

- [Konfigurations-Beispiele](../../config/README.md) - Config-Datei-Templates
