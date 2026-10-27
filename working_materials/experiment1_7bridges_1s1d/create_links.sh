#!/usr/bin/env bash
set -euo pipefail

create_veth() {
    local a="$1" b="$2"

    if ip link show "$a" >/dev/null 2>&1 ||
       ip link show "$b" >/dev/null 2>&1; then
        if ip link show "$a" >/dev/null 2>&1 &&
           ip link show "$b" >/dev/null 2>&1; then
            echo "Already exists: $a <-> $b"
            return
        fi
        echo "ERROR: only one endpoint exists: $a / $b" >&2
        exit 1
    fi

    sudo ip link add "$a" type veth peer name "$b"
    sudo ip link set "$a" up
    sudo ip link set "$b" up
    echo "Created: $a <-> $b"
}

create_veth v12a v12b
create_veth v23a v23b
create_veth v34a v34b
create_veth v47a v47b
create_veth v56a v56b
create_veth v64a v64b
create_veth v25a v25b

echo
echo "Checking OVS port errors:"
sudo ovs-vsctl show
