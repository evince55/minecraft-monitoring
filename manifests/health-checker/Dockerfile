FROM python:3.12-slim

RUN useradd -r -s /bin/false appuser

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

USER appuser

CMD ["python", "main.py"]
