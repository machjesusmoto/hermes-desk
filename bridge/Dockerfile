FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY hermes_bridge/ hermes_bridge/

EXPOSE 8765
CMD ["python", "-m", "hermes_bridge.server"]
