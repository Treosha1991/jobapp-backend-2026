import os


# Photo messages use multipart uploads and may arrive over a slow mobile
# connection. Keep the worker alive long enough to receive and normalize them.
timeout = int(os.getenv("GUNICORN_TIMEOUT_SECONDS", "180"))
graceful_timeout = int(os.getenv("GUNICORN_GRACEFUL_TIMEOUT_SECONDS", "30"))
