# Reguläres SSL-Zertifikat (CSR) erstellen (Debian/Ubuntu)

## Übersicht
Ein reguläres SSL-Zertifikat wird von einer vertrauenswürdigen Zertifizierungsstelle (CA - Certificate Authority) signiert und wird von allen Browsern automatisch akzeptiert.

**Vorteile:**
- Keine Browser-Warnungen
- Vertrauenswürdige Verschlüsselung
- Für öffentlich zugängliche Websites geeignet

**Zertifikatstypen:**
- **DV (Domain Validated):** Nur Domain-Besitz, schnell, günstig
- **OV (Organization Validated):** Firmendaten geprüft, mittel
- **EV (Extended Validation):** Umfassende Prüfung, teuer

**Zertifizierungsstellen (Beispiele):**
- Let's Encrypt (kostenlos, automatisiert) - **Achtung:** Benötigt Internet-Zugriff!
- DigiCert, GlobalSign, Sectigo (früher Comodo), GeoTrust

---

## Voraussetzungen

```bash
# OpenSSL sollte bereits installiert sein, prüfen:
openssl version

# Falls nicht installiert:
sudo apt update
sudo apt install openssl -y

# Verzeichnisse erstellen
sudo mkdir -p /etc/ssl/private /etc/ssl/certs /etc/ssl/csr
```

**Zertifikat-Pfade (Debian/Ubuntu Standard):**
- Zertifikate: `/etc/ssl/certs/`
- Private Keys: `/etc/ssl/private/`
- CSR: `/etc/ssl/csr/`

---

## Schritt 1: Private Key erstellen

```bash
sudo openssl genrsa -out /etc/ssl/private/fbk-time.key 4096
sudo chmod 600 /etc/ssl/private/fbk-time.key
sudo chown root:root /etc/ssl/private/fbk-time.key
```

**Wichtig:** Den Private Key NIEMALS an die CA senden! Nur den CSR (Certificate Signing Request).

---

## Schritt 2: Certificate Signing Request (CSR) erstellen

### Methode 1: Interaktiv

```bash
sudo openssl req -new -key /etc/ssl/private/fbk-time.key \
  -out /etc/ssl/csr/fbk-time.csr

# Du wirst nach folgenden Informationen gefragt:
# Country Name (2 letter code) [AU]: DE
# State or Province Name (full name) [Some-State]: Nordrhein-Westfalen
# Locality Name (eg, city) []: Koeln
# Organization Name (eg, company) [Internet Widgits Pty Ltd]: Meine Firma GmbH
# Organizational Unit Name (eg, section) []: IT Abteilung
# Common Name (e.g. server FQDN or YOUR name) []: absence.example.com
# Email Address []: admin@example.com
#
# A challenge password []: (leer lassen - wird nicht benötigt)
# An optional company name []: (leer lassen)
```

---

### Methode 2: Mit Konfigurations-Datei (Empfohlen)

Diese Methode erlaubt Subject Alternative Names (SAN), die für moderne Browser erforderlich sind:

```bash
# Konfigurationsdatei erstellen
cat > /tmp/csr.cnf << EOF
[req]
default_bits = 4096
prompt = no
default_md = sha256
req_extensions = req_ext
distinguished_name = dn

[dn]
C=DE
ST=Nordrhein-Westfalen
L=Koeln
O=Meine Firma GmbH
OU=IT Abteilung
CN=absence.example.com

[req_ext]
subjectAltName = @alt_names

[alt_names]
DNS.1 = absence.example.com
DNS.2 = www.absence.example.com
# Weitere Domains hinzufügen falls nötig:
# DNS.3 = app.absence.example.com
EOF

# CSR erstellen
sudo openssl req -new \
  -key /etc/ssl/private/fbk-time.key \
  -out /etc/ssl/csr/fbk-time.csr \
  -config /tmp/csr.cnf

# Temporäre Konfiguration löschen
rm /tmp/csr.cnf
```

**Wichtige Felder:**
- **Common Name (CN):** Muss exakt mit der Domain übereinstimmen!
- **Organization (O):** Offizieller Firmenname
- **Country (C):** 2-Buchstaben ISO-Code (z.B. DE, AT, CH)

---

## Schritt 3: CSR überprüfen

```bash
# CSR-Inhalt anzeigen
sudo openssl req -in /etc/ssl/csr/fbk-time.csr -noout -text

# Subject anzeigen
sudo openssl req -in /etc/ssl/csr/fbk-time.csr -noout -subject

# SANs anzeigen (falls vorhanden)
sudo openssl req -in /etc/ssl/csr/fbk-time.csr -noout -text | grep -A1 "Subject Alternative Name"

# CSR-Signatur verifizieren
sudo openssl req -in /etc/ssl/csr/fbk-time.csr -noout -verify
```

**Prüfe:**
- Common Name stimmt mit Domain überein
- Alle SANs sind korrekt
- Keine Tippfehler in Firmenname/Adresse

---

## Schritt 4: CSR an Zertifizierungsstelle senden

### CSR-Datei ausgeben

```bash
# CSR-Inhalt anzeigen (zum Kopieren)
sudo cat /etc/ssl/csr/fbk-time.csr
```

**Ausgabe sieht so aus:**
```
-----BEGIN CERTIFICATE REQUEST-----
MIIEojCCAooCAQAwYzELMAkGA1UEBhMCREUxHDAaBgNVBAgME05vcmRyaGVpbi1X
...
[Viele Zeilen Base64-Code]
...
-----END CERTIFICATE REQUEST-----
```

### Bei CA einreichen

1. **Website der CA aufrufen** (z.B. DigiCert, Sectigo, GlobalSign)
2. **Zertifikat bestellen** (DV, OV oder EV)
3. **CSR hochladen/einfügen**
4. **Domain-Validierung durchführen:**
   - E-Mail-Validierung (E-Mail an admin@domain.com)
   - DNS-Validierung (TXT-Record hinzufügen)
   - HTTP-Validierung (Datei auf Website ablegen)
5. **Zertifikat herunterladen**

---

## Schritt 5: Zertifikat von CA erhalten

Die CA sendet dir normalerweise:
1. **Server-Zertifikat** (`fbk-time.crt`)
2. **Intermediate Certificate(s)** (`intermediate.crt`)
3. **Root Certificate** (`root.crt`) - optional

---

## Schritt 6: Zertifikat installieren

### Zertifikate auf Server kopieren

```bash
sudo mv fbk-time.crt /etc/ssl/certs/fbk-time.crt
sudo cat intermediate.crt root.crt > /etc/ssl/certs/ca-bundle.crt
```

### Berechtigungen setzen

```bash
sudo chmod 600 /etc/ssl/private/fbk-time.key
sudo chmod 644 /etc/ssl/certs/fbk-time.crt
sudo chmod 644 /etc/ssl/certs/ca-bundle.crt
sudo chown root:root /etc/ssl/private/fbk-time.key
sudo chown root:root /etc/ssl/certs/*.crt
```

---

## Schritt 7: In Nginx einbinden

```nginx
# /etc/nginx/sites-available/fbk-time
ssl_certificate /etc/ssl/certs/fbk-time.crt;
ssl_certificate_key /etc/ssl/private/fbk-time.key;
ssl_trusted_certificate /etc/ssl/certs/ca-bundle.crt;
```

### Nginx testen und neu laden

```bash
# Konfiguration testen
sudo nginx -t

# Bei Erfolg: Nginx neu laden
sudo systemctl reload nginx
```

---

## Zertifikat überprüfen

### Lokal überprüfen

```bash
# Zertifikat-Details anzeigen
sudo openssl x509 -in /etc/ssl/certs/fbk-time.crt -text -noout

# Gültigkeitsdatum anzeigen
sudo openssl x509 -in /etc/ssl/certs/fbk-time.crt -noout -dates

# Prüfen, ob Zertifikat und Key zusammenpassen
sudo openssl x509 -noout -modulus -in /etc/ssl/certs/fbk-time.crt | openssl md5
sudo openssl rsa -noout -modulus -in /etc/ssl/private/fbk-time.key | openssl md5
# Die MD5-Hashes müssen identisch sein!
```

### Online überprüfen (nach Deployment)

```bash
# Mit OpenSSL verbinden
openssl s_client -connect absence.example.com:443 -servername absence.example.com

# Zertifikatskette anzeigen
openssl s_client -connect absence.example.com:443 -servername absence.example.com -showcerts
```

**Online-Tools:**
- https://www.ssllabs.com/ssltest/
- https://www.sslshopper.com/ssl-checker.html

---

## Zertifikat erneuern

Zertifikate haben eine begrenzte Gültigkeit (90 Tage bis 2 Jahre). Rechtzeitig erneuern!

### Manuelle Erneuerung

```bash
# CSR neu erstellen (mit bestehendem Key)
sudo openssl req -new \
  -key /etc/ssl/private/fbk-time.key \
  -out /etc/ssl/csr/fbk-time-renewal.csr \
  -config /tmp/csr.cnf

# CSR an CA senden, neues Zertifikat erhalten
# Zertifikat ersetzen
sudo mv new-fbk-time.crt /etc/ssl/certs/fbk-time.crt

# Nginx neu laden
sudo systemctl reload nginx
```

### Erinnerungen einrichten

```bash
# Cronjob erstellen (prüft täglich, warnt 30 Tage vorher)
sudo crontab -e

# Folgende Zeile hinzufügen
0 3 * * * /usr/bin/openssl x509 -checkend 2592000 -noout -in /etc/ssl/certs/fbk-time.crt || echo "SSL-Zertifikat läuft in weniger als 30 Tagen ab!" | mail -s "SSL-Warnung" admin@example.com
```

---

## Certificate Chain Order (Wichtig!)

Die richtige Reihenfolge für die Full Chain:
1. **Server-Zertifikat** (fbk-time.crt)
2. **Intermediate Certificate(s)** (in der Reihenfolge von nah zu weit)
3. **Root Certificate** (optional, meist nicht nötig)

**Beispiel:**
```bash
cat server.crt intermediate1.crt intermediate2.crt > fullchain.crt
```

---

## Alternative: Let's Encrypt (Kostenlos)

**Achtung:** Let's Encrypt benötigt Internet-Zugriff für die automatische Erneuerung!

```bash
# Certbot installieren
sudo apt install certbot python3-certbot-nginx -y

# Zertifikat erstellen und Nginx automatisch konfigurieren
sudo certbot --nginx -d absence.example.com

# Automatische Erneuerung einrichten
sudo certbot renew --dry-run
```

**Hinweis:** Da FBK-Time für Offline-Betrieb konzipiert ist, ist diese Option nur verfügbar, wenn temporärer Internet-Zugriff möglich ist.

---

## Troubleshooting

### "SSL certificate problem: unable to get local issuer certificate"
**Problem:** Intermediate Certificate fehlt
**Lösung:** Full Chain verwenden

### "NET::ERR_CERT_COMMON_NAME_INVALID"
**Problem:** Common Name stimmt nicht mit Domain überein
**Lösung:** CSR neu erstellen

### "NET::ERR_CERT_AUTHORITY_INVALID"
**Problem:** Zertifikat wurde von unbekannter CA signiert
**Lösung:** Zertifikat von vertrauenswürdiger CA beziehen

### Browser zeigt "Nicht sicher" trotz Zertifikat
**Problem:** Mixed Content (HTTP-Ressourcen auf HTTPS-Seite)
**Lösung:** Alle Ressourcen über HTTPS laden

---

## Wichtige Hinweise

**Private Key sicher aufbewahren!**
- Niemals teilen oder veröffentlichen
- Backup an sicherem Ort
- Bei Kompromittierung sofort Zertifikat widerrufen

**Zertifikat rechtzeitig erneuern:**
- Mindestens 30 Tage vor Ablauf
- Automatische Erinnerungen einrichten

**Full Chain verwenden:**
- Bessere Browser-Kompatibilität
- Vermeidet Zertifikatsfehler
