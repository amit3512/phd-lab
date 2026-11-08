#!/bin/bash
set -e

echo "==> Setting up network namespaces with STP-enabled Linux bridges..."

# ==========================================
# 1. Namespace sdnA Setup (Bridge + STP across s1 and s8)
# ==========================================
echo "==> Configuring namespace sdnA..."

# Cleanup previous attempts
sudo ip netns exec sdnA ip link set br-sw down 2>/dev/null || true
sudo ip netns exec sdnA ip link delete br-sw type bridge 2>/dev/null || true
sudo ip link delete hostA 2>/dev/null || true
sudo ip link delete hostA8 2>/dev/null || true
sudo ip link delete hostA-sw 2>/dev/null || true
sudo ip link delete hostA8-sw 2>/dev/null || true

# Create namespace
if ! sudo ip netns list | grep -q "sdnA"; then
    sudo ip netns add sdnA
fi
sudo ip netns exec sdnA ip link set lo up

# Create Linux bridge inside sdnA and enable STP to prevent loops
sudo ip netns exec sdnA ip link add br-sw type bridge
sudo ip netns exec sdnA ip link set br-sw up
sudo ip netns exec sdnA ip link set br-sw type bridge stp_state 1
sudo ip netns exec sdnA ip addr add 192.168.100.1/24 dev br-sw

# Create veth pairs for s1 and s8
sudo ip link add hostA type veth peer name hostA-sw
sudo ip link set hostA netns sdnA
sudo ip netns exec sdnA ip link set hostA master br-sw
sudo ip netns exec sdnA ip link set hostA up

sudo ip link add hostA8 type veth peer name hostA8-sw
sudo ip link set hostA8 netns sdnA
sudo ip netns exec sdnA ip link set hostA8 master br-sw
sudo ip netns exec sdnA ip link set hostA8 up

# Attach OVS ports
sudo ip link set hostA-sw up
sudo ip link set hostA8-sw up
sudo ovs-vsctl -- --may-exist add-port s1 hostA-sw
sudo ovs-vsctl -- --may-exist add-port s8 hostA8-sw


# ==========================================
# 2. Namespace sdnB Setup (Bridge + STP across s7 and s9)
# ==========================================
echo "==> Configuring namespace sdnB..."

# Cleanup previous attempts
sudo ip netns exec sdnB ip link set br-sw down 2>/dev/null || true
sudo ip netns exec sdnB ip link delete br-sw type bridge 2>/dev/null || true
sudo ip link delete hostB 2>/dev/null || true
sudo ip link delete hostB9 2>/dev/null || true
sudo ip link delete hostB-sw 2>/dev/null || true
sudo ip link delete hostB9-sw 2>/dev/null || true

# Create namespace
if ! sudo ip netns list | grep -q "sdnB"; then
    sudo ip netns add sdnB
fi
sudo ip netns exec sdnB ip link set lo up

# Create Linux bridge inside sdnB and enable STP
sudo ip netns exec sdnB ip link add br-sw type bridge
sudo ip netns exec sdnB ip link set br-sw up
sudo ip netns exec sdnB ip link set br-sw type bridge stp_state 1
sudo ip netns exec sdnB ip addr add 192.168.100.2/24 dev br-sw

# Create veth pairs for s7 and s9
sudo ip link add hostB type veth peer name hostB-sw
sudo ip link set hostB netns sdnB
sudo ip netns exec sdnB ip link set hostB master br-sw
sudo ip netns exec sdnB ip link set hostB up

sudo ip link add hostB9 type veth peer name hostB9-sw
sudo ip link set hostB9 netns sdnB
sudo ip netns exec sdnB ip link set hostB9 master br-sw
sudo ip netns exec sdnB ip link set hostB9 up

# Attach OVS ports
sudo ip link set hostB-sw up
sudo ip link set hostB9-sw up
sudo ovs-vsctl -- --may-exist add-port s7 hostB-sw
sudo ovs-vsctl -- --may-exist add-port s9 hostB9-sw

echo "==> STP-enabled bridge topology successfully deployed!"