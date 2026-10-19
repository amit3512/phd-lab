#!/usr/bin/env python3

import csv
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

INTERVAL = 5
OUTPUT = Path("network_traffic_metrics_oct19.csv")

# Full-fabric topology: Includes br-ryu-test (ingress) + switches s2 through s7
PORTS = {
    "br-ryu-test": ["v12a", "hostA-sw"],
    "s2": ["v23a", "v25a"],
    "s3": ["v23b", "v34a"],
    "s4": ["v34b", "v47a", "v64b"],
    "s5": ["v25b", "v56a"],
    "s6": ["v56b", "v64a"],
    "s7": ["v47b"],
}

FIELDS = [
    "timestamp_utc", "switch", "port",
    "rx_packets", "tx_packets", "rx_bytes", "tx_bytes",
    "rx_drops", "tx_drops", "rx_errors", "tx_errors",
    "rx_mbps", "tx_mbps", "rx_pps", "tx_pps",
]

PORT_BLOCK = re.compile(
    r"port\s+(?P<port_id>LOCAL|\d+):\s+"
    r"rx pkts=(?P<rx_packets>\d+), bytes=(?P<rx_bytes>\d+), "
    r"drop=(?P<rx_drops>\d+), errs=(?P<rx_errors>\d+)"
    r".*?\n\s+tx pkts=(?P<tx_packets>\d+), "
    r"bytes=(?P<tx_bytes>\d+), drop=(?P<tx_drops>\d+), "
    r"errs=(?P<tx_errors>\d+)",
    re.DOTALL,
)

PORT_NAME = re.compile(r"^\s*(\d+)\(([^)]+)\):", re.MULTILINE)


def run_command(args):
    return subprocess.run(
        ["sudo", "-n", *args],
        capture_output=True, text=True, timeout=10, check=True
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

    print(f"CSV: {OUTPUT.resolve()}")
    print(f"Sampling every {INTERVAL} seconds; Ctrl+C to stop.")

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
                        print(f"WARNING: {switch}/{port} not found")
                        continue

                    values = counters[port]
                    key = (switch, port)
                    row = {
                        "timestamp_utc": timestamp,
                        "switch": switch,
                        "port": port,
                        **values,
                        "rx_mbps": "",
                        "tx_mbps": "",
                        "rx_pps": "",
                        "tx_pps": "",
                    }

                    old = previous.get(key)
                    if old is not None and previous_time is not None:
                        elapsed = sample_time - previous_time
                        deltas = {
                            field: values[field] - old[field]
                            for field in values
                        }
                        if elapsed > 0 and all(v >= 0 for v in deltas.values()):
                            row["rx_mbps"] = round(
                                deltas["rx_bytes"] * 8 / elapsed / 1_000_000, 6
                            )
                            row["tx_mbps"] = round(
                                deltas["tx_bytes"] * 8 / elapsed / 1_000_000, 6
                            )
                            row["rx_pps"] = round(
                                deltas["rx_packets"] / elapsed, 3
                            )
                            row["tx_pps"] = round(
                                deltas["tx_packets"] / elapsed, 3
                            )

                    writer.writerow(row)
                    current[key] = values
                    count += 1

            previous = current
            previous_time = sample_time
            handle.flush()
            # Total expected ports across 7 switches is now 14
            print(f"{timestamp}: recorded {count}/14 ports")

            time.sleep(max(0, INTERVAL - (time.monotonic() - cycle_start)))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nCollector stopped.")