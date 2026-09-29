"""Gunicorn WSGI server configuration behind an Nginx reverse proxy."""

import json
from pathlib import Path

_base_dir = Path(__file__).resolve().parent
_settings_path = _base_dir / 'settings.json'

with open(_settings_path, 'r', encoding='utf-8') as f:
    _settings = json.load(f)

try:
    _server = _settings['system']['server']
    _logs = _settings['system']['logs']
    _host = _server['host']
    _port = _server['port']
    _runtime_path_value = _server['runtime_path']
    _access_log = _logs['access_log']
    _error_log = _logs['error_log']
except KeyError as exc:
    raise SystemExit(
        f"settings.json: Pflichtfeld {exc} fehlt — die "
        f"Server-Konfiguration ist unvollständig."
    )

_runtime_path = Path(_runtime_path_value)
if not _runtime_path.is_absolute():
    _runtime_path = _base_dir / _runtime_path
_runtime_path.mkdir(mode=0o750, parents=True, exist_ok=True)

_access_log_path = _base_dir / _access_log
_error_log_path = _base_dir / _error_log

for log_path in (_access_log_path, _error_log_path):
    log_path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)

bind = f"{_host}:{_port}"
backlog = 2048

workers = 3
worker_class = "sync"
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


def when_ready(server):
    server.log.info("Gunicorn server started")


def worker_int(worker):
    worker.log.info("Worker process terminated")


def post_fork(server, worker):
    server.log.info(f"Worker {worker.pid} started")
    from app import app as application
    from core.db import db
    from core.backup import start_auto_discovery
    from core.scheduler import start_scheduler

    # A SQLite connection shared across forks corrupts its lock state.
    # close=False abandons the inherited connections without closing the
    # master's handles.
    with application.app_context():
        db.engine.dispose(close=False)

    # Threads start after the fork, never in the preloaded master; the
    # scheduler's process-bound lock lets exactly one worker run the tasks.
    start_scheduler(application)

    start_auto_discovery(application)
