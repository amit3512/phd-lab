#!/bin/bash
set -e

echo "==> Cleaning up old interfaces and namespaces..."
sudo ip netns exec sdnA ip link set br-sw down 2>/dev/null || true
sudo ip netns exec sdnA ip link delete br-sw type bridge 2>/dev/null || true
sudo ip netns exec sdnB ip link set br-sw down 2>/dev/null || true
sudo ip netns exec sdnB ip link delete br-sw type bridge 2>/dev/null || true
sudo ip link delete hostA 2>/dev/null || true
sudo ip link delete hostA8 2>/dev/null || true
sudo ip link delete hostB 2>/dev/null || true
sudo ip link delete hostB9 2>/dev/null || true

# ==========================================
# 1. Namespace sdnA Setup
# ==========================================
echo "==> Configuring namespace sdnA..."
if ! sudo ip netns list | grep -q "sdnA"; then
    sudo ip netns add sdnA
fi
sudo ip netns exec sdnA ip link set lo up

# Create bridge inside sdnA
sudo ip netns exec sdnA ip link add br-sw type bridge
sudo ip netns exec sdnA ip link set br-sw up
sudo ip netns exec sdnA ip addr add 192.168.100.1/24 dev br-sw

# Create veth pair for s1 (Primary: UP)
sudo ip link add hostA type veth peer name hostA-sw
sudo ip link set hostA netns sdnA
sudo ip netns exec sdnA ip link set hostA master br-sw
sudo ip netns exec sdnA ip link set hostA up

# Create veth pair for s8 (Backup: DOWN)
sudo ip link add hostA8 type veth peer name hostA8-sw
sudo ip link set hostA8 netns sdnA
sudo ip netns exec sdnA ip link set hostA8 master br-sw
sudo ip netns exec sdnA ip link set hostA8 down

# Attach OVS switch ports
sudo ip link set hostA-sw up
sudo ip link set hostA8-sw up
sudo ovs-vsctl -- --may-exist add-port s1 hostA-sw
sudo ovs-vsctl -- --may-exist add-port s8 hostA8-sw


# ==========================================
# 2. Namespace sdnB Setup
# ==========================================
echo "==> Configuring namespace sdnB..."
if ! sudo ip netns list | grep -q "sdnB"; then
    sudo ip netns add sdnB
fi
sudo ip netns exec sdnB ip link set lo up

# Create bridge inside sdnB
sudo ip netns exec sdnB ip link add br-sw type bridge
sudo ip netns exec sdnB ip link set br-sw up
sudo ip netns exec sdnB ip addr add 192.168.100.2/24 dev br-sw

# Create veth pair for s7 (Primary: UP)
sudo ip link add hostB type veth peer name hostB-sw
sudo ip link set hostB netns sdnB
sudo ip netns exec sdnB ip link set hostB master br-sw
sudo ip netns exec sdnB ip link set hostB up

# Create veth pair for s9 (Backup: DOWN)
sudo ip link add hostB9 type veth peer name hostB9-sw
sudo ip link set hostB9 netns sdnB
sudo ip netns exec sdnB ip link set hostB9 master br-sw
sudo ip netns exec sdnB ip link set hostB9 down

# Attach OVS switch ports
sudo ip link set hostB-sw up
sudo ip link set hostB9-sw up
sudo ovs-vsctl -- --may-exist add-port s7 hostB-sw
sudo ovs-vsctl -- --may-exist add-port s9 hostB9-sw

echo "==> Topology deployed successfully!"