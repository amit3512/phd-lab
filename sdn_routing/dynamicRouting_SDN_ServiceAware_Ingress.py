import json
import threading
import requests
import paho.mqtt.client as mqtt

from kubernetes import client, config

RYU_URL = "http://127.0.0.1:8080/routing"

MQTT_BROKER = "0.0.0.0"
MQTT_TOPIC = "sensors/#"


service_table = {}
lock = threading.Lock()


# Kubernetes node where ingress controller runs
INGRESS_NODE_IP = "172.22.251.30"


def initialize_k8s():

    try:
        config.load_kube_config(config_file="/home/pi/.kube/config")

        print("K8s config loaded")

    except Exception as e:
        print(e)


def discover_ingress_routes():

    v1 = client.CoreV1Api()
    net = client.NetworkingV1Api()

    services = v1.list_service_for_all_namespaces()

    with lock:

        service_table.clear()

        for svc in services.items:

            name = svc.metadata.name

            ingresses = net.list_namespaced_ingress(svc.metadata.namespace)

            for ing in ingresses.items:

                if not ing.spec.rules:
                    continue

                for rule in ing.spec.rules:

                    if rule.http:

                        for path in rule.http.paths:

                            backend = path.backend.service

                            if backend and backend.name == name:

                                host = rule.host

                                service_table[name] = {
                                    "host": host,
                                    "ip": INGRESS_NODE_IP,
                                    "port": 80,
                                }

                                print(f"Mapped {name} -> {host}")


def get_service(sensor):

    with lock:

        for name, data in service_table.items():

            if sensor in name:

                return data

    return None


def send_to_ryu(sensor, destination_ip):

    payload = {
        "sensor": sensor,
        "destination_ip": destination_ip,
        "output_port": "veth1" if sensor == "bmp280" else "veth0",
    }

    print("Sending:", payload)

    try:

        r = requests.post(RYU_URL, json=payload)

        print("Ryu:", r.text)

    except Exception as e:

        print("Ryu error:", e)


def on_message(client, userdata, msg):

    try:

        topic = msg.topic

        sensor = topic.split("/")[1]

        data = json.loads(msg.payload.decode())

        print("\n================")

        print("Sensor:", sensor)

        route = get_service(sensor)

        if not route:

            print("No ingress route")

            return

        print("Ingress:", route["host"])

        print("Destination node:", route["ip"])

        send_to_ryu(sensor, route["ip"])

    except Exception as e:

        print("ERROR:", e)


initialize_k8s()

discover_ingress_routes()


mqtt_client = mqtt.Client()

mqtt_client.on_message = on_message


mqtt_client.connect(MQTT_BROKER, 1883)


mqtt_client.subscribe(MQTT_TOPIC)


print("SDN Ingress Routing Engine Started")


mqtt_client.loop_forever()
