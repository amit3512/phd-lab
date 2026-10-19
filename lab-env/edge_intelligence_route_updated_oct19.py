import time
import joblib
import pandas as pd
from operator import attrgetter
from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import MAIN_DISPATCHER, DEAD_DISPATCHER, set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib import hub

# Import your Real-Time Training Component
from rftc import RealTimeTrainingComponent

class EdgeIntelligenceRoute(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(EdgeIntelligenceRoute, self).__init__(*args, **kwargs)
        self.datapaths = {}
        
        # Load the trained AI model bundle
        self.model_path = "model_built_oct19.joblib"
        self.logger.info("Loading AI model from %s...", self.model_path)
        bundle = joblib.load(self.model_path)
        self.model = bundle["model"]
        self.features = bundle["features"]
        self.numeric_features = bundle["numeric_features"]
        self.categorical_features = bundle["categorical_features"]
        
        # Initialize the RFTC module for online buffering/retraining
        self.rftc = RealTimeTrainingComponent(self.logger)

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

    def _monitor_worker(self):
        while True:
            hub.sleep(5)  # Poll every 5 seconds
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
        switch_name = self._get_switch_name(dpid) # Map your DPID to switch string ('s2', 's7', etc.)

        for stat in sorted(body, key=attrgetter('port_no')):
            if stat.port_no == ofproto_v1_3.OFPP_LOCAL:
                continue

            port_name = f"port_{stat.port_no}"  # Or map via your port name dictionary

            # Construct stats dictionary matching training schema
            stats_dict = {
                "switch": switch_name,
                "port": port_name,
                "rx_packets": stat.rx_packets,
                "tx_packets": stat.tx_packets,
                "rx_bytes": stat.rx_bytes,
                "tx_bytes": stat.tx_bytes,
                "rx_drops": stat.rx_dropped,
                "tx_drops": stat.tx_dropped,
                "rx_errors": stat.rx_errors,
                "tx_errors": stat.tx_errors,
                "rx_mbps": 0.0,  # Calculate delta-based MBps if tracking history
                "tx_mbps": 0.0,
                "rx_pps": 0.0,
                "tx_pps": 0.0,
            }

            # 1. Ingest telemetry into RFTC buffer for potential retraining
            self.rftc.ingest_telemetry(stats_dict, self.features)

            # 2. Run Real-Time AI Model Prediction
            input_df = pd.DataFrame([stats_dict], columns=self.features)
            for col in self.numeric_features:
                input_df[col] = pd.to_numeric(input_df[col], errors="coerce").fillna(0)

            prediction = self.model.predict(input_df)[0]

            if prediction == 1:
                self.logger.warning("CONGESTION DETECTED: Switch %s, Port %s! Triggering reroute...", switch_name, port_name)
                self.trigger_dynamic_reroute(ev.msg.datapath, stat.port_no)

        # Check if RFTC wants to trigger background model retraining
        if self.rftc.should_retrain():
            self.model = self.rftc.retrain_model(
                self.model, self.features, self.categorical_features, self.numeric_features
            )

    def trigger_dynamic_reroute(self, datapath, congested_port_no):
        """
        Executes OpenFlow table modifications to modify paths or drop/reroute traffic.
        """
        parser = datapath.ofproto_parser
        ofp = datapath.ofproto

        # Example: Modify flow table rule to redirect traffic away from the congested port
        match = parser.OFPMatch(in_port=congested_port_no)
        
        # Define actions (e.g., output to an alternate fallback port or group table)
        actions = [parser.OFPActionOutput(ofp.OFPP_NORMAL)] # Adjust action to your alternate port
        
        inst = [parser.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, actions)]
        mod = parser.OFPFlowMod(
            datapath=datapath,
            priority=1000,
            match=match,
            instructions=inst,
            command=ofp.OFPFC_ADD
        )
        datapath.send_msg(mod)
        self.logger.info("Flow table updated successfully for datapath %016x to mitigate congestion.", datapath.id)

    def _get_switch_name(self, dpid):
        # Map your OpenFlow Datapath ID (DPID) to your topology names ('br-ryu-test', 's2', etc.)
        dpid_map = {
            1: "br-ryu-test",
            2: "s2",
            3: "s3",
            4: "s4",
            5: "s5",
            6: "s6",
            7: "s7"
        }
        return dpid_map.get(dpid, f"s{dpid}")