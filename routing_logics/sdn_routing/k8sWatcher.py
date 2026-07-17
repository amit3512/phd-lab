import json
import time
import threading

from kubernetes import client, config, watch

CACHE_FILE = "/home/pi/ingress_cache.json"


lock = threading.Lock()


ingress_cache = {}


def save_cache():

    with lock:

        with open(CACHE_FILE, "w") as f:

            json.dump(ingress_cache, f, indent=4)


def load_k8s():

    try:

        config.load_kube_config(config_file="/home/pi/.kube/config")

        print("Kubernetes config loaded")

    except Exception as e:

        print("Kubernetes config error:", e)

        exit(1)


def discover_existing_ingress():
    """
    Initial discovery only once
    """

    networking = client.NetworkingV1Api()
    core = client.CoreV1Api()

    ingresses = networking.list_ingress_for_all_namespaces()

    pods = core.list_pod_for_all_namespaces(
        label_selector="app.kubernetes.io/component=controller"
    )

    ingress_ips = []

    for pod in pods.items:

        if pod.status.pod_ip:

            node = pod.spec.node_name

            node_obj = core.read_node(node)

            for addr in node_obj.status.addresses:

                if addr.type == "InternalIP":

                    ingress_ips.append({"node": node, "ip": addr.address})

    with lock:

        for ing in ingresses.items:

            for rule in ing.spec.rules:

                if not rule.http:

                    continue

                for path in rule.http.paths:

                    service = path.backend.service.name

                    sensor = service.replace("-service", "")

                    ingress_cache[sensor] = ingress_ips

    save_cache()

    print("Initial ingress discovery completed")


def watch_nodes():
    """
    Watch only node changes
    """

    w = watch.Watch()

    core = client.CoreV1Api()

    for event in w.stream(core.list_node):

        obj = event["object"]

        event_type = event["type"]

        print("Node event:", event_type, obj.metadata.name)

        discover_existing_ingress()


def watch_ingress():
    """
    Watch ingress changes
    """

    w = watch.Watch()

    networking = client.NetworkingV1Api()

    for event in w.stream(networking.list_ingress_for_all_namespaces):

        print("Ingress event:", event["type"])

        discover_existing_ingress()


if __name__ == "__main__":

    load_k8s()

    discover_existing_ingress()

    t1 = threading.Thread(target=watch_nodes)

    t2 = threading.Thread(target=watch_ingress)

    t1.start()

    t2.start()

    print("Kubernetes watcher running")

    while True:

        time.sleep(60)
