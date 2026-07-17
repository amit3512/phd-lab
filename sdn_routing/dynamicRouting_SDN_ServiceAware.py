import json
import threading
import requests

import paho.mqtt.client as mqtt

from kubernetes import client, config

from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS

# ================= CONFIGURATION =================

INFLUX_TOKEN = "my-super-secret-admin-token"
INFLUX_ORG = "iot_edge_research"


RYU_CONTROLLER_URL = "http://127.0.0.1:8080/routing"


service_table = {}

table_lock = threading.Lock()

_influx_client_cache = {}


# ================= KUBERNETES =================


def initialize_k8s():

    try:

        config.load_kube_config(config_file="/home/pi/.kube/config")

        print("K8s: Local kubeconfig loaded.")

    except Exception as e:

        print(f"K8s Init Error: {e}")


def get_service_route(svc, net_v1):

    svc_name = svc.metadata.name

    namespace = svc.metadata.namespace

    INGRESS_NODE_PORT = 32213

    try:

        ingresses = net_v1.list_namespaced_ingress(namespace)

        for ing in ingresses.items:

            if ing.spec.rules:

                for rule in ing.spec.rules:

                    if rule.http and rule.http.paths:

                        for path in rule.http.paths:

                            if (
                                path.backend.service
                                and path.backend.service.name == svc_name
                            ):

                                print(
                                    f"DEBUG: Found route " f"{svc_name} -> {rule.host}"
                                )

                                return f"http://{rule.host}:" f"{INGRESS_NODE_PORT}"

    except Exception as e:

        print(f"Ingress error: {e}")

    return None


def refresh_routes():

    v1 = client.CoreV1Api()

    net_v1 = client.NetworkingV1Api()

    services = v1.list_service_for_all_namespaces()

    with table_lock:

        service_table.clear()

        for svc in services.items:

            url = get_service_route(svc, net_v1)

            if url:

                service_table[svc.metadata.name] = url

                print(f"Mapped: {svc.metadata.name}" f" -> {url}")


# ================= SDN ROUTING =================


def get_output_port(sensor):
    """
    Decide OVS output port
    """

    if sensor == "bmp280":

        return "veth1"

    elif sensor == "pa1010d":

        return "veth0"

    else:

        return "veth1"


def send_to_ryu(sensor, destination):

    output_port = get_output_port(sensor)

    payload = {"sensor": sensor, "destination": destination, "output_port": output_port}

    try:

        response = requests.post(RYU_CONTROLLER_URL, json=payload, timeout=3)

        print("Ryu response:", response.text)

    except Exception as e:

        print(f"Ryu error: {e}")


# ================= INFLUX =================


def write_to_influx(url, point, sensor_type):

    if url not in _influx_client_cache:

        client_obj = InfluxDBClient(url=url, token=INFLUX_TOKEN, org=INFLUX_ORG)

        _influx_client_cache[url] = client_obj.write_api(write_options=SYNCHRONOUS)

    try:

        _influx_client_cache[url].write(
            bucket=f"bucket_{sensor_type}", org=INFLUX_ORG, record=point
        )

        print(f"Sent {sensor_type} data to {url}")

    except Exception as e:

        print(f"Influx error: {e}")


# ================= MQTT =================


def on_message(client, userdata, msg):

    try:

        parts = msg.topic.split("/")

        if len(parts) < 2:

            return

        sensor_key = parts[1]

        data = json.loads(msg.payload.decode())

        destination_url = None

        with table_lock:

            for svc_name, url in service_table.items():

                if sensor_key in svc_name:

                    destination_url = url

                    break

        if destination_url:

            print("\n======================")

            print("Sensor:", sensor_key)

            print("Destination:", destination_url)

            # Send decision to Ryu

            send_to_ryu(sensor_key, destination_url)

            point = Point(sensor_key)

            sensor_type = data.get("service_type", sensor_key)

            for k, v in data.items():

                if k not in ["service_type", "timestamp"]:

                    try:

                        point.field(k, float(v))

                    except:

                        point.field(k, str(v))

            write_to_influx(destination_url, point, sensor_type)

        else:

            print("No route:", sensor_key)

    except Exception as e:

        print(f"Processing Error: {e}")


# ================= START =================


initialize_k8s()


refresh_routes()


mqtt_client = mqtt.Client(
    mqtt.CallbackAPIVersion.VERSION2, client_id="service-aware-routing"
)


mqtt_client.on_message = on_message


mqtt_client.connect("localhost", 1883)


mqtt_client.subscribe("sensors/#")


print("Service Aware SDN Routing Started...")


mqtt_client.loop_forever()
