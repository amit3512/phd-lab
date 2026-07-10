# import os
# import json
# import paho.mqtt.client as mqtt
# from influxdb_client import InfluxDBClient, Point
# from influxdb_client.client.write_api import SYNCHRONOUS

# # 1. InfluxDB Configuration pointing to your Kubernetes cluster
# INFLUX_URL = os.getenv("INFLUXDB_URL", "http://172.22.251.21:30086")
# INFLUX_TOKEN = os.getenv("INFLUXDB_TOKEN", "my-super-secret-admin-token")
# INFLUX_ORG = os.getenv("INFLUXDB_ORG", "iot_edge_research")
# INFLUX_BUCKET = os.getenv("INFLUXDB_BUCKET", "bmp280_metrics")

# # Initialize InfluxDB Client
# try:
#     db_client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
#     write_api = db_client.write_api(write_options=SYNCHRONOUS)
#     print(f"Successfully initialized connection to InfluxDB at {INFLUX_URL}")
# except Exception as e:
#     print(f"DB Initialization Error: {e}")
#     write_api = None

# # MQTT Broker Configuration
# BROKER_IP = "0.0.0.0"  # Listens on all interfaces on pi2
# TOPIC = "sensors/pi1/data"

# def on_message(client, userdata, msg):
#     try:
#         data = json.loads(msg.payload.decode())
#     except Exception as e:
#         print(f"Failed to parse JSON payload: {e}")
#         return

#     # Extract BMP280 Metrics
#     temp = data.get("temperature")
#     pressure = data.get("pressure")
#     timestamp = data.get("timestamp")
    
#     # Extract GPS Metrics
#     lat = data.get("latitude")
#     lon = data.get("longitude")
#     alt = data.get("altitude")
#     speed = data.get("speed")

#     # Determine Temperature Alert Status
#     status = "OK"
#     if temp is not None and temp > 50:
#         status = "HIGH TEMP ALERT!"

#     # Terminal logs (using standard text strings to prevent encoding crashes)
#     if lat is not None and lon is not None:
#         print(f"GPS -> Lat: {lat}, Lon: {lon}, Alt: {alt} m, Speed: {speed} m/s")
#     else:
#         print("GPS data not available")
#     print(f"Time: {timestamp}, Temp: {temp} C, Pressure: {pressure} hPa -> {status}")

#     # Process and stream to InfluxDB if baseline telemetry is valid
#     if write_api and temp is not None and pressure is not None:
#         try:
#             # Build the time-series record
#             point = (
#                 Point("bmp280_weather")
#                 .tag("sensor_id", "pi1")
#                 .field("temperature", float(temp))
#                 .field("pressure", float(pressure))
#             )

#             # Append optional spatial fields if GPS fix is good
#             if lat is not None and lon is not None:
#                 point.field("latitude", float(lat))
#                 point.field("longitude", float(lon))
#                 if alt is not None: 
#                     point.field("altitude", float(alt))
#                 if speed is not None: 
#                     point.field("speed", float(speed))

#             # Bind the explicit incoming data timestamp into InfluxDB nanoseconds
#             if timestamp is not None:
#                 point.time(int(float(timestamp) * 1_000_000_000))

#             # Synchronous write directly over the NodePort bridge into K8s
#             write_api.write(bucket=INFLUX_BUCKET, org=INFLUX_ORG, record=point)
#             print("   -> Streamed cleanly to K8s InfluxDB\n")

#         except Exception as e:
#             print(f"   -> Failed writing to InfluxDB: {e}\n")
#     else:
#         print("   -> Skipped database push (Missing keys or Client offline)\n")

# # Configure and deploy Paho MQTT Client (v2 API standard)
# client = mqtt.Client(
#     mqtt.CallbackAPIVersion.VERSION2,
#     client_id="collector"
# )
# client.on_message = on_message

# print(f"Connecting to local MQTT broker network...")
# client.connect(BROKER_IP, 1883)
# client.subscribe(TOPIC)

# print(f"Active. Listening for MQTT streams on '{TOPIC}'... (Ctrl+C to stop)")
# client.loop_forever()

import os
import json
import paho.mqtt.client as mqtt
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS

# 1. InfluxDB Configuration pointing to your Kubernetes cluster
INFLUX_URL = os.getenv("INFLUXDB_URL", "http://172.22.251.21:30086")
INFLUX_TOKEN = os.getenv("INFLUXDB_TOKEN", "my-super-secret-admin-token")
INFLUX_ORG = os.getenv("INFLUXDB_ORG", "iot_edge_research")
INFLUX_BUCKET = os.getenv("INFLUXDB_BUCKET", "bmp280_metrics")

# Initialize InfluxDB Client
try:
    db_client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
    write_api = db_client.write_api(write_options=SYNCHRONOUS)
    print(f"Successfully initialized connection to InfluxDB at {INFLUX_URL}")
except Exception as e:
    print(f"DB Initialization Error: {e}")
    write_api = None

# MQTT Broker Configuration
BROKER_IP = "0.0.0.0"  # Listens on all interfaces on pi2
TOPIC = "sensors/pi1/data"

def on_message(client, userdata, msg):
    try:
        data = json.loads(msg.payload.decode())
    except Exception as e:
        print(f"Failed to parse JSON payload: {e}")
        return

    # Extract BMP280 Metrics
    temp = data.get("temperature")
    pressure = data.get("pressure")
    timestamp = data.get("timestamp")
    
    # Extract GPS Metrics
    lat = data.get("latitude")
    lon = data.get("longitude")
    alt = data.get("altitude")
    speed = data.get("speed")

    # Determine Temperature Alert Status
    status = "OK"
    if temp is not None and temp > 50:
        status = "HIGH TEMP ALERT!"

    # Terminal logs (using standard text strings to prevent encoding crashes)
    if lat is not None and lon is not None:
        print(f"GPS -> Lat: {lat}, Lon: {lon}, Alt: {alt} m, Speed: {speed} m/s")
    else:
        print("GPS data not available")
    print(f"Time: {timestamp}, Temp: {temp} C, Pressure: {pressure} hPa -> {status}")

    # Process and stream to InfluxDB
    if write_api:
        points_to_write = []

        # TABLE 1: Weather Point (Only Temp & Pressure)
        if temp is not None and pressure is not None:
            weather_point = (
                Point("bmp280_weather")
                .tag("sensor_id", "bmp280")
                .field("temperature", float(temp))
                .field("pressure", float(pressure))
            )
            if timestamp is not None:
                weather_point.time(int(float(timestamp) * 1_000_000_000))
            points_to_write.append(weather_point)

        # TABLE 2: GPS Point (Only Spatial coordinates/speed)
        if lat is not None and lon is not None:
            gps_point = (
                Point("pa1010d_gps")
                .tag("sensor_id", "pa1010d")
                .field("latitude", float(lat))
                .field("longitude", float(lon))
            )
            if alt is not None: 
                gps_point.field("altitude", float(alt))
            if speed is not None: 
                gps_point.field("speed", float(speed))
            
            if timestamp is not None:
                gps_point.time(int(float(timestamp) * 1_000_000_000))
            points_to_write.append(gps_point)

        # Send both "tables" cleanly in a single batch push to K8s InfluxDB
        if points_to_write:
            try:
                write_api.write(bucket=INFLUX_BUCKET, org=INFLUX_ORG, record=points_to_write)
                print(f"   -> Streamed {len(points_to_write)} separate measurements to K8s\n")
            except Exception as e:
                print(f"   -> Failed writing to InfluxDB: {e}\n")
    else:
        print("   -> Skipped database push (Client offline)\n")

# Configure and deploy Paho MQTT Client (v2 API standard)
client = mqtt.Client(
    mqtt.CallbackAPIVersion.VERSION2,
    client_id="collector"
)
client.on_message = on_message

print(f"Connecting to local MQTT broker network...")
client.connect(BROKER_IP, 1883)
client.subscribe(TOPIC)

print(f"Active. Listening for MQTT streams on '{TOPIC}'... (Ctrl+C to stop)")
client.loop_forever()