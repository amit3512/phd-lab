import json
import threading
import requests

import paho.mqtt.client as mqtt

from kubernetes import client, config

# -------------------------
# Configuration
# -------------------------

RYU_URL = "http://127.0.0.1:8080/routing"

KUBECONFIG = "/home/pi/.kube/config"

MQTT_BROKER = "0.0.0.0"
MQTT_PORT = 1883

INGRESS_NODE_PORT = 32213


# -------------------------
# Global tables
# -------------------------

service_table = {}
service_ip_table = {}
service_port_table = {}

table_lock = threading.Lock()


# -------------------------
# Kubernetes
# -------------------------


def initialize_k8s():

    try:
        config.load_kube_config(config_file=KUBECONFIG)

        print("K8s: Local kubeconfig loaded.")

    except Exception as e:
        print("K8s error:", e)


def get_service_route(svc, net_v1):

    svc_name = svc.metadata.name
    namespace = svc.metadata.namespace

    try:

        ingresses = net_v1.list_namespaced_ingress(namespace)

        for ing in ingresses.items:

            if ing.spec.rules:

                for rule in ing.spec.rules:

                    if rule.http:

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

        print("Ingress error:", e)

    return None


def get_endpoint_ip(svc_name, namespace, v1):

    try:

        ep = v1.read_namespaced_endpoints(svc_name, namespace)

        if ep.subsets:

            for subset in ep.subsets:

                if subset.addresses:

                    return subset.addresses[0].ip

    except Exception as e:

        print("Endpoint error:", e)

    return None


def refresh_routes():

    v1 = client.CoreV1Api()

    net_v1 = client.NetworkingV1Api()

    services = v1.list_service_for_all_namespaces()

    with table_lock:

        service_table.clear()
        service_ip_table.clear()
        service_port_table.clear()

        for svc in services.items:

            name = svc.metadata.name

            url = get_service_route(svc, net_v1)

            if url:

                ip = get_endpoint_ip(name, svc.metadata.namespace, v1)

                if ip is None:
                    continue

                service_table[name] = url

                service_ip_table[name] = ip

                if "bmp280" in name:

                    service_port_table[name] = "veth1"

                elif "pa1010d" in name:

                    service_port_table[name] = "veth0"

                print("\nMapped Service:")

                print("Name:", name)

                print("URL:", url)

                print("IP:", ip)

                print("PORT:", service_port_table.get(name))


# -------------------------
# Ryu REST communication
# -------------------------


def send_to_ryu(sensor, destination, destination_ip, output_port):

    payload = {
        "sensor": sensor,
        "destination": destination,
        "destination_ip": destination_ip,
        "output_port": output_port,
    }

    print("Sending to Ryu:", payload)

    try:

        response = requests.post(RYU_URL, json=payload, timeout=3)

        print("Ryu response:", response.text)

    except Exception as e:

        print("Ryu error:", e)


# -------------------------
# MQTT
# -------------------------


def on_message(client, userdata, msg):

    try:

        parts = msg.topic.split("/")

        if len(parts) < 3:
            return

        sensor = parts[1]

        data = json.loads(msg.payload.decode())

        print("\n======================")

        print("Sensor:", sensor)

        service_name = None

        with table_lock:

            for svc in service_table:

                if sensor in svc:

                    service_name = svc

                    break

        if service_name is None:

            print("No service found")

            return

        destination = service_table[service_name]

        destination_ip = service_ip_table[service_name]

        output_port = service_port_table[service_name]

        print("Destination:", destination)

        print("Destination IP:", destination_ip)

        print("Output:", output_port)

        send_to_ryu(sensor, destination, destination_ip, output_port)

    except Exception as e:

        print("MQTT error:", e)


# -------------------------
# Start
# -------------------------

initialize_k8s()

refresh_routes()


mqtt_client = mqtt.Client(
    mqtt.CallbackAPIVersion.VERSION2, client_id="service-aware-ip-routing"
)


mqtt_client.on_message = on_message


mqtt_client.connect(MQTT_BROKER, MQTT_PORT)


mqtt_client.subscribe("sensors/#")


print("Service Aware IP SDN Routing Started...")


mqtt_client.loop_forever()
