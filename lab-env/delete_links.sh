#!/usr/bin/env bash
set -euo pipefail

echo "1. Deleting network namespaces (sdnA, sdnB)..."
sudo ip netns del sdnA 2>/dev/null || true
sudo ip netns del sdnB 2>/dev/null || true

echo "2. Deleting standalone veth interfaces..."
for veth in hostA hostA-sw hostB hostB-sw hostA8 hostA8-sw hostB9 hostB9-sw; do
    sudo ip link del "$veth" 2>/dev/null || true
done

echo "3. Deleting OVS bridges..."
for br in br-ryu-test s1 s2 s3 s4 s5 s6 s7 s8 s9 s10; do
    if sudo timeout 3 ovs-vsctl br-exists "$br" 2>/dev/null; then
        sudo timeout 3 ovs-vsctl del-br "$br" || true
        echo "Deleted bridge: $br"
    fi
done

echo "4. Cleaning up remaining veth links..."
for link in $(ip -o link show type veth 2>/dev/null | awk -F': ' '{print $2}'); do
    sudo ip link del "$link" 2>/dev/null || true
done

for veth in v12a v12b v23a v23b v34a v34b v47a v47b v56a v56b v64a v64b v25a v25b \
            v28a v28b v38a v38b v210a v210b v410a v410b v49a v49b v69a v69b \
            hostA hostA-sw hostB hostB-sw hostA8 hostA8-sw hostB9 hostB9-sw; do
    sudo ip link del "$veth" 2>/dev/null || true
done

echo "Cleanup complete!"

# for veth in v12a v12b v23a v23b v34a v34b v47a v47b v56a v56b v64a v64b v25a v25b \
#             v28a v28b v38a v38b v210a v210b v410a v410b v49a v49b v69a v69b \
#             hostA hostA-sw hostB hostB-sw hostA8 hostA8-sw hostB9 hostB9-sw; do
#     sudo ip link del "$veth" 2>/dev/null || true
# done