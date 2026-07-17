import json
import os
import threading
import paho.mqtt.client as mqtt
from kubernetes import client, config
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS

# Configuration
INFLUX_TOKEN = "my-super-secret-admin-token"
INFLUX_ORG = "iot_edge_research"
service_table = {}
table_lock = threading.Lock()
_influx_client_cache = {}


def initialize_k8s():
    """Initializes K8s config. Looks for local kubeconfig file."""
    try:
        # Since you are routing from outside, we use local kubeconfig
        config.load_kube_config(config_file="/home/pi/.kube/config")
        print("K8s: Local kubeconfig loaded.")
    except Exception as e:
        print(f"K8s Init Error: {e}")


def get_service_route(svc, net_v1):
    """
    Determines the URL using the Ingress Host and the Ingress Controller's NodePort.
    """
    svc_name = svc.metadata.name
    print(svc_name)
    namespace = svc.metadata.namespace

    # The NodePort your Ingress Controller is listening on
    INGRESS_NODE_PORT = 32213

    # 1. Search for an Ingress that covers this service
    try:
        ingresses = net_v1.list_namespaced_ingress(namespace)
        for ing in ingresses.items:
            if ing.spec.rules:
                for rule in ing.spec.rules:
                    # Check every path in the rule
                    if rule.http and rule.http.paths:
                        for path in rule.http.paths:
                            # Verify if this Ingress rule routes to our target service
                            if (
                                path.backend.service
                                and path.backend.service.name == svc_name
                            ):
                                # Return the domain name + the Ingress Controller's NodePort
                                print(
                                    f"DEBUG: Found route for {svc_name}: Host={rule.host}, Path={path.path} {path.backend.service.name}"
                                )
                                return f"http://{rule.host}:{INGRESS_NODE_PORT}"
    except Exception as e:
        print(f"Ingress lookup error for {svc_name}: {e}")

    # 2. Return None if no Ingress is found for this service
    return None


def refresh_routes():
    """Discover services and update the routing table."""
    v1 = client.CoreV1Api()
    net_v1 = client.NetworkingV1Api()

    services = v1.list_service_for_all_namespaces()

    with table_lock:
        service_table.clear()
        for svc in services.items:
            url = get_service_route(svc, net_v1)
            if url:
                service_table[svc.metadata.name] = url
                print(f"Mapped: {svc.metadata.name} -> {url}")


def write_to_influx(url, point, sensor_type):
    if url not in _influx_client_cache:
        _influx_client_cache[url] = InfluxDBClient(
            url=url, token=INFLUX_TOKEN, org=INFLUX_ORG
        ).write_api(write_options=SYNCHRONOUS)

    try:
        _influx_client_cache[url].write(
            bucket=f"bucket_{sensor_type}", org=INFLUX_ORG, record=point
        )
        print(f"Sent {sensor_type} data to {url}")
    except Exception as e:
        print(f"Write error to {url}: {e}")


def on_message(client, userdata, msg):
    try:
        # Topic is: sensors/bmp280/data -> sensor_key becomes 'bmp280'
        parts = msg.topic.split("/")
        if len(parts) < 2:
            return

        sensor_key = parts[1]
        data = json.loads(msg.payload.decode())

        destination_url = None
        with table_lock:
            # Fuzzy match: Look for a service that contains the sensor name
            for svc_name, url in service_table.items():
                if sensor_key in svc_name:
                    destination_url = url
                    break

        if destination_url:
            p = Point(sensor_key)
            # Use 'service_type' if available, otherwise fallback to sensor_key
            sensor_type = data.get("service_type", sensor_key)

            for k, v in data.items():
                if k not in ["service_type", "timestamp"]:
                    try:
                        p.field(k, float(v))
                    except:
                        p.field(k, str(v))

            write_to_influx(destination_url, p, sensor_type)
        else:
            print(f"No route found for sensor: {sensor_key}")

    except Exception as e:
        print(f"Processing Error: {e}")


# Startup
initialize_k8s()
refresh_routes()

mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="routing-engine")
mqtt_client.on_message = on_message
mqtt_client.connect("0.0.0.0", 1883)
mqtt_client.subscribe("sensors/#")
mqtt_client.loop_forever()
