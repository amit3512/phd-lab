#!/usr/bin/env bash
set -euo pipefail

run_ovs() {
    sudo ovs-vsctl --timeout=5 "$@"
}

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

# 1. Create original link veth pairs[cite: 1]
create_veth v12a v12b
create_veth v23a v23b
create_veth v34a v34b
create_veth v47a v47b
create_veth v56a v56b
create_veth v64a v64b
create_veth v25a v25b

# 2. Create OVS switches (bridges s1 through s10)
for br in s1 s2 s3 s4 s5 s6 s7 s8 s9 s10; do
    if ! run_ovs br-exists "$br"; then
        run_ovs add-br "$br"
        echo "Created OVS bridge: $br"
    else
        echo "OVS bridge already exists: $br"
    fi
done

# 3. Create inter-switch links
create_veth v28a v28b    # s2 <-> s8
create_veth v38a v38b    # s3 <-> s8
create_veth v210a v210b  # s2 <-> s10
create_veth v410a v410b  # s4 <-> s10
create_veth v49a v49b    # s4 <-> s9
create_veth v69a v69b    # s6 <-> s9

# 4. Attach original inter-switch links to OVS bridges[cite: 1]
run_ovs -- --may-exist add-port s1 v12a \
        -- --may-exist add-port s2 v12b
run_ovs -- --may-exist add-port s2 v23a \
        -- --may-exist add-port s3 v23b
run_ovs -- --may-exist add-port s3 v34a \
        -- --may-exist add-port s4 v34b
run_ovs -- --may-exist add-port s4 v47a \
        -- --may-exist add-port s7 v47b
run_ovs -- --may-exist add-port s5 v56a \
        -- --may-exist add-port s6 v56b
run_ovs -- --may-exist add-port s6 v64a \
        -- --may-exist add-port s4 v64b
run_ovs -- --may-exist add-port s2 v25a \
        -- --may-exist add-port s5 v25b

# 5. Attach new inter-switch links to OVS bridges
run_ovs -- --may-exist add-port s2 v28a \
        -- --may-exist add-port s8 v28b
run_ovs -- --may-exist add-port s3 v38a \
        -- --may-exist add-port s8 v38b

run_ovs -- --may-exist add-port s2 v210a \
        -- --may-exist add-port s10 v210b
run_ovs -- --may-exist add-port s4 v410a \
        -- --may-exist add-port s10 v410b

run_ovs -- --may-exist add-port s4 v49a \
        -- --may-exist add-port s9 v49b
run_ovs -- --may-exist add-port s6 v69a \
        -- --may-exist add-port s9 v69b


# 6. Create the sensor and edge namespaces
if ! sudo ip netns list | grep -q "sdnA"; then
    sudo ip netns add sdnA
fi
if ! sudo ip netns list | grep -q "sdnB"; then
    sudo ip netns add sdnB
fi

# --- Namespace sdnA Setup (Idempotent) ---
sudo ip netns exec sdnA ip link set lo up

# Setup hostA <-> s1
if ! ip link show hostA >/dev/null 2>&1; then
    sudo ip link add hostA type veth peer name hostA-sw
    sudo ip link set hostA netns sdnA
    sudo ip netns exec sdnA ip addr add 192.168.100.1/24 dev hostA || true
    sudo ip netns exec sdnA ip link set hostA up
fi
sudo ip link set hostA-sw up 2>/dev/null || true
run_ovs -- --may-exist add-port s1 hostA-sw

# Setup hostA8 <-> s8
if ! ip link show hostA8 >/dev/null 2>&1; then
    sudo ip link add hostA8 type veth peer name hostA8-sw
    sudo ip link set hostA8 netns sdnA
    sudo ip netns exec sdnA ip addr add 192.168.108.1/24 dev hostA8 || true
    sudo ip netns exec sdnA ip link set hostA8 up
fi
sudo ip link set hostA8-sw up 2>/dev/null || true
run_ovs -- --may-exist add-port s8 hostA8-sw


# --- Namespace sdnB Setup (Idempotent) ---
sudo ip netns exec sdnB ip link set lo up

# Setup hostB <-> s7 (IP: 192.168.100.2/24)
if ! ip link show hostB >/dev/null 2>&1; then
    sudo ip link add hostB type veth peer name hostB-sw
    sudo ip link set hostB netns sdnB
    sudo ip netns exec sdnB ip addr add 192.168.100.2/24 dev hostB || true
    sudo ip netns exec sdnB ip link set hostB up
fi
sudo ip link set hostB-sw up 2>/dev/null || true
run_ovs -- --may-exist add-port s7 hostB-sw

# Setup hostB9 <-> s9 (IP: 192.168.109.2/24)
if ! ip link show hostB9 >/dev/null 2>&1; then
    sudo ip link add hostB9 type veth peer name hostB9-sw
    sudo ip link set hostB9 netns sdnB
    sudo ip netns exec sdnB ip addr add 192.168.109.2/24 dev hostB9 || true
    sudo ip netns exec sdnB ip link set hostB9 up
fi
sudo ip link set hostB9-sw up 2>/dev/null || true
run_ovs -- --may-exist add-port s9 hostB9-sw

echo
echo "Setup complete! Checking OVS port status:"
run_ovs show