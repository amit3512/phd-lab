# Copyright (C) 2026 Edge Intelligence SDN Project.
# Fully Integrated 14-Step Dynamic Multi-Hop Routing Script with ML Prediction & RFTC (Updated for s1-s10)

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
from rftc_updated_1copy import RealTimeTrainingComponent

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

        # Start background monitoring & inference loop
        self.monitor_thread = hub.spawn(self._monitor_worker)

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
                capacity=1000.0  # Default link capacity in Mbps
            )
        self.logger.info("Topology Graph Updated. Total Nodes (Switches): %d | Total Edges (Links): %d", 
                         len(self.net.nodes), len(self.net.edges))
        
        # Proactively install initial AI-driven path from Switch 1 to Switch 7 at startup
        self.install_initial_path_at_startup()

    def install_initial_path_at_startup(self):
        """Computes and installs the best initial path from Switch 1 to Switch 7 once graph is ready."""
        src_dpid = self._get_dpid_by_name("s1")
        dst_dpid = self._get_dpid_by_name("s7")
        route_key = (src_dpid, dst_dpid)

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
                    self.logger.info("##################################################")
                    self.logger.info("SUCCESS: AI-DRIVEN INITIAL PATH INSTALLED: %s", best_path)
                    self.logger.info("ROUTE (Names): %s", readable_path)
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
            for stat in sorted(body, key=attrgetter('port_no')):
                if stat.port_no == ofproto_v1_3.OFPP_LOCAL:
                    continue

                # Expanded Port Mapping to cover s1 through s10 and veth definitions
                port_name_map = {
                    ("s1", 1): "v12a", ("s1", 2): "hostA-sw",
                    ("s2", 1): "v12b", ("s2", 2): "v23a", ("s2", 3): "v25a", ("s2", 4): "v28a", ("s2", 5): "v210a",
                    ("s3", 1): "v23b", ("s3", 2): "v34a", ("s3", 3): "v38a",
                    ("s4", 1): "v34b", ("s4", 2): "v47a", ("s4", 3): "v64b", ("s4", 4): "v410a", ("s4", 5): "v49a",
                    ("s5", 1): "v25b", ("s5", 2): "v56a",
                    ("s6", 1): "v56b", ("s6", 2): "v64a", ("s6", 3): "v69a",
                    ("s7", 1): "v47b", ("s7", 2): "hostB-sw",
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

                stats_dict = {
                    "switch": switch_name, "port": port_name,
                    "rx_packets": stat.rx_packets, "tx_packets": stat.tx_packets,
                    "rx_bytes": stat.rx_bytes, "tx_bytes": stat.tx_bytes,
                    "rx_drops": stat.rx_dropped, "tx_drops": stat.tx_dropped,
                    "rx_errors": stat.rx_errors, "tx_errors": stat.tx_errors,
                    "rx_mbps": max(0.0, rx_mbps), "tx_mbps": max(0.0, tx_mbps),
                    "rx_pps": max(0.0, rx_pps), "tx_pps": max(0.0, tx_pps),
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
                    self.logger.warning("PERSISTENT CONGESTION PREDICTED: Switch %s, Port %s (%s)!", 
                                         switch_name, port_no, info["port_name"])
                    self.evaluate_and_reroute_paths(dpid)

            if self.rftc.should_retrain():
                self.model = self.rftc.retrain_model(
                    self.model, self.features, self.categorical_features, self.numeric_features
                )
        except Exception as e:
            self.logger.error("CRASH in port stats handler: %s", str(e), exc_info=True)

    def evaluate_and_reroute_paths(self, congested_dpid):
        src_dpid = self._get_dpid_by_name("s1")
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
            return

        best_path, new_cost = min(feasible_paths, key=lambda x: x[1])
        
        current_time = time.time()
        cooldown_period = 10

        if route_key in self.current_routes:
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
        self.logger.warning("CONGESTION DETECTED: NEW AI-DRIVEN PATH INSTALLED: %s", best_path)
        self.logger.warning("NEW ROUTE (Names): %s", readable_path)
        self.logger.warning("##################################################")

        named_path = [self._get_name_by_dpid(dpid) for dpid in best_path]
        self.logger.warning("HOP-BY-HOP ROUTE NAMES: %s", named_path)
        self.install_multi_hop_rules(best_path)

    def install_multi_hop_rules(self, path):
        self.logger.warning("INSTALLING BIDIRECTIONAL MULTI-HOP PATH RULES: %s", path)
        if not path or len(path) < 2:
            return

        dpid_s1 = self._get_dpid_by_name("s1")
        dpid_s7 = self._get_dpid_by_name("s7")

        HOST_A_PORT = 2  # hostA-sw on s1
        # HOST_B_PORT = 1  # hostB-sw on s7
        # HOST_B_PORT = 2  # hostB-sw on s7


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

            if curr_dpid == dpid_s1:
                in_port = HOST_A_PORT
            else:
                prev_dpid = path[i-1]
                prev_edge_dict = self.net.get_edge_data(prev_dpid, curr_dpid)
                if not prev_edge_dict:
                    continue
                prev_edge = list(prev_edge_dict.values())[0]
                in_port = prev_edge.get('dst_port')
                if in_port is None:
                    continue

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

        # Final hop on s7
        last_dpid = path[-1]
        last_datapath = self.datapaths.get(last_dpid)
        if last_datapath and last_dpid == dpid_s7:
            parser = last_datapath.ofproto_parser
            ofproto = last_datapath.ofproto

            prev_dpid = path[-2]
            edge_dict = self.net.get_edge_data(prev_dpid, last_dpid)
            if edge_dict:
                edge_data = list(edge_dict.values())[0]
                in_port_from_core = edge_data.get('dst_port')
                if in_port_from_core is not None:
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

            if curr_dpid == dpid_s7:
                in_port = HOST_B_PORT
            else:
                prev_dpid = rev_path[i-1]
                prev_edge_dict = self.net.get_edge_data(prev_dpid, curr_dpid)
                if not prev_edge_dict:
                    continue
                prev_edge = list(prev_edge_dict.values())[0]
                in_port = prev_edge.get('dst_port')
                if in_port is None:
                    continue

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

        # Final hop on s1 for reverse path
        last_dpid_rev = rev_path[-1]
        last_datapath_rev = self.datapaths.get(last_dpid_rev)
        if last_datapath_rev and last_dpid_rev == dpid_s1:
            parser = last_datapath_rev.ofproto_parser
            ofproto = last_datapath_rev.ofproto

            prev_dpid = rev_path[-2]
            edge_dict = self.net.get_edge_data(prev_dpid, last_dpid_rev)
            if edge_dict:
                edge_data = list(edge_dict.values())[0]
                in_port_from_core = edge_data.get('dst_port')
                if in_port_from_core is not None:
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
            "00002e67fb1c3f4a": "s1",
            "0000aa0ad704974e": "s2",
            "0000aab0a554e144": "s3",
            "0000025d98ecd84e": "s4",
            "0000e26c45724a4e": "s5",
            "0000aeb5f54b6243": "s6",
            "0000428ded77364d": "s7",
            "0000560b24a8a143": "s8",
            "0000da8ceff6e245": "s9",
            "00005e8c84b9c046": "s10"
        }
        return dpid_map.get(dpid_hex, f"s{dpid}")

    def _get_dpid_by_name(self, name):
        name_map = {
            "s1": 0x2e67fb1c3f4a,
            "s2": 0xaa0ad704974e,
            "s3": 0xaab0a554e144,
            "s4": 0x25d98ecd84e,
            "s5": 0xe26c45724a4e,
            "s6": 0xaeb5f54b6243,
            "s7": 0x428ded77364d,
            "s8": 0x560b24a8a143,
            "s9": 0xda8ceff6e245,
            "s10": 0x5e8c84b9c046
        }
        hex_val = name_map.get(name)
        return hex_val if hex_val else None

    def _get_name_by_dpid(self, dpid):
        name_map = {
            "s1": 0x2e67fb1c3f4a,
            "s2": 0xaa0ad704974e,
            "s3": 0xaab0a554e144,
            "s4": 0x25d98ecd84e,
            "s5": 0xe26c45724a4e,
            "s6": 0xaeb5f54b6243,
            "s7": 0x428ded77364d,
            "s8": 0x560b24a8a143,
            "s9": 0xda8ceff6e245,
            "s10": 0x5e8c84b9c046
        }
        reverse_map = {v: k for k, v in name_map.items()}
        return reverse_map.get(dpid, f"unknown({dpid})")