# Konfigurations-Beispiele

Dieses Verzeichnis enthält Konfigurations-Beispiele für das Deployment von FBK-Time auf verschiedenen Plattformen.

## Verzeichnisstruktur

```
config/
└── examples/
    ├── debian/          # Debian/Ubuntu Konfigurationsdateien
    └── rhel/            # RHEL/CentOS/Rocky/Alma Konfigurationsdateien
```

## Plattform-spezifische Konfigurationen

### Debian/Ubuntu

Konfigurations-Beispiele für Debian-basierte Systeme (Debian, Ubuntu, Linux Mint):

- `examples/debian/nginx-debian.conf.example` - Nginx Hauptkonfiguration
- `examples/debian/nginx-fbk-time-debian.conf.example` - FBK-Time Site-Konfiguration
- `examples/debian/systemd-debian.service.example` - Systemd Service-Datei

**Wichtige Unterschiede:**
- Paketmanager: `apt`
- Nginx-Konfiguration: `/etc/nginx/sites-available/` + Symlink nach `/etc/nginx/sites-enabled/`
- SSL-Pfad: `/etc/ssl/certs/`, `/etc/ssl/private/`
- Nginx-User: `www-data`
- Service-User: `www-data` (existierender System-User)
- Firewall: UFW (`ufw`)

### RHEL/CentOS/Rocky/Alma

Konfigurations-Beispiele für Red Hat Enterprise Linux und Derivate:

- `examples/rhel/nginx-rhel.conf.example` - Nginx Hauptkonfiguration
- `examples/rhel/nginx-fbk-time-rhel.conf.example` - FBK-Time Site-Konfiguration
- `examples/rhel/systemd-rhel.service.example` - Systemd Service-Datei

**Wichtige Unterschiede:**
- Paketmanager: `dnf`
- Nginx-Konfiguration: `/etc/nginx/conf.d/` (direkte Platzierung)
- SSL-Pfad: `/etc/pki/tls/certs/`, `/etc/pki/tls/private/`
- Nginx-User: `nginx`
- Service-User: `fbktime` (muss manuell erstellt werden)
- Firewall: firewalld (`firewall-cmd`)
- **SELinux:** Aktiv - erfordert korrekte Kontexte und Booleans!

## Verwendung

1. **Entsprechende Beispiel-Datei kopieren:**
   ```bash
   # Debian/Ubuntu
   sudo cp config/examples/debian/nginx-debian.conf.example /etc/nginx/nginx.conf

   # RHEL
   sudo cp config/examples/rhel/nginx-rhel.conf.example /etc/nginx/nginx.conf
   ```

2. **Konfiguration anpassen:**
   - Ersetze `absence.example.com` mit deiner Domain/IP
   - Passe SSL-Zertifikat-Pfade an
   - Ändere Port-Nummern falls erforderlich
   - Setze korrekte Dateipfade für deine Installation

3. **Deployment-Dokumentation konsultieren:**
   - Siehe [docs/deployment/](../docs/deployment/) für detaillierte Setup-Anleitungen
   - Plattform-spezifische Anleitungen für jede Distributions-Familie verfügbar

## Wichtige Hinweise

- **Niemals Produktions-Konfigurationen committen** - Dies sind nur Templates
- **Sicherheitseinstellungen prüfen** vor dem Deployment in Produktion
- **Konfigurationen testen** vor Neustart der Services:
  ```bash
  # Nginx
  sudo nginx -t

  # Systemd
  sudo systemctl daemon-reload
  ```

## Verwandte Dokumentation

- [Deployment-Anleitung](../docs/deployment/README.md) - Vollständige Deployment-Anweisungen
