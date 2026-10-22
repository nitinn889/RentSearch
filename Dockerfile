# Hosted Rent Radar: accounts on, listens on $PORT. Works on Render, Railway, Fly.io, Koyeb, a VPS…
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    RENT_RADAR_MODE=server \
    PORT=8000

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py ./
COPY radar ./radar
COPY static ./static
RUN useradd --create-home radar && mkdir -p /app/data && chown radar /app/data
USER radar

EXPOSE 8000
# One worker: scans and the scheduler live in this process. Threads serve requests meanwhile.
CMD ["sh", "-c", "exec gunicorn --workers 1 --threads 8 --timeout 120 --bind 0.0.0.0:${PORT} app:app"]
