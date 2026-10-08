FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
# The worker runs every demo parse as this unprivileged account, never as root
# (app/workers/child_process.py). Empty keeps the child on the worker's user.
ENV PARSE_CHILD_USER=parser

WORKDIR /app

# Hash-pinned, compiled from requirements.txt for Linux / CPython 3.12 (the
# command is in the lock's header): a rebuild of an old commit gets the exact
# dependencies it shipped with, and a tampered or swapped wheel fails the install.
COPY requirements.lock .
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

# No home, no login shell, no password: the account only ever runs the parse
# child, which reads untrusted .dem bytes in a native extension.
RUN groupadd --system parser \
    && useradd --system --gid parser --no-create-home --home-dir /nonexistent \
        --shell /usr/sbin/nologin parser

COPY app ./app
RUN mkdir -p /data/uploads /data/replays /data/summaries /data/videos

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
