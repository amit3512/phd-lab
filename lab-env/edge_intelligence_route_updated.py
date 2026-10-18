import eventlet
eventlet.monkey_patch()

import time
import collections
from pathlib import Path
import joblib
import pandas as pd
from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
from ryu.lib import hub
from ryu.ofproto import ofproto_v1_3

# Import the Real-Time Training Component
from rftc import RealTimeTrainingComponent

# Topology and Role Definitions for the 7-Switch Fabric
REQUIRED_ROLES = ["br-ryu-test", "s2", "s3", "s4", "s5", "s6", "s7"]

# IP Addresses for the data flow rules
SRC = "192.168.100.1"
DST = "192.168.100.2"

# Model File Path and Default Features
#MODEL_FILE = Path("congestion_rf.joblib")
MODEL_FILE = Path("model_built.joblib")
FEATURES = ["rx_bytes", "tx_bytes", "rx_dropped", "tx_dropped"]

# Primary path routing hops: (switch_role, in_port_name, out_port_name)
PRIMARY_FORWARD = [
    ("br-ryu-test", "hostA-sw", "v12a"),
    ("s2", "v12b", "v23a"),
    ("s3", "v23b", "v34a"),
    ("s4", "v34b", "v47a"),
    ("s7", "v47b", "hostB-sw"),
]

# Alternate path routing hops (routing through s5/s6 fabric segment)
ALTERNATE_FORWARD = [
    ("br-ryu-test", "hostA-sw", "v12a"),
    ("s2", "v12b", "v25a"),
    ("s5", "v25b", "v56a"),
    ("s6", "v56b", "v64a"),
    ("s4", "v64b", "v47a"),
    ("s7", "v47b", "hostB-sw"),
]

# PRIORITY = 500
PRIORITY = 65535
# PRIORITY = 15790320
COOKIE = 0xEA100001
COOLDOWN_SECONDS = 10
POLL_SECONDS = 5


class ForceEdgeAIRyu(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(ForceEdgeAIRyu, self).__init__(*args, **kwargs)
        self.datapaths = {}  # dpid -> datapath object
        self.port_names = {}  # dpid -> {port_name: port_no}
        self.roles = {}  # role_name -> dpid
        self.barrier_xids = {}  # (dpid, xid) -> route_name
        self.pending_barriers = set()

        # Telemetry cache and model attributes
        self.port_stats_history = collections.defaultdict(dict)
        self.ai_model = None
        self.model_features = FEATURES

        self.current_route = None
        self.last_change = 0.0
        self.initial_install_attempted = False

        # Load the Random Forest model using the robust dictionary bundle parser
        self._load_model()

        # Initialize the Real-Time Training Component (RFTC)
        self.rftc = RealTimeTrainingComponent(
            logger=self.logger, 
            max_buffer_size=1000, 
            retrain_interval=12
        )

        # Spawn background monitoring and AI evaluation loop safely
        self.monitor_thread = hub.spawn(self._monitor)

    def _load_model(self):
        """Robustly loads model bundle (dictionary or raw model) from disk."""
        if not MODEL_FILE.exists():
            self.logger.error(
                "MODEL_NOT_FOUND path=%s; threshold/simulation fallback active",
                MODEL_FILE.resolve(),
            )
            return

        try:
            bundle = joblib.load(MODEL_FILE)
            if isinstance(bundle, dict):
                self.ai_model = bundle.get("model")
                self.model_features = bundle.get("features", FEATURES)
            else:
                self.ai_model = bundle
                self.model_features = FEATURES

            if self.ai_model is None:
                self.logger.error(
                    "MODEL_LOAD_FAILED: the file contains no 'model' entry"
                )
                return

            classes = getattr(self.ai_model, "classes_", None)
            if classes is None and hasattr(self.ai_model, "named_steps"):
                final_step = list(self.ai_model.named_steps.values())[-1]
                classes = getattr(final_step, "classes_", None)

            self.logger.info(
                "MODEL_LOADED path=%s features=%s classes=%s",
                MODEL_FILE.resolve(),
                self.model_features,
                classes,
            )

            if classes is not None and len(classes) < 2:
                self.logger.warning(
                    "MODEL_HAS_ONLY_ONE_CLASS classes=%s; "
                    "model predictions alone cannot distinguish congestion",
                    classes,
                )

        except Exception:
            self.logger.exception("MODEL_LOAD_FAILED")

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        dp = ev.msg.datapath
        self.datapaths[dp.id] = dp
        self.logger.info("SWITCH_FEATURES_RECEIVED dpid=%016x", dp.id)

        parser = dp.ofproto_parser
        req = parser.OFPPortDescStatsRequest(dp)
        dp.send_msg(req)

    @set_ev_cls(ofp_event.EventOFPPortDescStatsReply, MAIN_DISPATCHER)
    def port_desc_stats_reply_handler(self, ev):
        dp = ev.msg.datapath
        dpid = dp.id
        
        ports = {}
        for p in ev.msg.body:
            name = p.name
            if isinstance(name, bytes):
                name = name.decode('utf-8', errors='ignore')
            ports[name] = p.port_no
            
        self.port_names[dpid] = ports
        self.logger.info("DATAPATH_ACTIVE dpid=%016x", dpid)
        self.logger.info("PORTS_DISCOVERED dpid=%016x ports=%s", dpid, ports)
        self._assign_roles()

    def _assign_roles(self):
        for dpid, ports in self.port_names.items():
            if "hostA-sw" in ports and "v12a" in ports:
                self.roles["br-ryu-test"] = dpid
            elif "hostB-sw" in ports and "v47b" in ports:
                self.roles["s7"] = dpid
            elif "v23b" in ports and "v34a" in ports:
                self.roles["s3"] = dpid
            elif "v34b" in ports and "v47a" in ports:
                self.roles["s4"] = dpid
            elif "v56a" in ports and "v25b" in ports:
                self.roles["s5"] = dpid
            elif "v56b" in ports and "v64a" in ports:
                self.roles["s6"] = dpid
            elif "v12b" in ports and "v23a" in ports:
                self.roles["s2"] = dpid

        missing = [r for r in REQUIRED_ROLES if r not in self.roles]
        identified = list(self.roles.keys())

        if missing:
            self.logger.info("WAITING_FOR_SWITCH_ROLES missing=%s identified=%s", missing, identified)
        else:
            self.logger.info("ALL_ROLES_IDENTIFIED roles=%s", {k: format(v, "016x") for k, v in self.roles.items()})

    def _roles_ready(self):
        return all(r in self.roles and self.roles[r] in self.datapaths for r in REQUIRED_ROLES)

    def _request_port_stats(self):
        for dpid, dp in self.datapaths.items():
            parser = dp.ofproto_parser
            req = parser.OFPPortStatsRequest(dp, 0, ofproto_v1_3.OFPP_ANY)
            dp.send_msg(req)

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def port_stats_reply_handler(self, ev):
        dp = ev.msg.datapath
        dpid = dp.id
        timestamp = time.time()

        for stat in ev.msg.body:
            self.port_stats_history[(dpid, stat.port_no)] = {
                "rx_bytes": stat.rx_bytes,
                "tx_bytes": stat.tx_bytes,
                "rx_dropped": stat.rx_dropped,
                "tx_dropped": stat.tx_dropped,
                "time": timestamp
            }

    @set_ev_cls(ofp_event.EventOFPBarrierReply, MAIN_DISPATCHER)
    def barrier_reply(self, ev):
        dp = ev.msg.datapath
        dpid_hex = format(dp.id, "016x")
        route = self.barrier_xids.pop((dp.id, ev.msg.xid), "unknown")

        self.logger.info(
            "BARRIER_CONFIRMED dpid=%s xid=%s route=%s",
            dpid_hex,
            ev.msg.xid,
            route,
        )
        if dp.id in self.pending_barriers:
            self.pending_barriers.remove(dp.id)
        elif dpid_hex in self.pending_barriers:
            self.pending_barriers.remove(dpid_hex)

    def _add_rule(self, role, in_name, out_name, eth_type, match_fields, route):
        dpid = self.roles[role]
        dp = self.datapaths.get(dpid)
        if dp is None:
            return

        parser = dp.ofproto_parser
        ofp = dp.ofproto
        
        # Explicitly cast to integer to prevent OpenFlow serialization mismatches
        in_port = int(self.port_names[dpid][in_name])
        out_port = int(self.port_names[dpid][out_name])
        clean_eth_type = int(eth_type, 16) if isinstance(eth_type, str) else int(eth_type)

        # Build match explicitly
        match = parser.OFPMatch(
            in_port=in_port,
            eth_type=clean_eth_type,
            **match_fields
        )
        
        actions = [parser.OFPActionOutput(out_port)]
        instructions = [parser.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, actions)]
        safe_priority = min(int(PRIORITY), 65535)

        msg = parser.OFPFlowMod(
            datapath=dp,
            cookie=COOKIE,
            table_id=0,
            command=ofp.OFPFC_ADD,
            priority=safe_priority,
            match=match,
            instructions=instructions,
        )
        dp.send_msg(msg)
        self.logger.info(
            "FLOW_MOD_SENT route=%s switch=%s dpid=%016x in=%s(%s) out=%s(%s) eth_type=0x%04x",
            route, role, dpid, in_name, in_port, out_name, out_port, clean_eth_type
        )

    def _install_route(self, route):
        if not self._roles_ready():
            self.logger.error("ROUTE_INSTALL_ABORTED: roles not ready")
            return False

        forward = PRIMARY_FORWARD if route == "PRIMARY" else ALTERNATE_FORWARD
        reverse = [(role, out_port, in_port) for role, in_port, out_port in reversed(forward)]
        self.logger.warning("heyeyeyey...........................",reverse)
        self.logger.warning("FLOW_UPDATE_START route=%s priority=%d cookie=0x%x", route, PRIORITY, COOKIE)

        for role in sorted(REQUIRED_ROLES):
            dpid = self.roles[role]
            dp = self.datapaths[dpid]
            ofp = dp.ofproto
            parser = dp.ofproto_parser
            delete = parser.OFPFlowMod(
                datapath=dp,
                cookie=COOKIE,
                cookie_mask=0xFFFFFFFFFFFFFFFF,
                table_id=ofp.OFPTT_ALL,
                command=ofp.OFPFC_DELETE,
                out_port=ofp.OFPP_ANY,
                out_group=ofp.OFPG_ANY,
                match=parser.OFPMatch(),
            )
            dp.send_msg(delete)

        for is_forward, hops in [(True, forward), (False, reverse)]:
            for role, in_name, out_name in hops:
                src_ip, dst_ip = (SRC, DST) if is_forward else (DST, SRC)
                self._add_rule(role, in_name, out_name, 0x0800, {"ipv4_src": src_ip, "ipv4_dst": dst_ip}, route)
                self._add_rule(role, in_name, out_name, 0x0806, {}, route)

        self.pending_barriers = set(self.roles.values())
        for role in sorted(REQUIRED_ROLES):
            dpid = self.roles[role]
            dp = self.datapaths[dpid]
            barrier = dp.ofproto_parser.OFPBarrierRequest(dp)
            dp.set_xid(barrier)
            self.barrier_xids[(dp.id, barrier.xid)] = route
            dp.send_msg(barrier)

        self.current_route = route
        self.last_change = time.time()
        self.logger.warning("FLOW_UPDATE_SENT route=%s; waiting for barrier replies", route)
        return True

    def force_switch_route(self, target_route="ALTERNATE"):
        self.logger.warning("FORCED_ROUTE_UPDATE initiating switch to %s", target_route)
        return self._install_route(target_route)

    def _run_ai_model_inference(self):
        """Feeds live port stats telemetry into the loaded Random Forest model with smart type casting."""
        if self.ai_model is None:
            return True  # Fallback if model failed to load

        try:
            s3_dpid = self.roles.get("s3")
            if s3_dpid and (s3_dpid, 1) in self.port_stats_history:
                stats = self.port_stats_history[(s3_dpid, 1)]
                
                row_data = {}
                for f in self.model_features:
                    f_lower = f.lower()
                    # If the feature sounds like an identifier or category, treat it as a string
                    if any(kw in f_lower for kw in ["switch", "port", "name", "protocol", "mac", "ip"]):
                        row_data[f] = str(stats.get(f, "s3"))
                    else:
                        # Otherwise, treat it as a numeric counter
                        row_data[f] = float(stats.get(f, 0.0))
                
                # Wrap in DataFrame with explicit column ordering
                features_df = pd.DataFrame([row_data], columns=self.model_features)
                
                prediction = self.ai_model.predict(features_df)
                return prediction[0] == 1
                
        except Exception:
            self.logger.exception("AI_INFERENCE_ERROR")
            
        return False

    def _monitor(self):
        self.logger.info("MONITOR_LOOP_STARTED")
        while True:
            try:
                self._request_port_stats()

                if self._roles_ready():
                    if self.current_route is None and not self.initial_install_attempted:
                        self.initial_install_attempted = True
                        self._install_route("PRIMARY")

                    elif self.current_route == "PRIMARY":
                        elapsed = time.time() - self.last_change
                        if elapsed >= COOLDOWN_SECONDS:
                            
                            # --- RFTC INGESTION & RETRAINING HOOK ---
                            s3_dpid = self.roles.get("s3")
                            if s3_dpid and (s3_dpid, 1) in self.port_stats_history:
                                stats = self.port_stats_history[(s3_dpid, 1)]
                                self.rftc.ingest_telemetry(stats, self.model_features)
                                
                                if self.rftc.should_retrain():
                                    self.ai_model = self.rftc.retrain_model(
                                        self.ai_model, self.model_features, model_path=MODEL_FILE
                                    )
                            # ----------------------------------------

                            congested = self._run_ai_model_inference()
                            if congested:
                                self.logger.info("ROUTE_DECISION PRIMARY -> ALTERNATE triggered by Random Forest model")
                                self._install_route("ALTERNATE")

                    elif self.current_route == "ALTERNATE":
                        elapsed = time.time() - self.last_change
                        if elapsed >= COOLDOWN_SECONDS:
                            pass

            except Exception:
                self.logger.exception("MONITOR_LOOP_ERROR")

            hub.sleep(POLL_SECONDS)