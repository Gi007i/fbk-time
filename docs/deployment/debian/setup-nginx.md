# Nginx Reverse Proxy Konfiguration (Debian/Ubuntu)

## Übersicht
Nginx dient als Reverse Proxy für FBK-Time und leitet Anfragen an Gunicorn weiter.

**Getestet auf:** Ubuntu 22.04 LTS, Ubuntu 24.04 LTS, Debian 11, Debian 12

---

## Installation

```bash
sudo apt update
sudo apt install nginx -y
```

---

## Konfigurationspfade

- Konfigurationsdateien: `/etc/nginx/sites-available/`
- Aktivierte Sites: `/etc/nginx/sites-enabled/` (Symlinks)
- Hauptkonfiguration: `/etc/nginx/nginx.conf`
- SSL-Zertifikate: `/etc/ssl/certs/`, `/etc/ssl/private/`

---

## Konfigurationsdatei erstellen

```bash
sudo nano /etc/nginx/sites-available/fbk-time
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

    # SSL-Zertifikat Pfade (Debian/Ubuntu)
    ssl_certificate /etc/ssl/certs/fbk-time.crt;
    ssl_certificate_key /etc/ssl/private/fbk-time.key;

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
# Symlink erstellen (aktiviert die Konfiguration)
sudo ln -s /etc/nginx/sites-available/fbk-time /etc/nginx/sites-enabled/

# Konfiguration testen
sudo nginx -t

# Nginx neustarten
sudo systemctl restart nginx

# Nginx beim Systemstart aktivieren
sudo systemctl enable nginx
```

---

## Firewall konfigurieren (UFW)

```bash
# UFW aktivieren (falls noch nicht aktiv)
sudo ufw allow 'Nginx Full'
sudo ufw allow OpenSSH
sudo ufw enable

# Status prüfen
sudo ufw status
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
sudo ufw status
```

---

### 403 Forbidden

```bash
# Besitzer ändern
sudo chown -R www-data:www-data /var/www/fbk-time

# Berechtigungen setzen
sudo chmod -R 755 /var/www/fbk-time
```

---

### SSL/TLS Fehler

1. **Zertifikat nicht gefunden:**
```bash
ls -l /etc/ssl/certs/fbk-time.crt
ls -l /etc/ssl/private/fbk-time.key
```

2. **Berechtigungen falsch:**
```bash
sudo chmod 644 /etc/ssl/certs/fbk-time.crt
sudo chmod 600 /etc/ssl/private/fbk-time.key
```

---

### Port bereits in Verwendung

```bash
# Prüfe, welcher Prozess Port 80/443 nutzt
sudo ss -tulpn | grep :80
sudo ss -tulpn | grep :443
```

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
- Nginx User auf Debian/Ubuntu: `www-data`
- Konfigurationsbeispiel: `config/examples/debian/nginx-debian.conf.example`
