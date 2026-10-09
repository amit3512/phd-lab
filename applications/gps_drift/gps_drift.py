from fastapi import FastAPI
from pydantic import BaseModel
from datetime import datetime
import os
import math

app = FastAPI(title="PA1010D GPS Drift & Accuracy Service", version="1.0")

# Reference/Home coordinates for your fixed Breakout Garden sensor
# (You can override these later with Kubernetes environment variables)
REF_LAT = float(os.getenv("REF_LAT", "27.7172"))
REF_LON = float(os.getenv("REF_LON", "85.3240"))

class GPSDriftData(BaseModel):
    latitude: float
    longitude: float
    altitude: float = 0.0
    satellites: int = 0
    timestamp: str = None

def calculate_distance_meters(lat1, lon1, lat2, lon2):
    """Calculates distance between two GPS points in meters using Haversine formula."""
    R = 6371000  # Radius of earth in meters
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = math.sin(delta_phi / 2.0)**2 + \
        math.cos(phi1) * math.cos(phi2) * \
        math.sin(delta_lambda / 2.0)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return R * c

@app.post("/gps", status_code=200)
def process_gps_drift(data: GPSDriftData):
    processed_time = data.timestamp or datetime.utcnow().isoformat()
    
    # Calculate drift from the fixed reference point
    drift_meters = calculate_distance_meters(REF_LAT, REF_LON, data.latitude, data.longitude)
    
    # Real-time drift classification thresholds
    if drift_meters < 2.0:
        drift_status = "optimal_stability"
    elif drift_meters < 5.0:
        drift_status = "minor_drift"
    else:
        drift_status = "high_drift_warning"

    # Log to container stdout (viewable via `kubectl logs`)
    print(f"[{processed_time}] Drift Check -> Lat: {data.latitude}, Lon: {data.longitude} | Drift: {drift_meters:.2f}m | Sats: {data.satellites} | Status: {drift_status}")

    return {
        "status": "success",
        "processed_at": processed_time,
        "reference_point": {"latitude": REF_LAT, "longitude": REF_LON},
        "current_position": {
            "latitude": data.latitude,
            "longitude": data.longitude,
            "altitude": data.altitude,
            "satellites": data.satellites
        },
        "analytics": {
            "drift_distance_meters": round(drift_meters, 2),
            "stability_rating": drift_status
        }
    }

@app.get("/health")
def health_check():
    return {"status": "healthy", "service": "gps-drift-analytics"}