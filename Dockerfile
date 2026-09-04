FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# state lives on the mounted volume; the ticker keeps races advancing
ENV VN_DB=/data/vn.sqlite \
    VN_ENABLE_TICKER=1 \
    PYTHONUNBUFFERED=1

EXPOSE 8080
# exactly one worker: SQLite single-writer + one background ticker.
# threads handle concurrent requests; generous timeout covers a race
# catching up after idle time and AI document extraction.
CMD ["gunicorn", "--workers", "1", "--threads", "8", "--timeout", "180", \
     "--bind", "0.0.0.0:8080", "app:app"]
