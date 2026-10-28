"""The training container's app (app/services/training.py says what it serves).

    gunicorn app.services.training_app:app -k uvicorn.workers.UvicornWorker
"""

from app import service
from app.services import training

app = service.new_app("BRT 360 — Training")
service.include(app, *training.routers())
service.finish(app)
