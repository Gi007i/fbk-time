# Nginx Reverse Proxy Konfiguration (RHEL)

## Übersicht
Nginx dient als Reverse Proxy für FBK-Time und leitet Anfragen an Gunicorn weiter.

**Getestet auf:** RHEL, CentOS Stream 9, Rocky Linux 9, AlmaLinux 9

---

## Installation

```bash
sudo dnf install nginx -y

# SELinux Boolean setzen (Nginx darf zu Gunicorn verbinden)
sudo setsebool -P httpd_can_network_connect on
```

---

## Konfigurationspfade

- Konfigurationsdateien: `/etc/nginx/conf.d/`
- Hauptkonfiguration: `/etc/nginx/nginx.conf`
- SSL-Zertifikate: `/etc/pki/tls/certs/`, `/etc/pki/tls/private/`

---

## Konfigurationsdatei erstellen

```bash
sudo nano /etc/nginx/conf.d/fbk-time.conf
```

## Basis-Konfiguration (HTTP + HTTPS)

```nginx
# HTTP zu HTTPS umleiten
server {
    listen 80;
    server_name absence.example.com;
    return 301 https://$server_name$request_uri;
}

# HTTPS Server
server {
    listen 443 ssl http2;
    server_name absence.example.com;

    # SSL-Zertifikat Pfade (RHEL)
    ssl_certificate /etc/pki/tls/certs/fbk-time.crt;
    ssl_certificate_key /etc/pki/tls/private/fbk-time.key;

    # SSL-Protokolle und Ciphers
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;

    # SSL Session Cache
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 10m;

    # Security Headers
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;

    # Client Upload Limit
    client_max_body_size 10M;

    # Reverse Proxy zu Gunicorn
    location / {
        proxy_pass http://127.0.0.1:6000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Timeouts
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
    }

    # Statische Dateien direkt von Nginx ausliefern (Performance)
    location /static {
        alias /var/www/fbk-time/static;
        expires 1y;
        add_header Cache-Control "public, immutable";
        access_log off;
    }

    # Logs
    access_log /var/log/nginx/fbk-time-access.log;
    error_log /var/log/nginx/fbk-time-error.log;
}
```

---

## Konfiguration aktivieren

```bash
# Konfiguration testen
sudo nginx -t

# Nginx neustarten
sudo systemctl restart nginx

# Nginx beim Systemstart aktivieren
sudo systemctl enable nginx
```

**Hinweis:** Die SELinux-Kontexte für das Anwendungsverzeichnis werden in [configure-systemd-service.md](configure-systemd-service.md) konfiguriert.

---

## Firewall konfigurieren

```bash
# HTTP und HTTPS erlauben
sudo firewall-cmd --permanent --add-service=http
sudo firewall-cmd --permanent --add-service=https

# SSH erlauben (falls noch nicht)
sudo firewall-cmd --permanent --add-service=ssh

# Firewall neu laden
sudo firewall-cmd --reload

# Status prüfen
sudo firewall-cmd --list-all
```

---

## Logs überwachen

```bash
# Access Log
sudo tail -f /var/log/nginx/fbk-time-access.log

# Error Log
sudo tail -f /var/log/nginx/fbk-time-error.log
```

---

## Troubleshooting

### Nginx startet nicht

```bash
# Fehlerdetails anzeigen
sudo systemctl status nginx
sudo nginx -t

# SELinux Audit-Log prüfen
sudo ausearch -m AVC -ts recent | grep nginx

# SELinux temporär deaktivieren zum Testen (NUR ZUM DEBUGGEN!)
sudo setenforce 0
# Nach Test wieder aktivieren:
sudo setenforce 1

# Permanente Lösung: Korrekte Booleans setzen
sudo setsebool -P httpd_can_network_connect on
```

---

### 502 Bad Gateway

**Ursachen und Lösungen:**

1. **Gunicorn läuft nicht:**
```bash
sudo systemctl status fbk-time
sudo systemctl start fbk-time
```

2. **Port falsch konfiguriert:**
   - Prüfe `proxy_pass` URL (sollte `http://127.0.0.1:6000` sein)
   - Prüfe Gunicorn-Konfiguration (sollte auf Port 6000 lauschen)

3. **Firewall blockiert (lokal):**
```bash
sudo firewall-cmd --list-all
```

4. **SELinux blockiert Verbindung:**
```bash
# Nginx darf zu Gunicorn verbinden
sudo setsebool -P httpd_can_network_connect on

# Verifizieren
sudo getsebool httpd_can_network_connect
```

---

### 403 Forbidden

```bash
# Berechtigungen prüfen
ls -la /var/www/fbk-time/static

# SELinux Kontext prüfen
ls -Z /var/www/fbk-time/static

# Kontext korrigieren (siehe configure-systemd-service.md für vollständige SELinux-Konfiguration)
sudo restorecon -Rv /var/www/fbk-time
```

---

### SSL/TLS Fehler

1. **Zertifikat nicht gefunden:**
```bash
ls -l /etc/pki/tls/certs/fbk-time.crt
ls -l /etc/pki/tls/private/fbk-time.key
```

2. **SELinux blockiert Zertifikate:**
```bash
# Kontext prüfen
ls -Z /etc/pki/tls/certs/fbk-time.crt
ls -Z /etc/pki/tls/private/fbk-time.key

# Kontext korrigieren
sudo restorecon -Rv /etc/pki/tls/certs/
sudo restorecon -Rv /etc/pki/tls/private/
```

3. **Berechtigungen falsch:**
```bash
sudo chmod 644 /etc/pki/tls/certs/fbk-time.crt
sudo chmod 600 /etc/pki/tls/private/fbk-time.key
```

---

### Port bereits in Verwendung

```bash
# Prüfe, welcher Prozess Port 80/443 nutzt
sudo ss -tulpn | grep :80
sudo ss -tulpn | grep :443
```

---

## SELinux Checkliste (Nginx)

```bash
# 1. Boolean setzen (Nginx darf zu Gunicorn verbinden)
sudo setsebool -P httpd_can_network_connect on

# 2. Kontext auf Zertifikate prüfen
sudo restorecon -Rv /etc/pki/tls/

# 3. Audit-Log überwachen
sudo ausearch -m AVC -ts recent | grep nginx
```

**Hinweis:** Die vollständige SELinux-Konfiguration für das Anwendungsverzeichnis (Kontexte, Port-Freigabe) ist in [configure-systemd-service.md](configure-systemd-service.md) beschrieben.

---

## Performance-Optimierungen (Optional)

### Gzip Kompression aktivieren

```nginx
# In /etc/nginx/nginx.conf oder in server-Block

gzip on;
gzip_vary on;
gzip_proxied any;
gzip_comp_level 6;
gzip_types text/plain text/css text/xml text/javascript application/json application/javascript application/xml+rss application/rss+xml font/truetype font/opentype application/vnd.ms-fontobject image/svg+xml;
gzip_disable "msie6";
```

### Connection Limits

```nginx
# In http-Block von /etc/nginx/nginx.conf

limit_req_zone $binary_remote_addr zone=one:10m rate=10r/s;
limit_conn_zone $binary_remote_addr zone=addr:10m;

# In server-Block
limit_req zone=one burst=20;
limit_conn addr 10;
```

---

## Reload vs Restart

```bash
# Reload (ohne Downtime, für Konfig-Änderungen)
sudo nginx -s reload

# Restart (mit kurzer Downtime)
sudo systemctl restart nginx
```

---

## Hinweise

- Passe `server_name` an deine Domain/IP an
- Passe SSL-Zertifikat-Pfade an (siehe [install-ca-signed-ssl.md](install-ca-signed-ssl.md) oder [create-self-signed-ssl.md](create-self-signed-ssl.md))
- Passe den Pfad zu statischen Dateien an (`/var/www/fbk-time/static`)
- Standard-Port für Gunicorn ist 6000 (siehe `gunicorn.conf.py`)
- Nginx User auf RHEL: `nginx`
- Konfigurationsbeispiel: `config/examples/rhel/nginx-rhel.conf.example`
