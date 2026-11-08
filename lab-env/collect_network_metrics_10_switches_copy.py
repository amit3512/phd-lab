#!/usr/bin/env python3

import csv
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

INTERVAL = 5
OUTPUT = Path("network_traffic_metrics_10switches.csv")

PORTS = {
    "s1": ["v12a", "hostA-sw"],
    "s2": ["v12b", "v23a", "v210a", "v28a"],
    "s3": ["v34a", "v23b", "v38a"],
    "s4": ["v64b", "v34b", "v410a", "v47a", "v49a"],
    "s5": ["v25b", "v56a"],
    "s6": ["v64a", "v69a", "v56b"],
    "s7": ["hostB-sw", "v47b"],
    "s8": ["hostA8-sw", "v38b", "v28b"],
    "s9": ["hostB9-sw", "v69b", "v49b"],
    "s10": ["v210b", "v410b"],
}

TOTAL_EXPECTED_PORTS = sum(len(ports) for ports in PORTS.values())

FIELDS = [
    "timestamp_utc", "switch", "port",
    "rx_packets", "tx_packets", "rx_bytes", "tx_bytes",
    "rx_drops", "tx_drops", "rx_errors", "tx_errors",
    "rx_mbps", "tx_mbps", "rx_pps", "tx_pps",
]

PORT_BLOCK = re.compile(
    r"port\s+(?P<port_id>LOCAL|[\w-]+):\s+"
    r"rx pkts=(?P<rx_packets>\d+),\s+bytes=(?P<rx_bytes>\d+),\s+"
    r"drop=(?P<rx_drops>\d+),\s+errs=(?P<rx_errors>\d+)"
    r".*?\n\s+tx pkts=(?P<tx_packets>\d+),\s+"
    r"bytes=(?P<tx_bytes>\d+),\s+drop=(?P<tx_drops>\d+),\s+"
    r"errs=(?P<tx_errors>\d+)",
    re.DOTALL,
)

PORT_NAME = re.compile(r"^\s*(\d+)\(([^)]+)\):", re.MULTILINE)


def run_command(args):
    # Runs directly since the script itself should be executed with sudo
    return subprocess.run(
        args, capture_output=True, text=True, timeout=10, check=True
    ).stdout


def read_switch(switch):
    description = run_command([
        "ovs-ofctl", "-O", "OpenFlow13", "show", switch
    ])
    names = {
        match.group(1): match.group(2)
        for match in PORT_NAME.finditer(description)
    }

    stats = run_command([
        "ovs-ofctl", "-O", "OpenFlow13", "dump-ports", switch
    ])

    result = {}
    for match in PORT_BLOCK.finditer(stats):
        port_id = match.group("port_id")
        if port_id == "LOCAL":
            continue

        name = names.get(port_id)
        if name is None:
            continue

        result[name] = {
            key: int(match.group(key))
            for key in FIELDS[3:11]
        }

    return result


def main():
    previous = {}
    previous_time = None
    new_file = not OUTPUT.exists() or OUTPUT.stat().st_size == 0

    print(f"CSV Output: {OUTPUT.resolve()}")
    print(f"Sampling every {INTERVAL} seconds. Press Ctrl+C to stop.")

    with OUTPUT.open("a", newline="", buffering=1) as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        if new_file:
            writer.writeheader()

        while True:
            cycle_start = time.monotonic()
            sample_time = time.monotonic()
            timestamp = datetime.now(timezone.utc).isoformat()
            current = {}
            count = 0

            for switch, expected_ports in PORTS.items():
                try:
                    counters = read_switch(switch)
                except Exception as exc:
                    print(f"WARNING {switch}: {exc}")
                    continue

                for port in expected_ports:
                    if port not in counters:
                        print(f"WARNING: Port '{port}' on switch '{switch}' not found in OVS dump")
                        continue

                    values = counters[port]
                    key = (switch, port)
                    
                    row = {
                        "timestamp_utc": timestamp,
                        "switch": switch,
                        "port": port,
                        **values,
                        "rx_mbps": 0.0,
                        "tx_mbps": 0.0,
                        "rx_pps": 0.0,
                        "tx_pps": 0.0,
                    }

                    old = previous.get(key)
                    if old is not None and previous_time is not None:
                        elapsed = sample_time - previous_time
                        if elapsed > 0:
                            rx_bytes_delta = values["rx_bytes"] - old["rx_bytes"]
                            tx_bytes_delta = values["tx_bytes"] - old["tx_bytes"]
                            rx_pkts_delta = values["rx_packets"] - old["rx_packets"]
                            tx_pkts_delta = values["tx_packets"] - old["tx_packets"]

                            if (rx_bytes_delta >= 0 and tx_bytes_delta >= 0 and 
                                rx_pkts_delta >= 0 and tx_pkts_delta >= 0):
                                row["rx_mbps"] = round(rx_bytes_delta * 8 / elapsed / 1_000_000, 6)
                                row["tx_mbps"] = round(tx_bytes_delta * 8 / elapsed / 1_000_000, 6)
                                row["rx_pps"] = round(rx_pkts_delta / elapsed, 3)
                                row["tx_pps"] = round(tx_pkts_delta / elapsed, 3)

                    writer.writerow(row)
                    current[key] = values
                    count += 1

            previous = current
            previous_time = sample_time
            handle.flush()
            print(f"{timestamp}: successfully recorded {count}/{TOTAL_EXPECTED_PORTS} ports")

            time.sleep(max(0, INTERVAL - (time.monotonic() - cycle_start)))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nCollector stopped by user.")