import json
import threading
import time
import requests

import paho.mqtt.client as mqtt

from kubernetes import client, config

RYU_URL = "http://127.0.0.1:8080/routing"


MQTT_BROKER = "0.0.0.0"
MQTT_PORT = 1883
MQTT_TOPIC = "sensors/#"


service_table = {}

ingress_nodes = []

lock = threading.Lock()


# ------------------------------
# Kubernetes connection
# ------------------------------


def initialize_k8s():

    try:

        config.load_kube_config(config_file="/home/pi/.kube/config")

        print("Kubernetes connected")

    except Exception as e:

        print("Kubernetes error:", e)

        exit(1)


# ------------------------------
# Discover ingress controllers
# ------------------------------


def discover_ingress_nodes():

    v1 = client.CoreV1Api()

    while True:

        try:

            pods = v1.list_namespaced_pod(
                namespace="ingress-nginx",
                label_selector="app.kubernetes.io/component=controller",
            )

            discovered = []

            for pod in pods.items:

                if pod.status.phase != "Running":

                    continue

                node_name = pod.spec.node_name

                node = v1.read_node(node_name)

                node_ip = None

                for addr in node.status.addresses:

                    if addr.type == "InternalIP":

                        node_ip = addr.address

                if node_ip:

                    discovered.append({"node": node_name, "ip": node_ip})

            with lock:

                ingress_nodes.clear()

                ingress_nodes.extend(discovered)

            print("\nAvailable ingress nodes")

            for n in ingress_nodes:

                print(n["node"], n["ip"])

        except Exception as e:

            print("Ingress discovery error:", e)

        time.sleep(10)


# ------------------------------
# Discover ingress rules
# ------------------------------


def discover_services():

    api = client.NetworkingV1Api()

    while True:

        try:

            ingresses = api.list_ingress_for_all_namespaces()

            with lock:

                service_table.clear()

                for ing in ingresses.items:

                    if not ing.spec.rules:

                        continue

                    for rule in ing.spec.rules:

                        if not rule.http:

                            continue

                        for path in rule.http.paths:

                            backend = path.backend.service

                            if backend:

                                service_table[backend.name] = {"host": rule.host}

                                print("Service:", backend.name, "->", rule.host)

        except Exception as e:

            print("Service discovery error:", e)

        time.sleep(10)


# ------------------------------
# Select ingress node
# ------------------------------


def select_ingress():

    with lock:

        if len(ingress_nodes) == 0:

            return None

        selected = ingress_nodes.pop(0)

        ingress_nodes.append(selected)

        return selected["ip"]


# ------------------------------
# Find service
# ------------------------------


def get_service(sensor):

    with lock:

        for name, data in service_table.items():

            if sensor in name:

                return data

    return None


# ------------------------------
# Send only IP to Ryu
# ------------------------------


def send_to_ryu(sensor, destination_ip):

    payload = {"sensor": sensor, "destination_ip": destination_ip}

    print("Sending to Ryu:", payload)

    try:

        response = requests.post(RYU_URL, json=payload, timeout=5)

        print("Ryu:", response.text)

    except Exception as e:

        print("Ryu error:", e)


# ------------------------------
# MQTT
# ------------------------------


def on_message(client, userdata, msg):

    try:

        sensor = msg.topic.split("/")[1]

        data = json.loads(msg.payload.decode())

        print("\n==============")

        print("Sensor:", sensor)

        service = get_service(sensor)

        if service is None:

            print("No service found")

            return

        ingress_ip = select_ingress()

        if ingress_ip is None:

            print("No ingress available")

            return

        print("Ingress:", service["host"])

        print("Selected ingress:", ingress_ip)

        send_to_ryu(sensor, ingress_ip)

    except Exception as e:

        print("MQTT error:", e)


# ------------------------------
# MAIN
# ------------------------------

initialize_k8s()


threading.Thread(target=discover_ingress_nodes, daemon=True).start()


threading.Thread(target=discover_services, daemon=True).start()


mqtt_client = mqtt.Client()


mqtt_client.on_message = on_message


mqtt_client.connect(MQTT_BROKER, MQTT_PORT)


mqtt_client.subscribe(MQTT_TOPIC)


print("Dynamic SDN Ingress Engine Started")


mqtt_client.loop_forever()
