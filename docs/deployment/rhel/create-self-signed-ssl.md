# Self-Signed SSL-Zertifikat erstellen (RHEL)

## Übersicht
Ein Self-Signed SSL-Zertifikat eignet sich für:
- Interne Anwendungen
- Entwicklungs-/Test-Umgebungen
- Private Netzwerke ohne Zugriff von außen

**Hinweis:** Self-Signed Zertifikate werden von Browsern als "nicht vertrauenswürdig" angezeigt. Für produktive, öffentlich zugängliche Anwendungen sollte ein reguläres Zertifikat von einer Zertifizierungsstelle verwendet werden (siehe [install-ca-signed-ssl.md](install-ca-signed-ssl.md)).

---

## Voraussetzungen

```bash
# OpenSSL sollte bereits installiert sein, prüfen:
openssl version

# Falls nicht installiert:
sudo dnf install openssl -y

# SELinux Tools installieren (falls nicht vorhanden)
sudo dnf install policycoreutils-python-utils -y
```

---

## Zertifikat erstellen

**Zertifikat-Pfade (RHEL Standard):**
- **Zertifikat:** `/etc/pki/tls/certs/fbk-time.crt`
- **Private Key:** `/etc/pki/tls/private/fbk-time.key`

### Schritt 1: Verzeichnisse prüfen

```bash
# Verzeichnisse existieren bereits auf RHEL
ls -la /etc/pki/tls/certs/
ls -la /etc/pki/tls/private/
```

---

### Schritt 2: Zertifikat erstellen

#### Methode 1: Single Command (Einfach)

```bash
# Self-Signed Zertifikat erstellen (gültig für 365 Tage)
sudo openssl req -x509 -nodes -days 365 -newkey rsa:4096 \
  -keyout /etc/pki/tls/private/fbk-time.key \
  -out /etc/pki/tls/certs/fbk-time.crt \
  -subj "/C=DE/ST=NRW/L=Koeln/O=MeineFirma/OU=IT/CN=absence.example.com"
```

**Parameter anpassen:**
- **C=DE** → Dein Land (z.B. AT, CH)
- **ST=NRW** → Dein Bundesland/Kanton
- **L=Koeln** → Deine Stadt
- **O=MeineFirma** → Dein Firmenname
- **CN=absence.example.com** → Deine Domain oder IP

---

#### Methode 2: Mit SubjectAltName (SAN) - Empfohlen

Für moderne Browser wird empfohlen, SAN (Subject Alternative Name) zu verwenden:

```bash
# Konfigurationsdatei erstellen
cat > /tmp/san.cnf << EOF
[req]
default_bits = 4096
prompt = no
default_md = sha256
distinguished_name = dn
req_extensions = v3_req

[dn]
C=DE
ST=NRW
L=Koeln
O=MeineFirma
OU=IT
CN=absence.example.com

[v3_req]
subjectAltName = @alt_names

[alt_names]
DNS.1 = absence.example.com
DNS.2 = www.absence.example.com
IP.1 = 192.168.1.100
EOF

# Zertifikat mit SAN erstellen
sudo openssl req -x509 -nodes -days 365 -newkey rsa:4096 \
  -keyout /etc/pki/tls/private/fbk-time.key \
  -out /etc/pki/tls/certs/fbk-time.crt \
  -config /tmp/san.cnf \
  -extensions v3_req

# Temporäre Konfiguration löschen
rm /tmp/san.cnf
```

---

## Berechtigungen und SELinux

```bash
# Private Key schützen (nur root kann lesen)
sudo chmod 600 /etc/pki/tls/private/fbk-time.key

# Zertifikat kann von allen gelesen werden
sudo chmod 644 /etc/pki/tls/certs/fbk-time.crt

# Besitzer setzen
sudo chown root:root /etc/pki/tls/private/fbk-time.key
sudo chown root:root /etc/pki/tls/certs/fbk-time.crt

# SELinux-Kontext anwenden (Standard-Pfade haben korrekten Kontext)
sudo restorecon -Rv /etc/pki/tls/certs/
sudo restorecon -Rv /etc/pki/tls/private/

# Verifizieren
ls -Z /etc/pki/tls/certs/fbk-time.crt
ls -Z /etc/pki/tls/private/fbk-time.key
```

---

## Zertifikat überprüfen

```bash
# Zertifikat-Details anzeigen
sudo openssl x509 -in /etc/pki/tls/certs/fbk-time.crt -text -noout

# Nur Gültigkeitsdatum anzeigen
sudo openssl x509 -in /etc/pki/tls/certs/fbk-time.crt -noout -dates

# Private Key überprüfen
sudo openssl rsa -in /etc/pki/tls/private/fbk-time.key -check

# Prüfen, ob Zertifikat und Key zusammenpassen
sudo openssl x509 -noout -modulus -in /etc/pki/tls/certs/fbk-time.crt | openssl md5
sudo openssl rsa -noout -modulus -in /etc/pki/tls/private/fbk-time.key | openssl md5
# Die MD5-Hashes müssen identisch sein!
```

---

## In Nginx einbinden

Die Zertifikat-Pfade in der Nginx-Konfiguration (siehe [setup-nginx.md](setup-nginx.md)):

```nginx
# In /etc/nginx/conf.d/fbk-time.conf
ssl_certificate /etc/pki/tls/certs/fbk-time.crt;
ssl_certificate_key /etc/pki/tls/private/fbk-time.key;
```

### Nginx Konfiguration testen und neu laden

```bash
# Konfiguration testen
sudo nginx -t

# Bei Erfolg: Nginx neu laden
sudo systemctl reload nginx

# Status prüfen
sudo systemctl status nginx

# Falls Nginx nicht startet - SELinux prüfen
sudo setsebool -P httpd_can_network_connect on
sudo ausearch -m AVC -ts recent | grep nginx
```

### Firewall öffnen (falls noch nicht geschehen)

```bash
sudo firewall-cmd --permanent --add-service=http
sudo firewall-cmd --permanent --add-service=https
sudo firewall-cmd --reload
```

---

## SELinux Troubleshooting

Falls Nginx nicht auf Zertifikate zugreifen kann:

```bash
# SELinux Audit-Log prüfen
sudo ausearch -m AVC -ts recent | grep nginx

# Kontext korrigieren
sudo restorecon -Rv /etc/pki/tls/

# Falls nötig, SELinux-Kontext wiederherstellen
sudo restorecon -Rv /etc/pki/tls/
```

---

## Browser-Warnung umgehen (Optional)

### Option 1: Zertifikat im Browser importieren

1. Zertifikat herunterladen: `fbk-time.crt`
2. Im Browser öffnen:
   - **Chrome/Edge:** Einstellungen → Datenschutz → Zertifikate verwalten → Vertrauenswürdige Stammzertifizierungsstellen → Importieren
   - **Firefox:** Einstellungen → Datenschutz → Zertifikate anzeigen → Zertifizierungsstellen → Importieren

### Option 2: Browser-Exception hinzufügen

- Im Browser: "Erweitert" → "Trotzdem fortfahren" klicken
- Nur für interne, vertrauenswürdige Anwendungen!

---

## Zertifikat erneuern (nach Ablauf)

```bash
# Altes Zertifikat löschen (Key kann beibehalten werden)
sudo rm /etc/pki/tls/certs/fbk-time.crt

# Neues Zertifikat erstellen (mit bestehendem Key)
sudo openssl req -new -x509 -days 365 \
  -key /etc/pki/tls/private/fbk-time.key \
  -out /etc/pki/tls/certs/fbk-time.crt \
  -subj "/C=DE/ST=NRW/L=Koeln/O=MeineFirma/OU=IT/CN=absence.example.com"

# SELinux Kontext anwenden
sudo restorecon -Rv /etc/pki/tls/certs/

# Nginx neu laden
sudo systemctl reload nginx
```

---

## Automatische Erneuerung (Cronjob)

```bash
# Cronjob erstellen (erneut 30 Tage vor Ablauf)
sudo crontab -e

# Folgende Zeile hinzufügen (läuft täglich um 3 Uhr)
0 3 * * * /usr/bin/openssl x509 -checkend 2592000 -noout -in /etc/pki/tls/certs/fbk-time.crt || (/usr/bin/openssl req -new -x509 -days 365 -key /etc/pki/tls/private/fbk-time.key -out /etc/pki/tls/certs/fbk-time.crt -subj "/C=DE/ST=NRW/L=Koeln/O=MeineFirma/OU=IT/CN=absence.example.com" && /usr/sbin/nginx -s reload)
```

---

## Wichtige Hinweise

**Self-Signed Zertifikate sind NICHT für öffentliche Websites geeignet!**

**Verwende Self-Signed Zertifikate für:**
- Intranet-Anwendungen
- Entwicklungsumgebungen
- Private Netzwerke ohne externen Zugriff

**NICHT verwenden für:**
- Öffentlich zugängliche Websites
- E-Commerce / Zahlungsverkehr
- Anwendungen mit externen Nutzern
