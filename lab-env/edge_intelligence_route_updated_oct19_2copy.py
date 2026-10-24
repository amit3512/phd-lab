# Copyright (C) 2026 Edge Intelligence SDN Project.
# Fully Integrated 14-Step Dynamic Multi-Hop Routing Script with ML Prediction & RFTC

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

# Import your Real-Time Training Component
from rftc_oct19_updated import RealTimeTrainingComponent

class EdgeIntelligenceRoute(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(EdgeIntelligenceRoute, self).__init__(*args, **kwargs)
        self.datapaths = {}
        self.prev_stats = {}      # Key: (dpid, port_no), Value: (timestamp, rx_bytes, tx_bytes, rx_packets, tx_packets)
        self.link_loads = {}      # Key: (dpid, port_no), Value: rx_mbps
        
        # Tracking dictionaries for hysteresis & persistence
        self.consecutive_predictions = {} # Key: (dpid, port_no), Value: count
        self.current_routes = {}          # Key: (src, dst), Value: (path, cost, last_change_time)
        
        # 1. Initialize NetworkX Topology Graph for Multi-Hop Routing
        self.net = nx.MultiDiGraph()

        # Load the trained AI model bundle
        self.model_path = "model_built_oct21_updated.joblib"
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

        # Start background monitoring & inference loop
        self.monitor_thread = hub.spawn(self._monitor_worker)

    @set_ev_cls([topo_event.EventSwitchEnter, topo_event.EventSwitchLeave,
                 topo_event.EventLinkAdd, topo_event.EventLinkDelete])
    def _topology_change_handler(self, ev):
        """Dynamically discovers topology graph and triggers autonomous initial path installation."""
        switch_list = get_switch(self, None)
        self.net.clear()
        for switch in switch_list:
            self.net.add_node(switch.dp.id, datapath=switch.dp)
        
        link_list = get_link(self, None)
        
                
        for link in link_list:
            self.net.add_edge(
                link.src.dpid, 
                link.dst.dpid, 
                port=link.src.port_no,
                dst_port=link.dst.port_no,
                capacity=1000.0  # Default link capacity in Mbps
            )
        self.logger.info("Topology Graph Updated. Total Nodes (Switches): %d | Total Edges (Links): %d", 
                         len(self.net.nodes), len(self.net.edges))
        
        # Proactively install initial AI-driven path from Switch 1 (Host A) to Switch 7 (Host B) at startup
        self.install_initial_path_at_startup()

    # def install_initial_path_at_startup(self):
    #     """Computes and installs the best initial path from Switch 1 to Switch 7 at startup."""
    #     src_dpid = self._get_dpid_by_name("br-ryu-test") # Switch 1 (Host A)
    #     dst_dpid = self._get_dpid_by_name("s7")          # Switch 7 (Host B)
    #     route_key = (src_dpid, dst_dpid)

    #     if route_key in self.current_routes:
    #         return

    #     if src_dpid and dst_dpid and src_dpid in self.net and dst_dpid in self.net:
    #         if nx.has_path(self.net, src_dpid, dst_dpid):
    #             candidate_paths = list(nx.all_simple_paths(self.net, src_dpid, dst_dpid))
    #             if not candidate_paths:
    #                 return

    #             feasible_paths = []
    #             for path in candidate_paths:
    #                 path_cost = 0.0
    #                 for i in range(len(path) - 1):
    #                     u, v = path[i], path[i+1]
    #                     edge_data = self.net.get_edge_data(u, v)
    #                     port_no = edge_data[0]['port']
    #                     link_load = self.link_loads.get((u, port_no), 0.0)
    #                     port_key = (u, port_no)
    #                     predicted_risk = 1 if self.consecutive_predictions.get(port_key, 0) >= 2 else 0
                        
    #                     congestion_weight = 500.0
    #                     path_cost += link_load + (congestion_weight * predicted_risk)
                    
    #                 feasible_paths.append((path, path_cost))

    #             if feasible_paths:
    #                 best_path, initial_cost = min(feasible_paths, key=lambda x: x[1])
    #                 self.current_routes[route_key] = (best_path, initial_cost, time.time())
    #                 self.logger.info("AI-DRIVEN INITIAL PATH INSTALLED (Host A -> Host B): %s", best_path)
    #                 self.install_multi_hop_rules(best_path)
    # def install_initial_path_at_startup(self):
    #     """Computes and installs the best initial path from Switch 1 to Switch 7 with debug logging."""
    #     src_dpid = self._get_dpid_by_name("br-ryu-test") # Switch 1 (Host A)
    #     dst_dpid = self._get_dpid_by_name("s7")          # Switch 7 (Host B)
    #     route_key = (src_dpid, dst_dpid)

    #     # Print current graph nodes for debugging
    #     self.logger.info("DEBUG: Current nodes in graph: %s", list(self.net.nodes))
    #     self.logger.info("DEBUG: Looking for Source (br-ryu-test): %s, Dest (s7): %s", src_dpid, dst_dpid)

    #     if route_key in self.current_routes:
    #         return

    #     if src_dpid and dst_dpid and src_dpid in self.net and dst_dpid in self.net:
    #         if nx.has_path(self.net, src_dpid, dst_dpid):
    #             candidate_paths = list(nx.all_simple_paths(self.net, src_dpid, dst_dpid))
    #             if not candidate_paths:
    #                 self.logger.warning("DEBUG: Graph has path, but no simple paths found between src and dst.")
    #                 return

    #             feasible_paths = []
    #             for path in candidate_paths:
    #                 path_cost = 0.0
    #                 for i in range(len(path) - 1):
    #                     u, v = path[i], path[i+1]
    #                     edge_data = self.net.get_edge_data(u, v)
    #                     if not edge_data:
    #                         continue
    #                     port_no = edge_data[0]['port']
    #                     link_load = self.link_loads.get((u, port_no), 0.0)
    #                     port_key = (u, port_no)
    #                     predicted_risk = 1 if self.consecutive_predictions.get(port_key, 0) >= 2 else 0
                        
    #                     congestion_weight = 500.0
    #                     path_cost += link_load + (congestion_weight * predicted_risk)
                    
    #                 feasible_paths.append((path, path_cost))

    #             if feasible_paths:
    #                 best_path, initial_cost = min(feasible_paths, key=lambda x: x[1])
    #                 self.current_routes[route_key] = (best_path, initial_cost, time.time())
    #                 self.logger.info("==================================================")
    #                 self.logger.info("AI-DRIVEN INITIAL PATH INSTALLED (Host A -> Host B): %s", best_path)
    #                 self.logger.info("==================================================")
    #                 self.install_multi_hop_rules(best_path)
    #         else:
    #             self.logger.warning("DEBUG: nx.has_path returned False between %s and %s", src_dpid, dst_dpid)
    #     else:
    #         self.logger.warning("DEBUG: Source or Destination DPID missing from topology graph!")

    def install_initial_path_at_startup(self):
        """Computes and installs the best initial path from Switch 1 to Switch 7 once graph is ready."""
        # self.logger.info("Looking for best Intial path")
        src_dpid = self._get_dpid_by_name("br-ryu-test") # Switch 1 (Host A)
        dst_dpid = self._get_dpid_by_name("s7")          # Switch 7 (Host B)
        route_key = (src_dpid, dst_dpid)

        # If already installed, skip re-installing every time topology changes
        if route_key in self.current_routes:
            return

        self.logger.info("Looking for best Initial path (src: %s, dst: %s)...", src_dpid, dst_dpid)

        if src_dpid and dst_dpid and src_dpid in self.net and dst_dpid in self.net:
            if nx.has_path(self.net, src_dpid, dst_dpid):
                candidate_paths = list(nx.all_simple_paths(self.net, src_dpid, dst_dpid))
                if not candidate_paths:
                    return

                feasible_paths = []
                for path in candidate_paths:
                    path_cost = 0.0
                    for i in range(len(path) - 1):
                        u, v = path[i], path[i+1]
                        edge_data = self.net.get_edge_data(u, v)
                        if not edge_data:
                            continue
                        port_no = edge_data[0]['port']
                        link_load = self.link_loads.get((u, port_no), 0.0)
                        port_key = (u, port_no)
                        predicted_risk = 1 if self.consecutive_predictions.get(port_key, 0) >= 2 else 0
                        
                        congestion_weight = 500.0
                        path_cost += link_load + (congestion_weight * predicted_risk)
                    
                    feasible_paths.append((path, path_cost))

                if feasible_paths:
                    best_path, initial_cost = min(feasible_paths, key=lambda x: x[1])
                    self.current_routes[route_key] = (best_path, initial_cost, time.time())

                    readable_path = " -> ".join([self._get_name_by_dpid(node) for node in best_path])
                    # Force a loud, unmistakable log message
                    self.logger.info("##################################################")
                    self.logger.info("SUCCESS: AI-DRIVEN INITIAL PATH INSTALLED: %s", best_path)
                    self.logger.info("ROUTE (Names): %s", readable_path)
                    self.logger.info("##################################################")

                    #Adding to check on which order the flow rules is instaling by myself)
                    named_path = [self._get_name_by_dpid(dpid) for dpid in best_path]
                    self.logger.info("HOP-BY-HOP ROUTE NAMES: %s", named_path)
                    self.install_multi_hop_rules(best_path)

            else:
                self.logger.info("Waiting for all 14 links to connect before installing initial path...")
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
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        # Install Table-Miss Rule (Priority 0) -> Send to Controller
        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER,
                                         ofproto.OFPCML_NO_BUFFER)]
        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
        mod = parser.OFPFlowMod(datapath=datapath, priority=0,
                                match=match, instructions=inst)
        datapath.send_msg(mod)
        self.logger.info("Installed proactive table-miss rule for datapath: %016x", datapath.id)

    def _monitor_worker(self):
        while True:
            hub.sleep(5)  # Poll statistics every 5 seconds
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
            # --- PASS 1: Calculate metrics and run AI model prediction for all ports ---
            for stat in sorted(body, key=attrgetter('port_no')):
                if stat.port_no == ofproto_v1_3.OFPP_LOCAL:
                    continue

                port_name_map = {
                    ("br-ryu-test", 1): "v12a", ("br-ryu-test", 2): "hostA-sw",
                    ("s2", 1): "v25a", ("s2", 2): "v23a", ("s2", 3): "v12b",
                    ("s3", 1): "v23b", ("s3", 2): "v34a",
                    ("s4", 1): "v47a", ("s4", 2): "v34b", ("s4", 3): "v64b",
                    ("s5", 1): "v25b", ("s5", 2): "v56a",
                    ("s6", 1): "v64a", ("s6", 2): "v56b",
                    ("s7", 1): "hostB-sw", ("s7", 2): "v47b"
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

                stats_dict = {
                    "switch": switch_name, "port": port_name,
                    "rx_packets": stat.rx_packets, "tx_packets": stat.tx_packets,
                    "rx_bytes": stat.rx_bytes, "tx_bytes": stat.tx_bytes,
                    "rx_drops": stat.rx_dropped, "tx_drops": stat.tx_dropped,
                    "rx_errors": stat.rx_errors, "tx_errors": stat.tx_errors,
                    "rx_mbps": max(0.0, rx_mbps), "tx_mbps": max(0.0, tx_mbps),
                    "rx_pps": max(0.0, rx_pps), "tx_pps": max(0.0, tx_pps),
                }

                # Ingest telemetry into RFTC buffer for online retraining
                self.rftc.ingest_telemetry(stats_dict, self.features)

                # Prepare DataFrame for Model Inference
                input_df = pd.DataFrame([stats_dict], columns=self.features)
                for col in self.numeric_features:
                    input_df[col] = pd.to_numeric(input_df[col], errors="coerce").fillna(0)

                prediction = self.model.predict(input_df)[0]

                # Apply Persistence Check (Require 2 consecutive positive predictions)
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

            # --- PASS 2: Evaluate Congestion & Trigger Multi-Hop Dynamic Routing ---
            for port_no, info in port_evaluations.items():
                if info["prediction"] == 1:
                    self.logger.warning("PERSISTENT CONGESTION PREDICTED: Switch %s, Port %s (%s)!", 
                                         switch_name, port_no, info["port_name"])
                    
                    # Trigger end-to-end multi-hop path recalculation across the network graph
                    self.evaluate_and_reroute_paths(dpid)

            # Check if RFTC wants to trigger background model retraining
            if self.rftc.should_retrain():
                self.model = self.rftc.retrain_model(
                    self.model, self.features, self.categorical_features, self.numeric_features
                )
        except Exception as e:
            self.logger.error("CRASH in port stats handler: %s", str(e), exc_info=True)

    def evaluate_and_reroute_paths(self, congested_dpid):
        """Fully automated AI-guided path evaluation from Switch 1 to Switch 7 with hysteresis and fallback."""
        src_dpid = self._get_dpid_by_name("br-ryu-test")
        dst_dpid = self._get_dpid_by_name("s7")
        route_key = (src_dpid, dst_dpid)
        
        if not src_dpid or not dst_dpid or not nx.has_path(self.net, src_dpid, dst_dpid):
            return

        candidate_paths = list(nx.all_simple_paths(self.net, src_dpid, dst_dpid))
        feasible_paths = []

        for path in candidate_paths:
            path_cost = 0.0
            for i in range(len(path) - 1):
                u, v = path[i], path[i+1]
                edge_data = self.net.get_edge_data(u, v)
                port_no = edge_data[0]['port']
                
                link_load = self.link_loads.get((u, port_no), 0.0)
                port_key = (u, port_no)
                predicted_risk = 1 if self.consecutive_predictions.get(port_key, 0) >= 2 else 0
                
                congestion_weight = 500.0
                path_cost += link_load + (congestion_weight * predicted_risk)

            feasible_paths.append((path, path_cost))

        if not feasible_paths:
            self.logger.warning("No alternative paths available. Continuing with current congestion state.")
            return

        best_path, new_cost = min(feasible_paths, key=lambda x: x[1])
        
        current_time = time.time()
        cooldown_period = 10  # seconds

        if route_key in self.current_routes:
            old_path, old_cost, last_change = self.current_routes[route_key]
            
            # Cooldown check
            if current_time - last_change < cooldown_period:
                return

            # 10% Improvement Check; if insufficient improvement, maintain current path
            if old_cost > 0:
                improvement = (old_cost - new_cost) / old_cost
                if improvement < 0.10:
                    return # Insufficient improvement, keep current path

        # Update active route and install OpenFlow rules across the multi-hop path
        self.current_routes[route_key] = (best_path, new_cost, current_time)
        self.install_multi_hop_rules(best_path)

    def install_multi_hop_rules(self, path):
        """Installs OpenFlow forwarding rules across all switches in the chosen path."""
        self.logger.warning("INSTALLING MULTI-HOP PATH RULES: %s", path)
        for i in range(len(path) - 1):
            curr_dpid = path[i]
            next_dpid = path[i+1]
            
            edge_data = self.net.get_edge_data(curr_dpid, next_dpid)
            out_port = edge_data[0]['port']
            
            node_data = self.net.nodes[curr_dpid]
            if 'datapath' in node_data:
                datapath = node_data['datapath']
                parser = datapath.ofproto_parser
                
                match = parser.OFPMatch()
                actions = [parser.OFPActionOutput(out_port)]
                inst = [parser.OFPInstructionActions(ofproto_v1_3.OFPIT_APPLY_ACTIONS, actions)]
                
                mod = parser.OFPFlowMod(
                    datapath=datapath,
                    priority=2000,
                    match=match,
                    instructions=inst,
                    command=ofproto_v1_3.OFPFC_ADD
                )
                datapath.send_msg(mod)

    def _get_switch_name(self, dpid):
        dpid_hex = f"{dpid:016x}"
        dpid_map = {
            "000012bb705e754d": "br-ryu-test",
            "00006a95cb223a46": "br-test",
            "000002f9b0b2454f": "s2",
            "0000fa80ebe1194c": "s3",
            "0000c2b726c53740": "s4",
            "0000868028d9e64b": "s5",
            "00002a97c6f41c48": "s6",
            "000026c5b0a4f34c": "s7"
        }
        return dpid_map.get(dpid_hex, f"s{dpid}")

    def _get_dpid_by_name(self, name):
        name_map = {
            "br-ryu-test": 0x12bb705e754d,
            "br-test": 0x6a95cb223a46,
            "s2": 0x02f9b0b2454f,
            "s3": 0xfa80ebe1194c,
            "s4": 0xc2b726c53740,
            "s5": 0x868028d9e64b,
            "s6": 0x2a97c6f41c48,
            "s7": 0x26c5b0a4f34c
        }

        
        hex_val = name_map.get(name)
        return hex_val if hex_val else None

    # def _get_name_by_dpid(self, dpid):
    #     """Translates an integer or hex DPID back into its human-readable switch name."""
    #     # Convert integer DPID to 16-character hex string if necessary
    #     if isinstance(dpid, int):
    #         dpid_str = f"{dpid:016x}"
    #     else:
    #         dpid_str = str(dpid).zfill(16)

    #     dpid_to_name = {
    #         "000012bb705e754d": "br-ryu-test",
    #         "000002f9b0b2454f": "s2",
    #         "0000fa80ebe1194c": "s3",
    #         "0000c2b726c53740": "s4",
    #         "0000868028d9e64b": "s5",
    #         "00002a97c6f41c48": "s6",
    #         "000026c5b0a4f34c": "s7"
    #     }
    #     return dpid_to_name.get(dpid_str, f"unknown({dpid})")

    def _get_name_by_dpid(self, dpid):
        """Translates an integer DPID back to its name using your existing mapping."""
        name_map = {
            "br-ryu-test": 0x12bb705e754d,
            "br-test": 0x6a95cb223a46,
            "s2": 0x02f9b0b2454f,
            "s3": 0xfa80ebe1194c,
            "s4": 0xc2b726c53740,
            "s5": 0x868028d9e64b,
            "s6": 0x2a97c6f41c48,
            "s7": 0x26c5b0a4f34c
        }
        # Invert the dictionary (value -> key) dynamically
        reverse_map = {v: k for k, v in name_map.items()}
        return reverse_map.get(dpid, f"unknown({dpid})")

  