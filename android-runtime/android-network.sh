#!/bin/bash
set -euo pipefail
# Additive private network for BrandFleet Android; never flush host rules.
BRIDGE=bfandroid
SUBNET=10.77.0.0/24
GATEWAY=10.77.0.1/24
UPLINK=$(ip -4 route show default | awk 'NR == 1 {print $5}')
test -n "$UPLINK"
ip link show "$BRIDGE" >/dev/null 2>&1 || ip link add "$BRIDGE" type bridge
ip -4 addr show dev "$BRIDGE" | grep -q '10.77.0.1/24' || ip addr add "$GATEWAY" dev "$BRIDGE"
ip link set "$BRIDGE" up
sysctl -q -w net.ipv4.ip_forward=1
iptables -C FORWARD -i "$BRIDGE" -o "$UPLINK" -s "$SUBNET" -j ACCEPT 2>/dev/null || iptables -I FORWARD 1 -i "$BRIDGE" -o "$UPLINK" -s "$SUBNET" -j ACCEPT
iptables -C FORWARD -i "$UPLINK" -o "$BRIDGE" -d "$SUBNET" -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || iptables -I FORWARD 1 -i "$UPLINK" -o "$BRIDGE" -d "$SUBNET" -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
iptables -t nat -C POSTROUTING -s "$SUBNET" -o "$UPLINK" -j MASQUERADE 2>/dev/null || iptables -t nat -A POSTROUTING -s "$SUBNET" -o "$UPLINK" -j MASQUERADE
# Prevent a future forwarding rule from publishing the Android debug port.
iptables -C INPUT -i "$UPLINK" -p tcp --dport 5555 -j REJECT 2>/dev/null || iptables -I INPUT 1 -i "$UPLINK" -p tcp --dport 5555 -j REJECT

# Only the private website ingress may initiate app/ADB connections into Android.
INGRESS=10.77.1.10
iptables -C FORWARD -i "$UPLINK" -o "$BRIDGE" -s "$INGRESS" -d "$SUBNET" -p tcp -m multiport --dports 80,3000,5089,7700,8080,8081,5555 -j ACCEPT 2>/dev/null || iptables -I FORWARD 1 -i "$UPLINK" -o "$BRIDGE" -s "$INGRESS" -d "$SUBNET" -p tcp -m multiport --dports 80,3000,5089,7700,8080,8081,5555 -j ACCEPT
iptables -C FORWARD -o "$BRIDGE" -d "$SUBNET" -m conntrack --ctstate NEW -j REJECT 2>/dev/null || iptables -A FORWARD -o "$BRIDGE" -d "$SUBNET" -m conntrack --ctstate NEW -j REJECT
