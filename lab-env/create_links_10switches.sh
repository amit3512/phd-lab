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

# 1. Create original link veth pairs
create_veth v12a v12b
create_veth v23a v23b
create_veth v34a v34b
create_veth v47a v47b
create_veth v56a v56b
create_veth v64a v64b
create_veth v25a v25b

# 2. Create OVS switches (bridges s1 through s10 with netdev datapath & controller)
for br in s1 s2 s3 s4 s5 s6 s7 s8 s9 s10; do
    if ! run_ovs br-exists "$br"; then
        run_ovs add-br "$br" -- set Bridge "$br" datapath_type=netdev
        echo "Created OVS bridge: $br (netdev)"
    else
        run_ovs set Bridge "$br" datapath_type=netdev
        echo "OVS bridge already exists: $br (enforced netdev)"
    fi
    
    # Configure controller and OpenFlow 1.3 cleanly
    run_ovs del-controller "$br" 2>/dev/null || true
    run_ovs set-controller "$br" tcp:127.0.0.1:6653
    run_ovs set Bridge "$br" protocols=OpenFlow13
    
    # Bring bridge interface UP for netdev datapath forwarding
    sudo ip link set "$br" up
done

# 3. Create inter-switch links
create_veth v28a v28b    # s2 <-> s8
create_veth v38a v38b    # s3 <-> s8
create_veth v210a v210b  # s2 <-> s10
create_veth v410a v410b  # s4 <-> s10
create_veth v49a v49b    # s4 <-> s9
create_veth v69a v69b    # s6 <-> s9

# 4. Attach original inter-switch links to OVS bridges
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

# # 6. Create namespaces
# if ! sudo ip netns list | grep -q "sdnA"; then
#     sudo ip netns add sdnA
# fi
# if ! sudo ip netns list | grep -q "sdnB"; then
#     sudo ip netns add sdnB
# fi

# sudo ip netns exec sdnA ip link set lo up
# sudo ip netns exec sdnB ip link set lo up

# # --- Namespace sdnA Setup ---
# sudo ip netns exec sdnA ip link add br-sw type bridge 2>/dev/null || true
# sudo ip netns exec sdnA ip link set br-sw up
# sudo ip netns exec sdnA ip addr flush dev br-sw 2>/dev/null || true
# sudo ip netns exec sdnA ip addr add 192.168.100.1/24 dev br-sw || true

# # Setup hostA <-> s1
# if ! ip link show hostA >/dev/null 2>&1; then
#     sudo ip link add hostA type veth peer name hostA-sw
#     sudo ip link set hostA netns sdnA
#     sudo ip netns exec sdnA ip link set hostA master br-sw
#     sudo ip netns exec sdnA ip link set hostA up
# fi
# sudo ip link set hostA-sw up 2>/dev/null || true
# run_ovs -- --may-exist add-port s1 hostA-sw

# # Setup hostA8 <-> s8
# if ! ip link show hostA8 >/dev/null 2>&1; then
#     sudo ip link add hostA8 type veth peer name hostA8-sw
#     sudo ip link set hostA8 netns sdnA
#     sudo ip netns exec sdnA ip link set hostA8 master br-sw
#     sudo ip netns exec sdnA ip link set hostA8 up
# fi
# sudo ip link set hostA8-sw up 2>/dev/null || true
# run_ovs -- --may-exist add-port s8 hostA8-sw


# # --- Namespace sdnB Setup ---
# sudo ip netns exec sdnB ip link add br-sw type bridge 2>/dev/null || true
# sudo ip netns exec sdnB ip link set br-sw up
# sudo ip netns exec sdnB ip addr flush dev br-sw 2>/dev/null || true
# sudo ip netns exec sdnB ip addr add 192.168.100.2/24 dev br-sw || true

# # Setup hostB <-> s7
# if ! ip link show hostB >/dev/null 2>&1; then
#     sudo ip link add hostB type veth peer name hostB-sw
#     sudo ip link set hostB netns sdnB
#     sudo ip netns exec sdnB ip link set hostB master br-sw
#     sudo ip netns exec sdnB ip link set hostB up
# fi
# sudo ip link set hostB-sw up 2>/dev/null || true
# run_ovs -- --may-exist add-port s7 hostB-sw

# # Setup hostB9 <-> s9
# if ! ip link show hostB9 >/dev/null 2>&1; then
#     sudo ip link add hostB9 type veth peer name hostB9-sw
#     sudo ip link set hostB9 netns sdnB
#     sudo ip netns exec sdnB ip link set hostB9 master br-sw
#     sudo ip netns exec sdnB ip link set hostB9 up
# fi
# sudo ip link set hostB9-sw up 2>/dev/null || true
# run_ovs -- --may-exist add-port s9 hostB9-sw

echo
echo "Setup complete! Checking OVS status and bridge connectivity:"
run_ovs show