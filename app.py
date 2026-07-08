import os
from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS

app = FastAPI(title="BMP280 IoT Ingestion Engine")

INFLUX_URL = os.getenv("INFLUXDB_URL", "http://influxdb-service:8086")
INFLUX_TOKEN = os.getenv("INFLUXDB_TOKEN", "my-super-secret-admin-token")
INFLUX_ORG = os.getenv("INFLUXDB_ORG", "iot_edge_research")
INFLUX_BUCKET = os.getenv("INFLUXDB_BUCKET", "bmp280_metrics")

try:
    client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
    write_api = client.write_api(write_options=SYNCHRONOUS)
except Exception as e:
    print(f"DB Error: {e}")

class BMP280Payload(BaseModel):
    sensor_id: str
    temperature: float
    pressure: float

@app.post("/api/v1/telemetry", status_code=status.HTTP_201_CREATED)
async def ingest_sensor_data(data: BMP280Payload):
    try:
        point = (
            Point("bmp280_weather")
            .tag("sensor_id", data.sensor_id)
            .field("temperature", data.temperature)
            .field("pressure", data.pressure)
        )
        # We handle write parameters at the API level instead of inside the Point object
        write_api.write(bucket=INFLUX_BUCKET, org=INFLUX_ORG, record=point)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/healthz")
async def health_check():
    return {"status": "online"}
