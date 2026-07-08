# import os
# import json
# from kubernetes import client, config
# import paho.mqtt.client as mqtt
# from influxdb_client import InfluxDBClient, Point
# from influxdb_client.client.write_api import SYNCHRONOUS

# # 1. Kubernetes Discovery Setup
# KUBECONFIG_PATH = "/home/pi/.kube/config"
# SERVICE_NAME = "influxdb-service"
# NAMESPACE = "default"

# def get_dynamic_influx_url():
#     """Queries the K8s cluster to find the currently active InfluxDB node."""
#     try:
#         config.load_kube_config(config_file=KUBECONFIG_PATH)
#         discovery_api = client.DiscoveryV1Api()
#         selector = f"kubernetes.io/service-name={SERVICE_NAME}"

#         # slices = discovery_api.list_namespaced_endpoint_slice(NAMESPACE, label_selector=selector)
#         slices = discovery_api.list_endpoint_slice_for_all_namespaces()
#         print(f"Got Query1: {slices.items}")

#         for slice_item in slices.items:
#             # print(f"Got Query1: {slices.items}")
#             for endpoint in slice_item.endpoints:
#                 if endpoint.conditions.ready:
#                     # Map K8s node names to their physical IPs
#                     print
#                     node_map = {"pi5": "172.22.251.21", "jetson-orin": "172.22.251.253"}
#                     node_ip = node_map.get(endpoint.node_name, "172.22.251.253")
#                     print(f"Got Query2: {endpoint.node_name}")
#                     return f"http://{node_ip}:30086"
#     except Exception as e:
#         print(f"Discovery Error: {e}")

#     # Fallback to hardcoded IP if discovery fails
#     return "http://172.22.251.21:30086"

# # 2. InfluxDB Client Factory
# def create_db_client():
#     url = get_dynamic_influx_url()
#     print(f"Connecting InfluxDB to: {url}")
#     db_client = InfluxDBClient(url=url, token="my-super-secret-admin-token", org="iot_edge_research")
#     return db_client, db_client.write_api(write_options=SYNCHRONOUS)

# # Initialize
# db_client, write_api = create_db_client()

# # 3. MQTT Logic
# def on_message(client, userdata, msg):
#     global write_api, db_client
#     try:
#         data = json.loads(msg.payload.decode())
#     except Exception as e:
#         print(f"JSON Error: {e}")
#         return

#     # Prepare data points (same logic as before)
#     temp = data.get("temperature")
#     pressure = data.get("pressure")
#     points_to_write = []

#     if temp is not None:
#         weather_point = Point("bmp280_weather").field("temperature", float(temp))
#         if pressure: weather_point.field("pressure", float(pressure))
#         points_to_write.append(weather_point)

#     # Stream to InfluxDB
#     try:
#         write_api.write(bucket="bmp280_metrics", org="iot_edge_research", record=points_to_write)
#         print(f"Successfully streamed to {db_client.url}")
#     except Exception:
#         print("Write failed, refreshing InfluxDB connection...")
#         db_client, write_api = create_db_client()

# # Configure MQTT
# client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="collector")
# client.on_message = on_message
# client.connect("0.0.0.0", 1883)
# client.subscribe("sensors/pi1/data")

# print("Collector active. Discovery enabled.")
# client.loop_forever()

from kubernetes import client, config

KUBECONFIG_PATH = "/home/pi/.kube/config"

service_table = {}
node_ip_cache = {}


def load_node_cache():

    config.load_kube_config(config_file=KUBECONFIG_PATH)

    v1 = client.CoreV1Api()

    nodes = v1.list_node()

    for node in nodes.items:

        node_name = node.metadata.name

        for addr in node.status.addresses:

            if addr.type == "InternalIP":

                node_ip_cache[node_name] = addr.address

                break

    print("\nNODE CACHE")
    print("----------------")

    for k, v in node_ip_cache.items():

        print(f"{k} -> {v}")


def discover_services():

    config.load_kube_config(config_file=KUBECONFIG_PATH)

    discovery_api = client.DiscoveryV1Api()

    slices = discovery_api.list_endpoint_slice_for_all_namespaces()

    for slice_item in slices.items:

        namespace = slice_item.metadata.namespace

        labels = slice_item.metadata.labels or {}

        service_name = labels.get("kubernetes.io/service-name")

        if not service_name:
            continue

        if not slice_item.endpoints:
            print(f"No endpoints: " f"{namespace}/{service_name}")
            continue

        for endpoint in slice_item.endpoints:

            if endpoint.conditions and endpoint.conditions.ready:

                pod_ip = endpoint.addresses[0]

                node_name = endpoint.node_name

                node_ip = node_ip_cache.get(node_name)

                service_key = f"{namespace}/{service_name}"

                service_table[service_key] = {
                    "service_name": service_name,
                    "namespace": namespace,
                    "pod_ip": pod_ip,
                    "node_name": node_name,
                    "node_ip": node_ip,
                }


# def print_service_table():

#     print("\nSERVICE ROUTING TABLE")
#     print("=" * 80)


#     for service, data in service_table.items():

#         print(
#             f"""
# Service      : {data['service_name']}
# Namespace    : {data['namespace']}
# Pod IP       : {data['pod_ip']}
# Node Name    : {data['node_name']}
# Node IP      : {data['node_ip']}
# ----------------------------------------
# """
#         )


def on_message(client, userdata, msg):

    try:
        data = json.loads(msg.payload.decode())

    except Exception as e:
        print("JSON error:", e)
        return

    sensor_type = data.get("sensor_type")

    if sensor_type not in service_routes:

        print("Unknown sensor:", sensor_type)

        return

    destination = service_routes[sensor_type]

    print(f"""
Routing decision:
Sensor : {sensor_type}
Service: {destination['service']}
URL    : {destination['url']}
""")

    send_to_influx(destination["url"], data)


if __name__ == "__main__":

    load_node_cache()

    discover_services()

    # print_service_table()
    on_message()
