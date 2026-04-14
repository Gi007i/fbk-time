"""Gunicorn WSGI server configuration.

Production-ready settings for running Flask with Gunicorn.
Nginx reverse proxy architecture assumed.
"""

import json
from pathlib import Path

_base_dir = Path(__file__).resolve().parent
_settings_path = _base_dir / 'settings.json'

with open(_settings_path, 'r', encoding='utf-8') as f:
    _settings = json.load(f)

_server = _settings['system']['server']
_logs = _settings['system']['logs']

_runtime_path = Path(_server.get('runtime_path', 'data'))
if not _runtime_path.is_absolute():
    _runtime_path = _base_dir / _runtime_path
_runtime_path.mkdir(mode=0o755, parents=True, exist_ok=True)

_access_log_path = _base_dir / _logs['access_log']
_error_log_path = _base_dir / _logs['error_log']

for log_path in (_access_log_path, _error_log_path):
    log_path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)

bind = f"{_server['host']}:{_server['port']}"
backlog = 2048

# Multi-device concurrent access with same account
workers = 3
worker_class = "sync"
worker_connections = 1000
timeout = 30
keepalive = 2

max_requests = 1000
max_requests_jitter = 100
preload_app = True

accesslog = str(_access_log_path)
errorlog = str(_error_log_path)
loglevel = "info"
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)s'

limit_request_line = 4096
limit_request_fields = 100
limit_request_field_size = 8192

graceful_timeout = 30

pidfile = str(_runtime_path / "gunicorn.pid")
control_socket = str(_runtime_path / "gunicorn.ctl")

proc_name = "fbk-time"

worker_tmp_dir = "/dev/shm"
tmp_upload_dir = None

raw_env = [
    'FLASK_ENV=production',
]


def when_ready(server):
    server.log.info("Gunicorn server started")


def worker_int(worker):
    worker.log.info("Worker process terminated")


def pre_fork(server, worker):
    pass


def post_fork(server, worker):
    server.log.info(f"Worker {worker.pid} started")
