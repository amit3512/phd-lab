FROM python:3.10-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY speed_tracker.py .

EXPOSE 8000

CMD ["uvicorn", "speed_tracker:app", "--host", "0.0.0.0", "--port", "8000"]
