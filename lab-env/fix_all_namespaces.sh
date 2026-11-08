#!/bin/bash
set -e

echo "==> Completely cleaning up namespaces and veth pairs..."
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

# --- Setup sdnA ---
echo "Setting up sdnA..."
sudo ip netns add sdnA
sudo ip netns exec sdnA ip link set lo up
sudo ip netns exec sdnA ip link add br-sw type bridge
sudo ip netns exec sdnA ip link set br-sw up
sudo ip netns exec sdnA ip addr add 192.168.100.1/24 dev br-sw

# veth for s1 (Primary)
sudo ip link add hostA type veth peer name hostA-sw
sudo ip link set hostA netns sdnA
sudo ip netns exec sdnA ip link set hostA master br-sw
sudo ip netns exec sdnA ip link set hostA up

# veth for s8 (Backup)
sudo ip link add hostA8 type veth peer name hostA8-sw
sudo ip link set hostA8 netns sdnA
sudo ip netns exec sdnA ip link set hostA8 master br-sw
sudo ip netns exec sdnA ip link set hostA8 down

# --- Setup sdnB ---
echo "Setting up sdnB..."
sudo ip netns add sdnB
sudo ip netns exec sdnB ip link set lo up
sudo ip netns exec sdnB ip link add br-sw type bridge
sudo ip netns exec sdnB ip link set br-sw up
sudo ip netns exec sdnB ip addr add 192.168.100.2/24 dev br-sw

# veth for s7 (Primary)
sudo ip link add hostB type veth peer name hostB-sw
sudo ip link set hostB netns sdnB
sudo ip netns exec sdnB ip link set hostB master br-sw
sudo ip netns exec sdnB ip link set hostB up

# veth for s9 (Backup)
sudo ip link add hostB9 type veth peer name hostB9-sw
sudo ip link set hostB9 netns sdnB
sudo ip netns exec sdnB ip link set hostB9 master br-sw
sudo ip netns exec sdnB ip link set hostB9 down

# --- Bring up OVS-facing veth ends ---
sudo ip link set hostA-sw up
sudo ip link set hostA8-sw up
sudo ip link set hostB-sw up
sudo ip link set hostB9-sw up

# --- Attach to OVS Switches ---
sudo ovs-vsctl -- --may-exist add-port s1 hostA-sw
sudo ovs-vsctl -- --may-exist add-port s8 hostA8-sw
sudo ovs-vsctl -- --may-exist add-port s7 hostB-sw
sudo ovs-vsctl -- --may-exist add-port s9 hostB9-sw

echo "==> Both namespaces successfully rebuilt and attached!"
