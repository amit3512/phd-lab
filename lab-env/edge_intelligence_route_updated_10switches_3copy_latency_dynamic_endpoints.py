# Copyright (C) 2026 Edge Intelligence SDN Project.
# Fully Integrated Dynamic Multi-Hop Routing Script with Strict Reciprocal Endpoint Pairs (s1<->s7 and s8<->s9).

import time
import joblib
import pandas as pd
import networkx as nx
from operator import attrgetter
from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import MAIN_DISPATCHER, DEAD_DISPATCHER, CONFIG_DISPATCHER, set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib import hub
from ryu.topology import event as topo_event
from ryu.topology.api import get_switch, get_link
from ryu.lib.packet import packet, ethernet, arp

# Import your Real-Time Training Component
from rftc_updated_1copy import RealTimeTrainingComponent

class EdgeIntelligenceRoute(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(EdgeIntelligenceRoute, self).__init__(*args, **kwargs)
        self.datapaths = {}
        self.prev_stats = {}      # Key: (dpid, port_no), Value: (timestamp, rx_bytes, tx_bytes, rx_packets, tx_packets)
        self.link_loads = {}      # Key: (dpid, port_no), Value: rx_mbps
        self.link_packet_rates = {} # Key: (dpid, port_no), Value: rx_pps (packets per second)
        
        # Tracking dictionaries for link-level data-plane latencies
        self.link_latencies = {}  # Key: (src_dpid, dst_dpid), Value: latency_ms
        
        # Dynamic port mapping cache: Key: dpid, Value: {port_name: port_no}
        self.switch_port_maps = {}

        # Tracking dictionaries for hysteresis & persistence
        self.consecutive_predictions = {} # Key: (dpid, port_no), Value: count
        self.current_routes = {}          # Key: (src, dst), Value: (path, cost, last_change_time)
        
        # Tracking dictionaries for native OpenFlow Echo Latency
        self.echo_send_times = {}         # Key: dpid, Value: start_time
        self.echo_latencies = {}          # Key: dpid, Value: latest rtt_ms

        # Latest tracked network latency metrics cache
        self.latest_network_latency = {
            "latency_min_ms": 0.0,
            "latency_avg_ms": 0.0,
            "latency_max_ms": 0.0,
            "packet_loss_pct": 0.0
        }
        
        # 1. Initialize NetworkX Topology Graph for Multi-Hop Routing
        self.net = nx.MultiDiGraph()

        # Load the trained AI model bundle
        self.model_path = "model_built_updated.joblib"
        self.logger.info("Loading AI model from %s...", self.model_path)
        bundle = joblib.load(self.model_path)
        
        self.model = bundle["model"]
        self.features = bundle["features"]
        self.logger.info('Features loaded successfully.')
        self.numeric_features = bundle["numeric_features"]
        self.categorical_features = bundle["categorical_features"]

        # Force the estimator to run single-threaded (n_jobs=1) to prevent eventlet freezes
        if hasattr(self.model, 'set_params'):
            try:
                self.model.set_params(n_jobs=1)
            except Exception:
                pass

        if hasattr(self.model, 'steps'):
            for name, step in self.model.steps:
                if hasattr(step, 'n_jobs'):
                    step.n_jobs = 1
        
        # Initialize RFTC module for online buffering/retraining
        self.logger.info("Initializing RFTC...")
        self.rftc = RealTimeTrainingComponent(self.logger)
        self.logger.info("RFTC initialized successfully.")

        # Start background monitoring, echo latency, and continuous link-latency probing threads
        self.monitor_thread = hub.spawn(self._monitor_worker)
        self.echo_thread = hub.spawn(self._echo_latency_worker)
        self.latency_probe_thread = hub.spawn(self._continuous_latency_probe_worker)

    def _continuous_latency_probe_worker(self):
        """Continuously measures link latencies, updates active path cost, and prints current path latency & link packet rates."""
        while True:
            hub.sleep(3)  # Probe interval
            
            for u, v, data in self.net.edges(data=True):
                base_rtt = self.echo_latencies.get(u, 1.0)
                neighbor_rtt = self.echo_latencies.get(v, 1.0)
                estimated_link_latency = max(0.5, (base_rtt + neighbor_rtt) / 4.0)
                self.link_latencies[(u, v)] = estimated_link_latency

            # Calculate and print current continuous path latency and link packet rates for active routes
            for route_key, route_info in list(self.current_routes.items()):
                path, _, _ = route_info
                current_path_latency = 0.0
                
                for i in range(len(path) - 1):
                    u, v = path[i], path[i+1]
                    current_path_latency += self.link_latencies.get((u, v), 1.0)
                    
                    edge_dict = self.net.get_edge_data(u, v)
                    if edge_dict:
                        edge_data = list(edge_dict.values())[0]
                        p_no = edge_data.get('port')
                        pps = self.link_packet_rates.get((u, p_no), 0.0)
                        self.logger.info("  -> Link [%s <--> %s] Active Traffic: Packet Rate = %.2f pps", 
                                         self._get_switch_name(u), self._get_switch_name(v), pps)

                named_path_str = " -> ".join([self._get_switch_name(d) for d in path])
                self.logger.info("[LIVE MONITOR] Active Path [%s] Current Latency: %.3f ms", named_path_str, current_path_latency)

                LATENCY_THRESHOLD_MS = 6.0
                if current_path_latency > LATENCY_THRESHOLD_MS:
                    self.logger.warning("HIGH PATH LATENCY DETECTED (%.3f ms > Threshold %.1f ms)! Forcing path & source switch for testing...", 
                                         current_path_latency, LATENCY_THRESHOLD_MS)
                    
                    # Force switch active source for testing purposes
                    if self.active_source_name == "s1":
                        self.active_source_name = "s8"
                        self.logger.warning("[TEST FORCE SWITCH OVER] Toggled active source from s1 to s8!")
                    else:
                        self.active_source_name = "s1"
                        self.logger.warning("[TEST FORCE SWITCH OVER] Toggled active source from s8 to s1!")
                        
                    # Force path recalculation using the newly toggled source pair
                    self.evaluate_and_reroute_paths(path[0], force=True)

    def _echo_latency_worker(self):
        """Actively sends OpenFlow Echo requests strictly after active paths are installed."""
        while True:
            hub.sleep(10)
            
            if not self.current_routes:
                continue
                
            for route_key, route_info in list(self.current_routes.items()):
                path, _, _ = route_info
                for dpid in path:
                    datapath = self.datapaths.get(dpid)
                    if datapath:
                        parser = datapath.ofproto_parser
                        echo_req = parser.OFPEchoRequest(datapath, data=b'echo_probe')
                        self.echo_send_times[dpid] = time.time()
                        datapath.send_msg(echo_req)

    @set_ev_cls(ofp_event.EventOFPEchoReply, MAIN_DISPATCHER)
    def _echo_reply_handler(self, ev):
        """Handles incoming OpenFlow Echo replies and logs real-time RTT."""
        msg = ev.msg
        datapath = msg.datapath
        dpid = datapath.id

        if dpid in self.echo_send_times:
            send_time = self.echo_send_times.pop(dpid)
            rtt_ms = (time.time() - send_time) * 1000.0
            switch_name = self._get_switch_name(dpid)
            
            self.echo_latencies[dpid] = rtt_ms
            
            self.latest_network_latency["latency_avg_ms"] = rtt_ms
            if self.latest_network_latency["latency_min_ms"] == 0.0 or rtt_ms < self.latest_network_latency["latency_min_ms"]:
                self.latest_network_latency["latency_min_ms"] = rtt_ms
            if rtt_ms > self.latest_network_latency["latency_max_ms"]:
                self.latest_network_latency["latency_max_ms"] = rtt_ms

    @set_ev_cls([topo_event.EventSwitchEnter, topo_event.EventSwitchLeave,
                 topo_event.EventLinkAdd, topo_event.EventLinkDelete])
    def _topology_change_handler(self, ev):
        """Dynamically discovers topology graph and triggers autonomous initial path installation."""
        self.net.clear()

        switch_list = get_switch(self, None)
        for switch in switch_list:
            self.net.add_node(switch.dp.id, datapath=switch.dp)
        
        link_list = get_link(self, None)
        for link in link_list:
            self.net.add_edge(
                link.src.dpid, 
                link.dst.dpid, 
                port=link.src.port_no,
                dst_port=link.dst.port_no,
                capacity=1000.0
            )
        self.logger.info("Topology Graph Updated. Total Nodes (Switches): %d | Total Edges (Links): %d", 
                         len(self.net.nodes), len(self.net.edges))
        
        self.install_initial_path_at_startup()

    def _get_dynamic_host_port(self, switch_name):
        dpid = self._get_dpid_by_name(switch_name)
        if not dpid:
            return 2 if switch_name in ["s1", "s7"] else 3
            
        p_map = self.switch_port_maps.get(dpid, {})
        target_keywords = {
            "s1": "hostA-sw",
            "s8": "hostA8-sw",
            "s7": "hostB-sw",
            "s9": "hostB9-sw"
        }
        target_keyword = target_keywords.get(switch_name, "")
        
        for name, pno in p_map.items():
            if target_keyword in name:
                return pno
                
        return 2 if switch_name in ["s1", "s7"] else 3

    def _get_active_endpoint_pair(self):
        """
        Reciprocal Dual-Path Endpoint Switching:
        - Pair 1: s1 (192.168.100.1) <-> s7 (192.168.100.2)
        - Pair 2: s8 (192.168.108.1) <-> s9 (192.168.108.2)
        """
        s1_dpid = self._get_dpid_by_name("s1")
        s7_dpid = self._get_dpid_by_name("s7")
        s8_dpid = self._get_dpid_by_name("s8")
        s9_dpid = self._get_dpid_by_name("s9")
        
        s1_port = self._get_dynamic_host_port("s1")
        s8_port = self._get_dynamic_host_port("s8")
        
        s1_congested = self.consecutive_predictions.get((s1_dpid, s1_port), 0) >= 2
        s8_congested = self.consecutive_predictions.get((s8_dpid, s8_port), 0) >= 2
        
        # if s1_congested and not s8_congested:
        #     self.logger.warning("[SWITCH OVER] Pair 1 (s1 <-> s7) is congested! Switching to Pair 2 (s8 <-> s9).")
        #     return s8_dpid, s9_dpid
        # elif s8_congested and not s1_congested:
        #     self.logger.warning("[SWITCH OVER] Pair 2 (s8 <-> s9) is congested! Switching back to Pair 1 (s1 <-> s7).")
        #     return s1_dpid, s7_dpid

        # for (src, dst) in self.current_routes.keys():
        #             if src == s8_dpid:
        #                 return s8_dpid, s9_dpid

        if s8_congested and not s1_congested:
                    self.logger.warning("[SWITCH OVER] Pair 1 (s1 <-> s7) is congested! Switching to Pair 2 (s8 <-> s9).")
                    # Automatically set traffic generator target IP to 100.2 for s7
                    try:
                        with open("/tmp/active_target_ip.txt", "w") as f:
                            f.write("192.168.100.2")
                    except Exception:
                        pass
                    return s1_dpid, s7_dpid
        elif s1_congested and not s8_congested:
                    self.logger.warning("[SWITCH OVER] Pair 1 (s1 <-> s7) is congested! Switching to Pair 2 (s8 <-> s9).")
                    try:
                        with open("/tmp/active_target_ip.txt", "w") as f:
                            f.write("192.168.108.2")
                    except Exception:
                        pass
                    return s8_dpid, s9_dpid
            
        for (src, dst) in self.current_routes.keys():
            if src == s1_dpid:
                return s1_dpid, s7_dpid

        # with open("/tmp/active_target_ip.txt", "w") as f:
        #                             f.write("192.168.108.2")        
        return s8_dpid, s9_dpid

    def install_initial_path_at_startup(self):
        src_dpid, dst_dpid = self._get_active_endpoint_pair()
        route_key = (src_dpid, dst_dpid)

        if route_key in self.current_routes:
            return

        self.logger.info("Looking for best Initial path (src: %s, dst: %s)...", self._get_switch_name(src_dpid), self._get_switch_name(dst_dpid))

        if src_dpid and dst_dpid and src_dpid in self.net and dst_dpid in self.net:
            if nx.has_path(self.net, src_dpid, dst_dpid):
                candidate_paths = list(nx.all_simple_paths(self.net, src_dpid, dst_dpid))
                if not candidate_paths:
                    return

                feasible_paths = []
                for path in candidate_paths:
                    path_cost = 0.0
                    path_latency_sum = 0.0
                    
                    for i in range(len(path) - 1):
                        u, v = path[i], path[i+1]
                        edge_data = self.net.get_edge_data(u, v)
                        if not edge_data:
                            continue
                        port_no = edge_data[0]['port']
                        link_load = self.link_loads.get((u, port_no), 0.0)
                        port_key = (u, port_no)
                        predicted_risk = 1 if self.consecutive_predictions.get(port_key, 0) >= 2 else 0
                        
                        link_latency = self.link_latencies.get((u, v), 1.0)
                        path_latency_sum += link_latency
                        
                        congestion_weight = 500.0
                        latency_weight = 10.0
                        path_cost += link_load + (congestion_weight * predicted_risk) + (latency_weight * link_latency)
                    
                    feasible_paths.append((path, path_cost, path_latency_sum))

                if feasible_paths:
                    best_path, initial_cost, best_path_latency = min(feasible_paths, key=lambda x: x[1])
                    self.current_routes[route_key] = (best_path, initial_cost, time.time())

                    readable_path = " -> ".join([self._get_name_by_dpid(node) for node in best_path])
                    self.logger.info("##################################################")
                    self.logger.info("SUCCESS: AI-DRIVEN INITIAL PATH INSTALLED: %s", best_path)
                    self.logger.info("ROUTE (Names): %s", readable_path)
                    self.logger.info("OVERALL PATH LATENCY: %.3f ms (Total Cost: %.2f)", best_path_latency, initial_cost)
                    self.logger.info("##################################################")

                    named_path = [self._get_name_by_dpid(dpid) for dpid in best_path]
                    self.logger.info("HOP-BY-HOP ROUTE NAMES: %s", named_path)
                    self.install_multi_hop_rules(best_path)

            else:
                self.logger.info("Waiting for links to connect before installing initial path...")
        else:
            self.logger.info("Waiting for source/destination switches to register...")

    @set_ev_cls(ofp_event.EventOFPStateChange, [MAIN_DISPATCHER, DEAD_DISPATCHER])
    def _state_change_handler(self, ev):
        datapath = ev.datapath
        dpid = datapath.id
        if ev.state == MAIN_DISPATCHER:
            if dpid not in self.datapaths:
                self.logger.info('Registering datapath: %016x', dpid)
                self.datapaths[dpid] = datapath
        elif ev.state == DEAD_DISPATCHER:
            if dpid in self.datapaths:
                self.logger.info('Unregistering datapath: %016x', dpid)
                del self.datapaths[dpid]

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        self.datapaths[datapath.id] = datapath
        self.logger.info("Switch registered: DPID=%s", datapath.id)
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER,
                                         ofproto.OFPCML_NO_BUFFER)]
        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
        mod = parser.OFPFlowMod(datapath=datapath, priority=0,
                                match=match, instructions=inst)
        datapath.send_msg(mod)
        self.logger.info("Installed proactive table-miss rule for datapath: %016x", datapath.id)

        req = parser.OFPPortDescStatsRequest(datapath, 0)
        datapath.send_msg(req)

    @set_ev_cls(ofp_event.EventOFPPortDescStatsReply, MAIN_DISPATCHER)
    def _port_desc_stats_reply_handler(self, ev):
        dpid = ev.msg.datapath.id
        self.switch_port_maps[dpid] = {}
        for p in ev.msg.body:
            p_name = p.name.decode('utf-8') if isinstance(p.name, bytes) else p.name
            self.switch_port_maps[dpid][p_name] = p.port_no
        self.logger.info("Cached port descriptions for switch %s: %s", self._get_switch_name(dpid), self.switch_port_maps[dpid])

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in_handler(self, ev):
        try:
            msg = ev.msg
            datapath = msg.datapath
            ofproto = datapath.ofproto
            parser = datapath.ofproto_parser
            in_port = msg.match['in_port']

            pkt = packet.Packet(msg.data)
            eth = pkt.get_protocol(ethernet.ethernet)

            if eth and eth.ethertype == 0x0806:
                actions = [parser.OFPActionOutput(ofproto.OFPP_FLOOD)]
                out = parser.OFPPacketOut(
                    datapath=datapath,
                    buffer_id=msg.buffer_id,
                    in_port=in_port,
                    actions=actions,
                    data=msg.data,
                )
                datapath.send_msg(out)
        except Exception as e:
            self.logger.error("ERROR in _packet_in_handler: %s", str(e), exc_info=True)

    def _monitor_worker(self):
        while True:
            hub.sleep(5)
            src_dpid, dst_dpid = self._get_active_endpoint_pair()
            if (src_dpid, dst_dpid) not in self.current_routes:
                if len(self.net.edges) > 0:
                    self.install_initial_path_at_startup()

            for dpid, datapath in list(self.datapaths.items()):
                self._request_port_stats(datapath)

    def _request_port_stats(self, datapath):
        ofp = datapath.ofproto
        parser = datapath.ofproto_parser
        req = parser.OFPPortStatsRequest(datapath, 0, ofp.OFPP_ANY)
        datapath.send_msg(req)

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def _port_stats_reply_handler(self, ev):
        body = ev.msg.body
        dpid = ev.msg.datapath.id
        switch_name = self._get_switch_name(dpid)
        current_time = time.time()
        
        port_evaluations = {}
        
        try:
            for stat in sorted(body, key=attrgetter('port_no')):
                if stat.port_no == ofproto_v1_3.OFPP_LOCAL:
                    continue

                port_name_map = {
                    ("s1", 1): "v12a", ("s1", 2): "hostA-sw",
                    ("s2", 1): "v12b", ("s2", 2): "v23a", ("s2", 3): "v25a", ("s2", 4): "v28a", ("s2", 5): "v210a",
                    ("s3", 1): "v23b", ("s3", 2): "v34a", ("s3", 3): "v38a",
                    ("s4", 1): "v34b", ("s4", 2): "v47a", ("s4", 3): "v64b", ("s4", 4): "v410a", ("s4", 5): "v49a",
                    ("s5", 1): "v25b", ("s5", 2): "v56a",
                    ("s6", 1): "v56b", ("s6", 2): "v64a", ("s6", 3): "v69a",
                    ("s7", 1): "hostB-sw", ("s7", 2): "v47b",
                    ("s8", 1): "v28b", ("s8", 2): "v38b", ("s8", 3): "hostA8-sw",
                    ("s9", 1): "v49b", ("s9", 2): "v69b", ("s9", 3): "hostB9-sw",
                    ("s10", 1): "v210b", ("s10", 2): "v410b"
                }
                port_name = port_name_map.get((switch_name, stat.port_no), f"port_{stat.port_no}")
                key = (dpid, stat.port_no)

                rx_mbps, tx_mbps, rx_pps, tx_pps = 0.0, 0.0, 0.0, 0.0

                if key in self.prev_stats:
                    prev_time, prev_rx_bytes, prev_tx_bytes, prev_rx_packets, prev_tx_packets = self.prev_stats[key]
                    time_interval = current_time - prev_time
                    if time_interval > 0:
                        rx_mbps = ((stat.rx_bytes - prev_rx_bytes) * 8) / (time_interval * 1000000.0)
                        tx_mbps = ((stat.tx_bytes - prev_tx_bytes) * 8) / (time_interval * 1000000.0)
                        rx_pps = (stat.rx_packets - prev_rx_packets) / time_interval
                        tx_pps = (stat.tx_packets - prev_tx_packets) / time_interval

                self.prev_stats[key] = (current_time, stat.rx_bytes, stat.tx_bytes, stat.rx_packets, stat.tx_packets)
                self.link_loads[key] = max(0.0, rx_mbps)
                self.link_packet_rates[key] = max(0.0, rx_pps)

                stats_dict = {
                    "switch": switch_name, "port": port_name,
                    "rx_packets": stat.rx_packets, "tx_packets": stat.tx_packets,
                    "rx_bytes": stat.rx_bytes, "tx_bytes": stat.tx_bytes,
                    "rx_drops": stat.rx_dropped, "tx_drops": stat.tx_dropped,
                    "rx_errors": stat.rx_errors, "tx_errors": stat.tx_errors,
                    "rx_mbps": max(0.0, rx_mbps), "tx_mbps": max(0.0, tx_mbps),
                    "rx_pps": max(0.0, rx_pps), "tx_pps": max(0.0, tx_pps),
                    "latency_avg_ms": self.latest_network_latency["latency_avg_ms"],
                    "latency_max_ms": self.latest_network_latency["latency_max_ms"],
                    "latency_min_ms": self.latest_network_latency["latency_min_ms"],
                    "packet_loss_pct": self.latest_network_latency["packet_loss_pct"]
                }

                self.rftc.ingest_telemetry(stats_dict, self.features)

                input_df = pd.DataFrame([stats_dict], columns=self.features)
                for col in self.numeric_features:
                    input_df[col] = pd.to_numeric(input_df[col], errors="coerce").fillna(0)

                prediction = self.model.predict(input_df)[0]

                port_key = (dpid, stat.port_no)
                if prediction == 1:
                    self.consecutive_predictions[port_key] = self.consecutive_predictions.get(port_key, 0) + 1
                else:
                    self.consecutive_predictions[port_key] = 0

                persistent_prediction = 1 if self.consecutive_predictions.get(port_key, 0) >= 2 else 0

                port_evaluations[stat.port_no] = {
                    "port_no": stat.port_no,
                    "port_name": port_name,
                    "prediction": persistent_prediction,
                    "total_load": rx_mbps + tx_mbps
                }

            for port_no, info in port_evaluations.items():
                if info["prediction"] == 1:
                    self.logger.warning("PERSISTENT CONGESTION PREDICTED: Switch %s, Port %s (%s)! RTT Latency: %.2f ms", 
                                         switch_name, port_no, info["port_name"], self.latest_network_latency["latency_avg_ms"])
                    self.evaluate_and_reroute_paths(dpid)

            if self.rftc.should_retrain():
                self.model = self.rftc.retrain_model(
                    self.model, self.features, self.categorical_features, self.numeric_features
                )
        except Exception as e:
            self.logger.error("CRASH in port stats handler: %s", str(e), exc_info=True)

    def evaluate_and_reroute_paths(self, congested_dpid, force=False):
        src_dpid, dst_dpid = self._get_active_endpoint_pair()
        route_key = (src_dpid, dst_dpid)
        
        if not src_dpid or not dst_dpid or not nx.has_path(self.net, src_dpid, dst_dpid):
            return

        candidate_paths = list(nx.all_simple_paths(self.net, src_dpid, dst_dpid))
        feasible_paths = []

        for path in candidate_paths:
            path_cost = 0.0
            path_latency_sum = 0.0
            
            for i in range(len(path) - 1):
                u, v = path[i], path[i+1]
                edge_data = self.net.get_edge_data(u, v)
                if not edge_data:
                    continue
                port_no = edge_data[0]['port']
                
                link_load = self.link_loads.get((u, port_no), 0.0)
                port_key = (u, port_no)
                predicted_risk = 1 if self.consecutive_predictions.get(port_key, 0) >= 2 else 0
                
                link_latency = self.link_latencies.get((u, v), 1.0)
                path_latency_sum += link_latency
                
                congestion_weight = 500.0
                latency_weight = 10.0
                path_cost += link_load + (congestion_weight * predicted_risk) + (latency_weight * link_latency)

            feasible_paths.append((path, path_cost, path_latency_sum))

        if not feasible_paths:
            return

        best_path, new_cost, best_path_latency = min(feasible_paths, key=lambda x: x[1])
        
        current_time = time.time()
        cooldown_period = 10

        if not force and route_key in self.current_routes:
            old_path, old_cost, last_change = self.current_routes[route_key]
            if current_time - last_change < cooldown_period:
                return
            if old_cost > 0:
                improvement = (old_cost - new_cost) / old_cost
                if improvement < 0.10:
                    return

        self.current_routes[route_key] = (best_path, new_cost, current_time)

        readable_path = " -> ".join([self._get_name_by_dpid(node) for node in best_path])
        self.logger.warning("##################################################")
        self.logger.warning("REROUTE TRIGGERED: NEW AI-DRIVEN PATH INSTALLED: %s", best_path)
        self.logger.warning("NEW ROUTE (Names): %s", readable_path)
        self.logger.warning("OVERALL PATH LATENCY: %.3f ms (Total Cost: %.2f)", best_path_latency, new_cost)
        self.logger.warning("##################################################")

        named_path = [self._get_name_by_dpid(dpid) for dpid in best_path]
        self.logger.warning("HOP-BY-HOP ROUTE NAMES: %s", named_path)
        self.install_multi_hop_rules(best_path)

    def install_multi_hop_rules(self, path):
        self.logger.warning("INSTALLING BIDIRECTIONAL MULTI-HOP PATH RULES: %s", path)
        if not path or len(path) < 2:
            return

        start_dpid = path[0]
        end_dpid = path[-1]

        start_switch_name = self._get_switch_name(start_dpid)
        end_switch_name = self._get_switch_name(end_dpid)

        # Dynamic host port resolution matching Pair 1 (s1<->s7) or Pair 2 (s8<->s9)
        HOST_A_PORT = 2 if start_switch_name == "s1" else 3
        HOST_B_PORT = 2 if end_switch_name == "s7" else 3

        self.logger.info("Host Port Mapping -> Source: %s (Port %s), Destination: %s (Port %s)", 
                         start_switch_name, HOST_A_PORT, end_switch_name, HOST_B_PORT)

        # --- 1. FORWARD PATH ---
        for i in range(len(path) - 1):
            curr_dpid = path[i]
            next_dpid = path[i+1]
            
            datapath = self.datapaths.get(curr_dpid)
            if not datapath:
                continue

            parser = datapath.ofproto_parser
            ofproto = datapath.ofproto

            edge_dict = self.net.get_edge_data(curr_dpid, next_dpid)
            if not edge_dict:
                continue
            edge_data = list(edge_dict.values())[0]
            out_port = edge_data.get('port')
            if out_port is None:
                continue

            if curr_dpid == start_dpid:
                in_port = HOST_A_PORT
            else:
                prev_dpid = path[i-1]
                prev_edge_dict = self.net.get_edge_data(prev_dpid, curr_dpid)
                in_port = None
                if prev_edge_dict:
                    prev_edge = list(prev_edge_dict.values())[0]
                    in_port = prev_edge.get('dst_port')
                if in_port is None:
                    in_port = edge_data.get('port', 1)

            match = parser.OFPMatch(in_port=in_port)
            actions = [parser.OFPActionOutput(out_port)]
            inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
            
            mod = parser.OFPFlowMod(
                datapath=datapath,
                priority=2000,
                match=match,
                instructions=inst,
                command=ofproto.OFPFC_ADD
            )
            datapath.send_msg(mod)

        last_datapath = self.datapaths.get(end_dpid)
        if last_datapath:
            parser = last_datapath.ofproto_parser
            ofproto = last_datapath.ofproto

            prev_dpid = path[-2]
            edge_dict = self.net.get_edge_data(prev_dpid, end_dpid)
            in_port_from_core = None
            if edge_dict:
                edge_data = list(edge_dict.values())[0]
                in_port_from_core = edge_data.get('dst_port')
            if in_port_from_core is None:
                in_port_from_core = 2

            match = parser.OFPMatch(in_port=in_port_from_core)
            actions = [parser.OFPActionOutput(HOST_B_PORT)]
            inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
            
            mod = parser.OFPFlowMod(
                datapath=last_datapath,
                priority=2000,
                match=match,
                instructions=inst,
                command=ofproto.OFPFC_ADD
            )
            last_datapath.send_msg(mod)

        # --- 2. REVERSE PATH ---
        rev_path = list(reversed(path))
        for i in range(len(rev_path) - 1):
            curr_dpid = rev_path[i]
            next_dpid = rev_path[i+1]
            
            datapath = self.datapaths.get(curr_dpid)
            if not datapath:
                continue

            parser = datapath.ofproto_parser
            ofproto = datapath.ofproto

            edge_dict = self.net.get_edge_data(curr_dpid, next_dpid)
            if not edge_dict:
                continue
            edge_data = list(edge_dict.values())[0]
            out_port = edge_data.get('port')
            if out_port is None:
                continue

            if curr_dpid == end_dpid:
                in_port = HOST_B_PORT
            else:
                prev_dpid = rev_path[i-1]
                prev_edge_dict = self.net.get_edge_data(prev_dpid, curr_dpid)
                in_port = None
                if prev_edge_dict:
                    prev_edge = list(prev_edge_dict.values())[0]
                    in_port = prev_edge.get('dst_port')
                if in_port is None:
                    in_port = edge_data.get('port', 1)

            match = parser.OFPMatch(in_port=in_port)
            actions = [parser.OFPActionOutput(out_port)]
            inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
            
            mod = parser.OFPFlowMod(
                datapath=datapath,
                priority=2000,
                match=match,
                instructions=inst,
                command=ofproto.OFPFC_ADD
            )
            datapath.send_msg(mod)

        last_datapath_rev = self.datapaths.get(start_dpid)
        if last_datapath_rev:
            parser = last_datapath_rev.ofproto_parser
            ofproto = last_datapath_rev.ofproto

            prev_dpid = rev_path[-2]
            edge_dict = self.net.get_edge_data(prev_dpid, start_dpid)
            in_port_from_core = None
            if edge_dict:
                edge_data = list(edge_dict.values())[0]
                in_port_from_core = edge_data.get('dst_port')
            if in_port_from_core is None:
                in_port_from_core = 2

            match = parser.OFPMatch(in_port=in_port_from_core)
            actions = [parser.OFPActionOutput(HOST_A_PORT)]
            inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
            
            mod = parser.OFPFlowMod(
                datapath=last_datapath_rev,
                priority=2000,
                match=match,
                instructions=inst,
                command=ofproto.OFPFC_ADD
            )
            last_datapath_rev.send_msg(mod)

    def _get_switch_name(self, dpid):
        dpid_hex = f"{dpid:016x}"
        dpid_map = {
            "0000ae73f7c32b4e": "s1",
            "0000728acfc9874d": "s2",
            "000096da8849ce42": "s3",
            "0000aef6e2bf5e4a": "s4",
            "0000829b9d497447": "s5",
            "0000827a9f69124e": "s6",
            "0000fa600d2aa548": "s7",
            "00007a4da0027546": "s8",
            "00001a6518d0254b": "s9",
            "0000da1faa2ec84d": "s10"
        }
        return dpid_map.get(dpid_hex, f"s{dpid}")

    def _get_dpid_by_name(self, name):
        name_map = {
            "s1": 0xae73f7c32b4e,
            "s2": 0x728acfc9874d,
            "s3": 0x96da8849ce42,
            "s4": 0xaef6e2bf5e4a,
            "s5": 0x829b9d497447,
            "s6": 0x827a9f69124e,
            "s7": 0xfa600d2aa548,
            "s8": 0x7a4da0027546,
            "s9": 0x1a6518d0254b,
            "s10": 0xda1faa2ec84d
        }
        hex_val = name_map.get(name)
        return hex_val if hex_val else None

    def _get_name_by_dpid(self, dpid):
        name_map = {
            "s1": 0xae73f7c32b4e,
            "s2": 0x728acfc9874d,
            "s3": 0x96da8849ce42,
            "s4": 0xaef6e2bf5e4a,
            "s5": 0x829b9d497447,
            "s6": 0x827a9f69124e,
            "s7": 0xfa600d2aa548,
            "s8": 0x7a4da0027546,
            "s9": 0x1a6518d0254b,
            "s10": 0xda1faa2ec84d
        }
        reverse_map = {v: k for k, v in name_map.items()}
        return reverse_map.get(dpid, f"unknown({dpid})")