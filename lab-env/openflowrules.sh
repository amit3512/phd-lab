#!/usr/bin/env bash
set -euo pipefail

echo "OpenFlow-regels toepassen..."

add_rule() {
    local sw="$1"
    local flow="$2"
    sudo ovs-ofctl -O OpenFlow13 add-flow "$sw" "$flow"
}

del_flows() {
    local sw="$1"
    sudo ovs-ofctl -O OpenFlow13 del-flows "$sw"
}

# 1. Switch s1
del_flows netdev@s1
add_rule netdev@s1 "priority=100,in_port=hostA-sw,actions=output:v12a"
add_rule netdev@s1 "priority=100,in_port=v12a,actions=output:hostA-sw"

# 2. Switch s2
del_flows netdev@s2
add_rule netdev@s2 "priority=100,in_port=v12b,actions=output:v23a"
add_rule netdev@s2 "priority=100,in_port=v23a,actions=output:v12b"
add_rule netdev@s2 "priority=100,in_port=v28a,actions=output:v210a"
add_rule netdev@s2 "priority=100,in_port=v210a,actions=output:v28a"

# 3. Switch s3
del_flows netdev@s3
add_rule netdev@s3 "priority=100,in_port=v23b,actions=output:v34a"
add_rule netdev@s3 "priority=100,in_port=v34a,actions=output:v23b"

# 4. Switch s4
del_flows netdev@s4
add_rule netdev@s4 "priority=100,in_port=v34b,actions=output:v47a"
add_rule netdev@s4 "priority=100,in_port=v47a,actions=output:v34b"
add_rule netdev@s4 "priority=100,in_port=v410a,actions=output:v49a"
add_rule netdev@s4 "priority=100,in_port=v49a,actions=output:v410a"

# 5. Switch s5
del_flows netdev@s5
add_rule netdev@s5 "priority=100,in_port=v25b,actions=output:v56a"
add_rule netdev@s5 "priority=100,in_port=v56a,actions=output:v25b"

# 6. Switch s6
del_flows netdev@s6
add_rule netdev@s6 "priority=100,in_port=v56b,actions=output:v64a"
add_rule netdev@s6 "priority=100,in_port=v64a,actions=output:v56b"

# 7. Switch s7
del_flows netdev@s7
add_rule netdev@s7 "priority=100,in_port=v47b,actions=output:hostB-sw"
add_rule netdev@s7 "priority=100,in_port=hostB-sw,actions=output:v47b"

# 8. Switch s8
del_flows netdev@s8
add_rule netdev@s8 "priority=100,in_port=hostA8-sw,actions=output:v28b"
add_rule netdev@s8 "priority=100,in_port=v28b,actions=output:hostA8-sw"

# 9. Switch s9
del_flows netdev@s9
add_rule netdev@s9 "priority=100,in_port=v49b,actions=output:hostB9-sw"
add_rule netdev@s9 "priority=100,in_port=hostB9-sw,actions=output:v49b"

# 10. Switch s10
del_flows netdev@s10
add_rule netdev@s10 "priority=100,in_port=v210b,actions=output:v410b"
add_rule netdev@s10 "priority=100,in_port=v410b,actions=output:v210b"

echo "Klaar! Alle OpenFlow-regels zijn ingesteld."
