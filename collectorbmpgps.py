import paho.mqtt.client as mqtt
import json

broker_ip = "0.0.0.0"  # listen on all interfaces

def on_message(client, userdata, msg):
    data = json.loads(msg.payload.decode())

    temp = data.get("temperature")
    pressure = data.get("pressure")
    timestamp = data.get("timestamp")

    # Basic status check
    status = "OK"
    if temp is not None and temp > 50:
        status = "HIGH TEMP ALERT!"

    print(f"Time: {timestamp}, Temp: {temp}°C, Pressure: {pressure} hPa -> {status}")

    # 🔹 NEW: GPS handling
    lat = data.get("latitude")
    lon = data.get("longitude")
    alt = data.get("altitude")
    speed = data.get("speed")

    if lat is not None and lon is not None:
        print(f"GPS -> Lat: {lat}, Lon: {lon}, Alt: {alt} m, Speed: {speed} m/s")
    else:
        print("GPS data not available")

# client = mqtt.Client("collector")
client = mqtt.Client(
    mqtt.CallbackAPIVersion.VERSION2,
    client_id="collector"
)
client.on_message = on_message

client.connect(broker_ip, 1883)

# 🔹 IMPORTANT: update topic
client.subscribe("sensors/pi1/data")

client.loop_forever()
