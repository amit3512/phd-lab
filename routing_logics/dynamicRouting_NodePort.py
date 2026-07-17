import json
import paho.mqtt.client as mqtt
from kubernetes import client, config
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS
import threading

# Global Configuration
KUBECONFIG_PATH = "/home/pi/.kube/config"
INFLUX_TOKEN = "my-super-secret-admin-token"
INFLUX_ORG = "iot_edge_research"

# Thread safety
table_lock = threading.Lock()
service_table = {}
node_ip_cache = {}
service_port_map = {}
_influx_client_cache = {}


def initialize_k8s():
    """Safely initialize K8s config and API client."""
    try:
        # Load from file, then explicitly set host if needed
        config.load_kube_config(config_file=KUBECONFIG_PATH)
        # Force the host if not found in file
        configuration = client.Configuration.get_default_copy()
        if not configuration.host:
            configuration.host = "https://127.0.0.1:6443"
            client.Configuration.set_default(configuration)
    except Exception as e:
        print(f"K8s Init Error: {e}")


def load_node_cache():
    v1 = client.CoreV1Api()
    nodes = v1.list_node()
    for node in nodes.items:
        node_name = node.metadata.name
        for addr in node.status.addresses:
            if addr.type == "InternalIP":
                node_ip_cache[node_name] = addr.address
                break
    print(f"Node Cache Initialized: {len(node_ip_cache)} nodes found.")


def load_service_port_map():
    v1 = client.CoreV1Api()
    services = v1.list_service_for_all_namespaces()
    for svc in services.items:
        key = f"{svc.metadata.namespace}/{svc.metadata.name}"
        for port_def in svc.spec.ports or []:
            if port_def.node_port:
                service_port_map[key] = port_def.node_port


def discover_services():
    load_service_port_map()
    discovery_api = client.DiscoveryV1Api()
    slices = discovery_api.list_endpoint_slice_for_all_namespaces()

    with table_lock:
        for slice_item in slices.items:
            ns = slice_item.metadata.namespace
            svc_name = slice_item.metadata.labels.get("kubernetes.io/service-name")
            if not svc_name:
                continue

            key = f"{ns}/{svc_name}"
            port = service_port_map.get(key)
            if not port:
                continue

            for ep in slice_item.endpoints or []:
                if ep.conditions and ep.conditions.ready:
                    node_ip = node_ip_cache.get(ep.node_name)
                    if node_ip:
                        service_table[svc_name] = f"http://{node_ip}:{port}"
                        print(f"Mapped {svc_name} -> {service_table[svc_name]}")


def write_to_k8s_influx(url, point, sensor_type):
    print("write_to_k8s_influx")
    if url not in _influx_client_cache:
        client_obj = InfluxDBClient(url=url, token=INFLUX_TOKEN, org=INFLUX_ORG)
        _influx_client_cache[url] = client_obj.write_api(write_options=SYNCHRONOUS)

    try:
        write_api = _influx_client_cache[url]
        write_api.write(bucket=f"bucket_{sensor_type}", org=INFLUX_ORG, record=point)
        print(f"Successfully sent {sensor_type} to {url}")
    except Exception as e:
        print(f"Write error to {url}: {e}")


def build_point_from_data(sensor_type, data):
    p = Point(sensor_type)
    for key, value in data.items():
        if key not in ["service_type", "timestamp"]:
            try:
                p.field(key, float(value))
            except:
                p.field(key, str(value))
    return p


def on_message(client, userdata, msg):
    try:
        topic_parts = msg.topic.split("/")
        sensor_key = topic_parts[1]
        data = json.loads(msg.payload.decode())
        # sensor_type = data.get("service_type")

        with table_lock:
            destination_url = service_table.get(sensor_key)

        if destination_url:
            point = build_point_from_data(sensor_key, data)
            write_to_k8s_influx(destination_url, point, sensor_key)
            print(sensor_key)
            print(data)

        else:
            print(f"No route for {sensor_key}")
    except Exception as e:
        print(f"Processing Error: {e}")


# Startup Execution
initialize_k8s()
load_node_cache()
discover_services()

mqtt_client = mqtt.Client(
    mqtt.CallbackAPIVersion.VERSION2, client_id="pi2-routing-engine"
)
mqtt_client.on_message = on_message
mqtt_client.connect("0.0.0.0", 1883)
mqtt_client.subscribe("sensors/#")
mqtt_client.loop_forever()
