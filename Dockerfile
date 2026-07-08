FROM python:3.11-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Tell Python's pip installer to use your lab proxy
ENV http_proxy=http://172.22.248.31:3128
ENV https_proxy=http://172.22.248.31:3128

RUN pip install --no-cache-dir fastapi uvicorn influxdb-client pydantic
COPY app.py .
EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
