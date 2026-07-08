import json
import paho.mqtt.client as mqtt
from kubernetes import client, config
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS

# =====================================================
# SENSOR ROUTING TABLE
# Replace later with Kubernetes EndpointSlice discovery
# =====================================================

# service_routes = {
#     "bmp280": {
#         "service_name": "influxdb-service-bmp280",
#         "namespace": "default",
#         "node_name": "pi-worker3",
#         "node_ip": "172.22.251.30",
#         "url": "http://172.22.251.30:30086",
#     },
#     "pa1010d": {
#         "service_name": "bmp",
#         "namespace": "default",
#         "node_name": "pi-worker5",
#         "node_ip": "172.22.251.21",
#         "url": "http://172.22.251.21:30086",
#     },
# }

# 1. Kubernetes Discovery Setup
KUBECONFIG_PATH = "/home/pi/.kube/config"


# =====================================================
# INFLUXDB CONFIGURATION
# =====================================================

INFLUX_TOKEN = "my-super-secret-admin-token"
INFLUX_ORG = "iot_edge_research"
INFLUX_BUCKET_BMP = "bmp280_metrics"
INFLUX_BUCKET_PA1010D = "pa1010d_metrics"
SERVICE_NAME = "influxdb-service"


def get_dynamic_influx_url():
    """Queries the K8s cluster to find the currently active InfluxDB node."""
    try:
        config.load_kube_config(config_file=KUBECONFIG_PATH)
        discovery_api = client.DiscoveryV1Api()
        selector = f"kubernetes.io/service-name={SERVICE_NAME}"

        # slices = discovery_api.list_namespaced_endpoint_slice(NAMESPACE, label_selector=selector)
        slices = discovery_api.list_endpoint_slice_for_all_namespaces()
        print(f"Got Query1: {slices.items}")

        for slice_item in slices.items:
            print(f"Got Query1: {slices.items}")
            for endpoint in slice_item.endpoints:
                if endpoint.conditions.ready:
                    # Map K8s node names to their physical IPs
                    # print
                    node_map = {"pi3": "172.22.251.30", "jetson-orin": "172.22.251.253"}
                    node_ip = node_map.get(endpoint.node_name, "172.22.251.253")
                    print(f"Got Query2: {endpoint.node_name}")
                    return f"http://{node_ip}:30086"
    except Exception as e:
        print(f"Discovery Error: {e}")

    # Fallback to hardcoded IP if discovery fails
    return "http://172.22.251.30:30086"


# 2. InfluxDB Client Factory
def create_db_client():
    url = get_dynamic_influx_url()
    print(f"Connecting InfluxDB to: {url}")
    db_client = InfluxDBClient(
        url=url, token="my-super-secret-admin-token", org="iot_edge_research"
    )
    return db_client, db_client.write_api(write_options=SYNCHRONOUS)


# Initialize
db_client, write_api = create_db_client()

# =====================================================
# CREATE INFLUXDB CONNECTION
# =====================================================


# def create_influx_client(url):

#     print(f"Connecting InfluxDB: {url}")

#     client = InfluxDBClient(url=url, token=INFLUX_TOKEN, org=INFLUX_ORG)

#     write_api = client.write_api(write_options=SYNCHRONOUS)

#     return client, write_api


# =====================================================
# WRITE DATA TO SELECTED INFLUXDB
# =====================================================


# def write_sensor_data(destination, data):

#     influx_url = destination["url"]

#     db_client, write_api = create_influx_client(influx_url)

#     # print({data})
#     sensor_type = data.get(
#         # "sensor_type"
#         "service_type"
#     )

#     point = Point(sensor_type)

#     for key, value in data.items():

#         if key == "sensor_type":
#             continue

#         try:

#             point.field(key, float(value))

#         except:

#             point.field(key, str(value))

#     try:

#         write_api.write(bucket=INFLUX_BUCKET, org=INFLUX_ORG, record=point)

#         print("Data written successfully")

#     except Exception as e:

#         print("Influx write error:", e)

#     finally:

#         db_client.close()


# =====================================================
# ROUTING DECISION ENGINE
# =====================================================


# def route_sensor_data(data):

#     # print(data)
#     # sensor_type = data.get(
#     #     "sensor_type"
#     #     # "service_type"
#     # )
#     sensor_type = (
#         data.get("sensor_type") or data.get("service_type") or data.get("service-type")
#     )

#     if sensor_type is None:

#         print("Missing sensor_type")

#         return

#     destination = service_routes.get(sensor_type)

#     if destination is None:

#         print(f"No destination for {sensor_type}")

#         return

#     print(
#         """
# =================================
# ROUTING DECISION
# =================================
# Sensor      : {}
# Service     : {}
# Namespace   : {}
# Node        : {}
# Node IP     : {}
# URL         : {}
# =================================
# """.format(
#             sensor_type,
#             destination["service_name"],
#             destination["namespace"],
#             destination["node_name"],
#             destination["node_ip"],
#             destination["url"],
#         )
#     )

#     write_sensor_data(destination, data)


# =====================================================
# MQTT CALLBACK
# =====================================================


# def on_message(client, userdata, msg):

#     try:

#         payload = msg.payload.decode()

#         data = json.loads(payload)

#     except Exception as e:

#         print("MQTT JSON error:", e)

#         return

#     print("\nReceived:")

#     print(data)

#     route_sensor_data(data)

# # 3. MQTT Logic


def on_message(client, userdata, msg):
    global write_api, db_client

    try:
        points_to_write_bmp = []
        points_to_write_pa1010d = []

        data = json.loads(msg.payload.decode())
        print(f"{data.get("service_type")}")
        if data.get("service_type") == "bmp280":
            temp = data.get("temperature")
            pressure = data.get("pressure")
            timestamp = data.get("timestamp")

            weather_point = (
                Point("bmp280")
                .field("temperature", float(temp))
                .field("pressure", float(pressure))
            )
            if timestamp is not None:
                weather_point.time(int(float(timestamp) * 1_000_000_000))

            #         if pressure: weather_point.field("pressure", float(pressure))
            points_to_write_bmp.append(weather_point)
            if points_to_write_bmp:
                try:
                    write_api.write(
                        bucket=INFLUX_BUCKET_BMP,
                        org=INFLUX_ORG,
                        record=points_to_write_bmp,
                    )
                    print(
                        f"   -> Streamed {len(points_to_write_bmp)} separate measurements to K8s {data}\n"
                    )
                except Exception as e:
                    print(f"   -> Failed writing to InfluxDB: {e}\n")

        if data.get("service_type") == "pa1010d":
            lat = data.get("latitude")
            lon = data.get("longitude")
            alt = data.get("altitude")
            speed = data.get("speed")
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
                points_to_write_pa1010d.append(gps_point)

                # if timestamp is not None:
                #     gps_point.time(int(float(timestamp) * 1_000_000_000))
                if points_to_write_pa1010d:
                    try:
                        write_api.write(
                            bucket=INFLUX_BUCKET_PA1010D,
                            org=INFLUX_ORG,
                            record=points_to_write_pa1010d,
                        )
                        print(
                            f"   -> Streamed {len(points_to_write_pa1010d)} separate measurements to K8s pa1010d {data}\n"
                        )
                    except Exception as e:
                        print(f"   -> Failed writing to InfluxDB: {e}\n")

    except Exception as e:
        print(f"JSON Error: {e}")
        return

    # # Prepare data points (same logic as before)
    # temp = data.get("temperature")
    # pressure = data.get("pressure")
    # points_to_write = []

    # if temp is not None:
    #     weather_point = Point("bmp280_weather").field("temperature", float(temp))
    #     if pressure:
    #         weather_point.field("pressure", float(pressure))
    #     points_to_write.append(weather_point)

    # # Stream to InfluxDB
    # try:
    #     write_api.write(
    #         bucket="bmp280_metrics", org="iot_edge_research", record=points_to_write
    #     )
    #     print(f"Successfully streamed to {db_client.url}")
    # except Exception:
    #     print("Write failed, refreshing InfluxDB connection...")
    #     db_client, write_api = create_db_client()


# =====================================================
# MQTT STARTUP
# =====================================================

mqtt_client = mqtt.Client(
    mqtt.CallbackAPIVersion.VERSION2, client_id="pi2-routing-engine"
)


mqtt_client.on_message = on_message


mqtt_client.connect("0.0.0.0", 1883)


mqtt_client.subscribe("sensors/#")


print("""
=====================================
PI2 SENSOR ROUTING ENGINE STARTED
=====================================
Listening:
    sensors/#

Routes:

BMP280
 |
 v
influxdb-service-bmp280
 |
 v
pi-worker3
172.22.251.30


PA1010D
 |
 v
influxdb-service-pa1010d
 |
 v
pi-worker5
172.22.251.21

=====================================
""")


mqtt_client.loop_forever()
