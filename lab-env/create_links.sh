# #!/usr/bin/env bash
# set -euo pipefail

# create_veth() {
#     local a="$1" b="$2"

#     if ip link show "$a" >/dev/null 2>&1 ||
#        ip link show "$b" >/dev/null 2>&1; then
#         if ip link show "$a" >/dev/null 2>&1 &&
#            ip link show "$b" >/dev/null 2>&1; then
#             echo "Already exists: $a <-> $b"
#             return
#         fi
#         echo "ERROR: only one endpoint exists: $a / $b" >&2
#         exit 1
#     fi

#     sudo ip link add "$a" type veth peer name "$b"
#     sudo ip link set "$a" up
#     sudo ip link set "$b" up
#     echo "Created: $a <-> $b"
# }

# create_veth v12a v12b
# create_veth v23a v23b
# create_veth v34a v34b
# create_veth v47a v47b
# create_veth v56a v56b
# create_veth v64a v64b
# create_veth v25a v25b

# echo
# echo "Checking OVS port errors:"
# sudo ovs-vsctl show

#!/bin/bash
set -e

LINKS=(
    "v12a br-ryu-test v12b s2"
    "v23a s2 v23b s3"
    "v34a s3 v34b s4"
    "v47a s4 v47b s7"
    "v56a s5 v56b s6"
    "v64a s6 v64b s4"
    "v25a s2 v25b s5"
)

echo "Setting up OVS bridges and veth links..."

for link in "${LINKS[@]}"; do
    read -r vA brA vB brB <<< "$link"

    # Delete existing veth endpoints if they linger
    sudo ip link delete "$vA" 2>/dev/null || true

    # Create fresh veth pair
    sudo ip link add "$vA" type veth peer name "$vB"
    
    # Bring both interfaces up
    sudo ip link set dev "$vA" up
    sudo ip link set dev "$vB" up

    # Attach to OVS bridges safely
    sudo ovs-vsctl -- --if-exists del-port "$vA" -- add-port "$brA" "$vA"
    sudo ovs-vsctl -- --if-exists del-port "$vB" -- add-port "$brB" "$vB"
    
    echo "Configured: $vA <-> $vB attached to $brA <-> $brB"
done

echo "OVS port mapping verification:"
sudo ovs-vsctl show
