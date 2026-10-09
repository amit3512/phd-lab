from fastapi import FastAPI
from pydantic import BaseModel
from datetime import datetime

app = FastAPI(title="PA1010D Real-Time GPS Service", version="1.0")

class GPSData(BaseModel):
    latitude: float
    longitude: float
    altitude: float = 0.0
    speed: float = 0.0
    timestamp: str = None

@app.post("/gps", status_code=200)
def process_gps(data: GPSData):
    """
    Real-time endpoint: receives GPS payload, processes it instantly,
    logs it to container stdout, checks for real-time thresholds,
    and returns a response without writing to a database.
    """
    processed_time = data.timestamp or datetime.utcnow().isoformat()
    
    # Real-time processing logic example (e.g., speed check / alert)
    is_speeding = data.speed > 80.0
    
    # Print to container logs (viewable via `kubectl logs`)
    print(f"[{processed_time}] GPS Received -> Lat: {data.latitude}, Lon: {data.longitude}, Speed: {data.speed}")
    if is_speeding:
        print(f"WARNING: Speed threshold exceeded! Speed: {data.speed}")

    return {
        "status": "success",
        "processed_at": processed_time,
        "coordinates": {
            "latitude": data.latitude,
            "longitude": data.longitude,
            "altitude": data.altitude,
            "speed": data.speed
        },
        "alert": "speeding_limit_exceeded" if is_speeding else "normal"
    }

@app.get("/health")
def health_check():
    return {"status": "healthy"}