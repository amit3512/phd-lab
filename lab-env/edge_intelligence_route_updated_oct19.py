import time
import joblib
import pandas as pd
from operator import attrgetter
from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import MAIN_DISPATCHER, DEAD_DISPATCHER, CONFIG_DISPATCHER, set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib import hub

# Import your Real-Time Training Component
from rftc_oct19_updated import RealTimeTrainingComponent
from joblib import parallel_backend

class EdgeIntelligenceRoute(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(EdgeIntelligenceRoute, self).__init__(*args, **kwargs)
        self.datapaths = {}
        # Add a dictionary to store past stats for delta calculations
        self.prev_stats = {} # Key: (dpid, port_no), Value: (timestamp, rx_bytes, tx_bytes, rx_packets, tx_packets)

        self.active_reroutes = set() # Tracks (dpid, congested_port_no)

        # Load the trained AI model bundle
        self.model_path = "model_built_oct21_updated.joblib"
        self.logger.info("Loading AI model from %s...", self.model_path)
        bundle = joblib.load(self.model_path)
        # Force the underlying estimator to run single-threaded (no multiprocessing)
        # 2. Configure n_jobs = 1 right after loading to prevent eventlet/multiprocessing crashes
        
        self.model = bundle["model"]
        self.features = bundle["features"]
        self.logger.info('Features.......................:', bundle["features"])
        self.numeric_features = bundle["numeric_features"]
        self.categorical_features = bundle["categorical_features"]

        # 3. NOW force the estimator to run single-threaded (n_jobs=1)
        if hasattr(self.model, 'set_params'):
            try:
                self.model.set_params(n_jobs=1)
            except Exception:
                pass

        if hasattr(self.model, 'steps'):
            for name, step in self.model.steps:
                if hasattr(step, 'n_jobs'):
                    step.n_jobs = 1
        
        # Initialize the RFTC module for online buffering/retraining
        # self.rftc = RealTimeTrainingComponent(self.logger)
        self.logger.info("Initializing RFTC...")
        self.rftc = RealTimeTrainingComponent(self.logger)
        self.logger.info("RFTC initialized successfully.")

        # Start background monitoring & inference loop
        self.monitor_thread = hub.spawn(self._monitor_worker)

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
        self.logger.info("Installed proactive table-miss rule for datapath: %016x %s", datapath.id, mod)
        

    # ===================================


    def _monitor_worker(self):
        while True:
            hub.sleep(5)  # Poll every 5 seconds
            for dpid, datapath in list(self.datapaths.items()):
                self._request_port_stats(datapath)

    def _request_port_stats(self, datapath):
        self.logger.info("Requesting port stats for datapath: %016x", datapath.id) # <-- Add this
        ofp = datapath.ofproto
        parser = datapath.ofproto_parser
        req = parser.OFPPortStatsRequest(datapath, 0, ofp.OFPP_ANY)
        datapath.send_msg(req)

    # @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    # def _port_stats_reply_handler(self, ev):
    #     self.logger.info("Received port stats reply from datapath: %016x", ev.msg.datapath.id) # <-- Add this
    #     body = ev.msg.body
    #     dpid = ev.msg.datapath.id
    #     switch_name = self._get_switch_name(dpid) # Map your DPID to switch string ('s2', 's7', etc.)
    #     current_time = time.time()

    #     port_evaluations = {}

    #     try:
    #         for stat in sorted(body, key=attrgetter('port_no')):
    #             if stat.port_no == ofproto_v1_3.OFPP_LOCAL:
    #                 continue

    #             # port_name_map = {
    #             #     ("br-ryu-test", 1): "v12a",
    #             #     ("br-ryu-test", 2): "hostA-sw",
    #             #     ("s2", 1): "v25a",
    #             #     ("s2", 2): "v23a",
    #             #     ("s2", 3): "v12b",
    #             #     ("s3", 1): "v23b",
    #             #     ("s3", 2): "v34a",
    #             #     ("s4", 1): "v47a",
    #             #     ("s4", 2): "v34b",
    #             #     ("s4", 3): "v64b",
    #             #     ("s5", 1): "v25b",
    #             #     ("s5", 2): "v56a",
    #             #     ("s6", 1): "v64a",
    #             #     ("s6", 2): "v56b",
    #             #     ("s7", 1): "hostB-sw",
    #             #     ("s7", 2): "v47b"
    #             # }
    #             port_name_map = {
    #                 ("br-ryu-test", 1): "v12a", ("br-ryu-test", 2): "hostA-sw",
    #                 ("s2", 1): "v25a", ("s2", 2): "v23a", ("s2", 3): "v12b",
    #                 ("s3", 1): "v23b", ("s3", 2): "v34a",
    #                 ("s4", 1): "v47a", ("s4", 2): "v34b", ("s4", 3): "v64b",
    #                 ("s5", 1): "v25b", ("s5", 2): "v56a",
    #                 ("s6", 1): "v64a", ("s6", 2): "v56b",
    #                 ("s7", 1): "hostB-sw", ("s7", 2): "v47b"
    #             }
    #             # port_name = f"port_{stat.port_no}"  # Or map via your port name dictionary
    #             port_name = port_name_map.get((switch_name, stat.port_no), f"port_{stat.port_no}")
    #             key = (dpid, stat.port_no)
    #             # Construct stats dictionary matching training schema

    #             # Default rates
    #             # rx_mbps = 0.0
    #             # tx_mbps = 0.0
    #             # rx_pps = 0.0
    #             # tx_pps = 0.0
    #             rx_mbps, tx_mbps, rx_pps, tx_pps = 0.0, 0.0, 0.0, 0.0

    #             # Calculate delta if previous stats exist
    #             if key in self.prev_stats:
    #                 prev_time, prev_rx_bytes, prev_tx_bytes, prev_rx_packets, prev_tx_packets = self.prev_stats[key]
    #                 time_interval = current_time - prev_time

    #                 if time_interval > 0:
    #                     # Bytes to Megabits per second (Mbps): (delta_bytes * 8) / (interval * 1,000,000)
    #                     rx_mbps = ((stat.rx_bytes - prev_rx_bytes) * 8) / (time_interval * 1000000.0)
    #                     tx_mbps = ((stat.tx_bytes - prev_tx_bytes) * 8) / (time_interval * 1000000.0)
                        
    #                     # Packets per second (pps)
    #                     rx_pps = (stat.rx_packets - prev_rx_packets) / time_interval
    #                     tx_pps = (stat.tx_packets - prev_tx_packets) / time_interval
    #                     self.logger.info("DEBUG [%s %s] Interval: %.2fs | Calc Mbps: %.4f", 
    #                                  switch_name, port_name, time_interval, rx_mbps)
    #             # Save current stats for the next interval calculation
    #             self.prev_stats[key] = (current_time, stat.rx_bytes, stat.tx_bytes, stat.rx_packets, stat.tx_packets)
                
    #             stats_dict = {
    #             "switch": switch_name,
    #             "port": port_name,
    #             "rx_packets": stat.rx_packets,
    #             "tx_packets": stat.tx_packets,
    #             "rx_bytes": stat.rx_bytes,
    #             "tx_bytes": stat.tx_bytes,
    #             "rx_drops": stat.rx_dropped,
    #             "tx_drops": stat.tx_dropped,
    #             "rx_errors": stat.rx_errors,
    #             "tx_errors": stat.tx_errors,
    #             "rx_mbps": max(0.0, rx_mbps),
    #             "tx_mbps": max(0.0, tx_mbps),
    #             "rx_pps": max(0.0, rx_pps),
    #             "tx_pps": max(0.0, tx_pps),
    #         }
    #             self.logger.info("Switch: %s | Port: %s | RX Bytes: %s | RX Mbps: %.2f", 
    #                          switch_name, port_name, stat.rx_bytes, rx_mbps)

    #             # 1. Ingest telemetry into RFTC buffer for potential retraining
    #             self.logger.info("Ingesting telemetry for %s, %s", switch_name, port_name)
    #             self.rftc.ingest_telemetry(stats_dict, self.features)

    #             # 2. Run Real-Time AI Model Prediction
    #             self.logger.info("Running model prediction...")
    #             input_df = pd.DataFrame([stats_dict], columns=self.features)
    #             self.logger.info(f"Presenting model Features:\n{input_df}")
    #             for col in self.numeric_features:
    #                 input_df[col] = pd.to_numeric(input_df[col], errors="coerce").fillna(0)

    #             prediction = self.model.predict(input_df)[0]
    #             self.logger.info("Processed %s, %s -> Prediction: %s", switch_name, port_name, prediction)
    #             # self.logger.info("Prediction result: %s", prediction)

    #             # Store evaluation info for Pass 2
    #             port_evaluations[stat.port_no] = {
    #                 "port_no": stat.port_no,
    #                 "port_name": port_name,
    #                 "prediction": prediction,
    #                 "total_load": rx_mbps + tx_mbps
    #             }

    #             if prediction == 1:
    #                 self.logger.warning("CONGESTION DETECTED: Switch %s, Port %s! Triggering reroute...", switch_name, port_name)
    #                 self.trigger_dynamic_reroute(ev.msg.datapath, stat.port_no)

    #         # Check if RFTC wants to trigger background model retraining
    #         if self.rftc.should_retrain():
    #             self.model = self.rftc.retrain_model(
    #                 self.model, self.features, self.categorical_features, self.numeric_features
    #             )
    #     except Exception as e:
    #         self.logger.error("CRASH in port stats handler: %s", str(e), exc_info=True)

    # # def trigger_dynamic_reroute(self, datapath, congested_port_no):
    # #     """
    # #     Executes OpenFlow table modifications to modify paths or drop/reroute traffic.
    # #     """
    # #     parser = datapath.ofproto_parser
    # #     ofp = datapath.ofproto

    # #     # Example: Modify flow table rule to redirect traffic away from the congested port
    # #     match = parser.OFPMatch(in_port=congested_port_no)
        
    # #     # Define actions (e.g., output to an alternate fallback port or group table)
    # #     actions = [parser.OFPActionOutput(ofp.OFPP_NORMAL)] # Adjust action to your alternate port
        
    # #     inst = [parser.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, actions)]
    # #     mod = parser.OFPFlowMod(
    # #         datapath=datapath,
    # #         priority=1000,
    # #         match=match,
    # #         instructions=inst,
    # #         command=ofp.OFPFC_ADD
    # #     )
    # #     datapath.send_msg(mod)
    # #     self.logger.info("Flow table updated successfully for datapath %016x to mitigate congestion.", datapath.id)

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def _port_stats_reply_handler(self, ev):
        body = ev.msg.body
        dpid = ev.msg.datapath.id
        switch_name = self._get_switch_name(dpid)
        current_time = time.time()
        
        port_evaluations = {}
        
        try:
            # --- PASS 1: Calculate metrics and predict status for ALL ports on this switch ---
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

                stats_dict = {
                    "switch": switch_name, "port": port_name,
                    "rx_packets": stat.rx_packets, "tx_packets": stat.tx_packets,
                    "rx_bytes": stat.rx_bytes, "tx_bytes": stat.tx_bytes,
                    "rx_drops": stat.rx_dropped, "tx_drops": stat.tx_dropped,
                    "rx_errors": stat.rx_errors, "tx_errors": stat.tx_errors,
                    "rx_mbps": max(0.0, rx_mbps), "tx_mbps": max(0.0, tx_mbps),
                    "rx_pps": max(0.0, rx_pps), "tx_pps": max(0.0, tx_pps),
                }
                self.logger.info("Stats Dict...................: %s", stats_dict)

                # Run model prediction
                input_df = pd.DataFrame([stats_dict], columns=self.features)
                for col in self.numeric_features:
                    input_df[col] = pd.to_numeric(input_df[col], errors="coerce").fillna(0)

                # Print feature importances or values causing the trigger
                self.logger.info("Input features for prediction:\n%s", input_df.to_dict(orient='records'))
                if hasattr(self.model, "feature_importances_"):
                    self.logger.info("Top features: %s", sorted(zip(self.features, self.model.feature_importances_3 if hasattr(self.model, 'feature_importances_') else []), key=lambda x: x[1], reverse=True)[:5])
                
                self.logger.info("Model expects these features: %s", self.features)
                prediction = self.model.predict(input_df)[0]

                # Store evaluation info for Pass 2
                port_evaluations[stat.port_no] = {
                    "port_no": stat.port_no,
                    "port_name": port_name,
                    "prediction": prediction,
                    "total_load": rx_mbps + tx_mbps
                }

            # --- PASS 2: Handle congestion and select the best alternate port ---
            for port_no, info in port_evaluations.items():
                if info["prediction"] == 1:
                    self.logger.warning("CONGESTION DETECTED: Switch %s, Port %s (%s)!", 
                                         switch_name, port_no, info["port_name"])
                    
                    # Find all uncongested ports on this same switch
                    uncongested_candidates = [
                        p for p_no, p in port_evaluations.items() 
                        if p["prediction"] == 0 and p_no != port_no
                    ]

                    if uncongested_candidates:
                        # Pick the uncongested port with the LOWEST total load (least used)
                        best_alternate = min(uncongested_candidates, key=lambda x: x["total_load"])
                        alternate_port_no = best_alternate["port_no"]
                        
                        self.logger.info("Selected optimal alternate port: %s (%s) with load %.2f Mbps", 
                                         alternate_port_no, best_alternate["port_name"], best_alternate["total_load"])
                        
                        # Trigger the strict OpenFlow reroute rule (Priority 2000)
                        self.trigger_dynamic_reroute(ev.msg.datapath, port_no, alternate_port_no)
                    else:
                        self.logger.error("No alternative uncongested ports available on switch %s!", switch_name)

            if self.rftc.should_retrain():
                self.model = self.rftc.retrain_model(
                    self.model, self.features, self.categorical_features, self.numeric_features
                )
        except Exception as e:
            self.logger.error("CRASH in port stats handler: %s", str(e), exc_info=True)

    def trigger_dynamic_reroute(self, datapath, congested_port_no, alternate_port_no):
        """
        Executes strict OpenFlow emergency rerouting with Priority 2000 
        directing traffic to the calculated optimal least-used port.
        """
        parser = datapath.ofproto_parser
        ofp = datapath.ofproto

        match = parser.OFPMatch(in_port=congested_port_no)
        actions = [parser.OFPActionOutput(alternate_port_no)]
        inst = [parser.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, actions)]
        
        mod = parser.OFPFlowMod(
            datapath=datapath,
            priority=2000,
            match=match,
            instructions=inst,
            command=ofp.OFPFC_ADD
        )
        datapath.send_msg(mod)
        self.logger.warning(
            "EMERGENCY REROUTE [Priority 2000]: Datapath %016x redirected port %s -> port %s (Controller-Managed)", 
            datapath.id, congested_port_no, alternate_port_no
        )
    
    
    def _get_switch_name(self, dpid):
        # Map your OpenFlow Datapath ID (DPID) to your topology names ('br-ryu-test', 's2', etc.)
        # dpid_map = {
        #     1: "br-ryu-test",
        #     2: "s2",
        #     3: "s3",
        #     4: "s4",
        #     5: "s5",
        #     6: "s6",
        #     7: "s7"
        # }
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
        # return dpid_map.get(dpid, f"s{dpid}")
        return dpid_map.get(dpid_hex, f"s{dpid}")