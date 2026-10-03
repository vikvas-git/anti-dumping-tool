# syntax=docker/dockerfile:1
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY anti_dumping_api.py .

# Code Engine expects the app to listen on $PORT (default 8080)
ENV PORT=8080
EXPOSE 8080

CMD ["sh", "-c", "uvicorn anti_dumping_api:app --host 0.0.0.0 --port ${PORT}"]
