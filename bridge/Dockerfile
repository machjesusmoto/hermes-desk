FROM python:3.12-slim

WORKDIR /app

COPY bridge/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bridge/bridge.py .

EXPOSE 8080

CMD ["python", "bridge.py"]
