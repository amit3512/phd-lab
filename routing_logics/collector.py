import paho.mqtt.client as mqtt
import json

broker_ip = "0.0.0.0"  # listen on all interfaces

def on_message(client, userdata, msg):
    data = json.loads(msg.payload.decode())
    temp = data["temperature"]
    pressure = data["pressure"]
    timestamp = data["timestamp"]

    # Example analysis: print and simple condition check
    status = "OK"
    if temp > 50:
        status = "HIGH TEMP ALERT!"
    print(f"Time: {timestamp}, Temp: {temp}°C, Pressure: {pressure} hPa -> {status}")

client = mqtt.Client("collector")
client.on_message = on_message
client.connect(broker_ip, 1883)
client.subscribe("sensors/pi1/bmp280")
client.loop_forever()