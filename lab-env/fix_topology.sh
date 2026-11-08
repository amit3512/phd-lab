#!/bin/bash
set -e

echo "==> Re-creating network namespaces and bridges..."

# Cleanup everything first
sudo ip netns del sdnA 2>/dev/null || true
sudo ip netns del sdnB 2>/dev/null || true
sudo ip link delete hostA 2>/dev/null || true
sudo ip link delete hostA-sw 2>/dev/null || true
sudo ip link delete hostA8 2>/dev/null || true
sudo ip link delete hostA8-sw 2>/dev/null || true
sudo ip link delete hostB 2>/dev/null || true
sudo ip link delete hostB-sw 2>/dev/null || true
sudo ip link delete hostB9 2>/dev/null || true
sudo ip link delete hostB9-sw 2>/dev/null || true

# 1. Setup sdnA
sudo ip netns add sdnA
sudo ip netns exec sdnA ip link set lo up
sudo ip netns exec sdnA ip link add br-sw type bridge
sudo ip netns exec sdnA ip link set br-sw up
sudo ip netns exec sdnA ip addr add 192.168.100.1/24 dev br-sw

sudo ip link add hostA type veth peer name hostA-sw
sudo ip link set hostA netns sdnA
sudo ip netns exec sdnA ip link set hostA master br-sw
sudo ip netns exec sdnA ip link set hostA up

sudo ip link add hostA8 type veth peer name hostA8-sw
sudo ip link set hostA8 netns sdnA
sudo ip netns exec sdnA ip link set hostA8 master br-sw
sudo ip netns exec sdnA ip link set hostA8 down

# 2. Setup sdnB
sudo ip netns add sdnB
sudo ip netns exec sdnB ip link set lo up
sudo ip netns exec sdnB ip link add br-sw type bridge
sudo ip netns exec sdnB ip link set br-sw up
sudo ip netns exec sdnB ip addr add 192.168.100.2/24 dev br-sw

sudo ip link add hostB type veth peer name hostB-sw
sudo ip link set hostB netns sdnB
sudo ip netns exec sdnB ip link set hostB master br-sw
sudo ip netns exec sdnB ip link set hostB up

sudo ip link add hostB9 type veth peer name hostB9-sw
sudo ip link set hostB9 netns sdnB
sudo ip netns exec sdnB ip link set hostB9 master br-sw
sudo ip netns exec sdnB ip link set hostB9 down

# 3. Attach OVS switch ports
sudo ip link set hostA-sw up
sudo ip link set hostA8-sw up
sudo ip link set hostB-sw up
sudo ip link set hostB9-sw up

sudo ovs-vsctl -- --may-exist add-port s1 hostA-sw
sudo ovs-vsctl -- --may-exist add-port s8 hostA8-sw
sudo ovs-vsctl -- --may-exist add-port s7 hostB-sw
sudo ovs-vsctl -- --may-exist add-port s9 hostB9-sw

echo "==> Topology successfully fixed and deployed!"
