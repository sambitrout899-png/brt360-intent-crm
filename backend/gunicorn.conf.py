"""gunicorn reads this file by itself from its working folder (backend/, in
both the systemd and the Docker setup). Only what metrics need is here: one
folder where every worker writes its Prometheus samples, so /metrics can add
them up (app/observability.py). Everything else stays on the command line."""

import os
import shutil
import tempfile

# set before the workers start (and import prometheus_client): they inherit it
METRICS_DIR = os.environ.setdefault("PROMETHEUS_MULTIPROC_DIR", os.path.join(tempfile.gettempdir(), "brt360-metrics"))


def on_starting(server):
    # samples from an earlier run would be counted again
    shutil.rmtree(METRICS_DIR, ignore_errors=True)
    os.makedirs(METRICS_DIR, exist_ok=True)


def child_exit(server, worker):
    from prometheus_client import multiprocess

    multiprocess.mark_process_dead(worker.pid)
