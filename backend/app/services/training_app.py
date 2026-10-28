"""The training container's app (app/services/training.py says what it serves).

    gunicorn app.services.training_app:app -k uvicorn.workers.UvicornWorker
"""

from app.services import split_app

app = split_app.create("training")
