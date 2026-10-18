
import time
from pathlib import Path

import joblib
import pandas as pd

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import (
    CONFIG_DISPATCHER,
    MAIN_DISPATCHER,
    DEAD_DISPATCHER,
    set_ev_cls,
)
from ryu.lib import hub
from ryu.ofproto import ofproto_v1_3


# ============================================================
# CONFIGURATION
# ============================================================

SRC = "192.168.100.1"
DST = "192.168.100.2"

MODEL_FILE = Path("congestion_rf.joblib")

# Deliberately low threshold for testing.
# Units: Mbps.
THRESHOLD_MBPS = 0.0001

POLL_SECONDS = 5
COOLDOWN_SECONDS = 10

COOKIE = 0xEA100001
PRIORITY = 500

# True: demonstrate PRIMARY -> ALTERNATE even without real congestion.
# False: switch routes based on the live threshold or model prediction.
SIMULATE_CONGESTION = True

REQUIRED_ROLES = {
    "br-ryu-test", "s2", "s3", "s4", "s5", "s6", "s7"
}

FEATURES = [
    "switch", "port",
    "rx_mbps", "tx_mbps",
    "rx_pps", "tx_pps",
    "rx_drops", "tx_drops",
    "rx_errors", "tx_errors",
]

NUMERIC_FEATURES = [
    "rx_mbps", "tx_mbps",
    "rx_pps", "tx_pps",
    "rx_drops", "tx_drops",
    "rx_errors", "tx_errors",
]

# The names here must match the names reported by OVS.
ROLE_PORT_SIGNATURES = {
    "br-ryu-test": {"v12a", "hostA-sw"},
    "s2":    {"v12b", "v23a", "v25a"},
    "s3":    {"v23b", "v34a"},
    "s4":    {"v34b", "v47a", "v64b"},
    "s5":    {"v25b", "v56a"},
    "s6":    {"v56b", "v64a"},
    "s7":    {"v47b", "hostB-sw"},
}

# Primary route: edgeA -> s2 -> s3 -> s4 -> s7
PRIMARY_FORWARD = [
    ("br-ryu-test", "hostA-sw", "v12a"),
    ("s2",    "v12b",     "v23a"),
    ("s3",    "v23b",     "v34a"),
    ("s4",    "v34b",     "v47a"),
    ("s7",    "v47b",     "hostB-sw"),
]

# Alternate route: edgeA -> s2 -> s5 -> s6 -> s4 -> s7
ALTERNATE_FORWARD = [
    ("br-ryu-test", "hostA-sw", "v12a"),
    ("s2",    "v12b",     "v25a"),
    ("s5",    "v25b",     "v56a"),
    ("s6",    "v56b",     "v64a"),
    ("s4",    "v64b",     "v47a"),
    ("s7",    "v47b",     "hostB-sw"),
]

# Outgoing ports on the primary route, used for live monitoring.
PRIMARY_EGRESS_PORTS = [
    ("br-ryu-test", "v12a"),
    ("s2", "v23a"),
    ("s3", "v34a"),
    ("s4", "v47a"),
]


class EdgeAIRyu(app_manager.RyuApp):

    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(EdgeAIRyu, self).__init__(*args, **kwargs)

        self.datapaths = {}
        self.port_names = {}
        self.port_numbers = {}
        self.roles = {}

        # Previous port counters, used to calculate traffic rates.
        self.previous = {}

        # Latest calculated metrics keyed by (dpid, port_number).
        self.live = {}

        self.model = None
        self.model_features = FEATURES

        self.current_route = None
        self.last_change = 0.0
        self.initial_install_attempted = False

        self.barrier_xids = {}
        self.last_flow_check = 0.0

        self._load_model()

        self.monitor_thread = hub.spawn(self._monitor)

        self.logger.info(
            "EDGE_AI_START threshold=%.6f Mbps simulation=%s",
            THRESHOLD_MBPS,
            SIMULATE_CONGESTION,
        )

    # ========================================================
    # MODEL
    # ========================================================

    def _load_model(self):
        if not MODEL_FILE.exists():
            self.logger.error(
                "MODEL_NOT_FOUND path=%s; threshold-based detection remains enabled",
                MODEL_FILE.resolve(),
            )
            return

        try:
            bundle = joblib.load(MODEL_FILE)

            if isinstance(bundle, dict):
                self.model = bundle.get("model")
                self.model_features = bundle.get("features", FEATURES)
            else:
                self.model = bundle
                self.model_features = FEATURES

            if self.model is None:
                self.logger.error(
                    "MODEL_LOAD_FAILED: the file contains no 'model' entry"
                )
                return

            classes = getattr(self.model, "classes_", None)

            if classes is None and hasattr(self.model, "named_steps"):
                final_step = list(self.model.named_steps.values())[-1]
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

    def _predict_congestion(self, role, port_name, metrics):
        """Return the model prediction, or None if prediction fails."""

        if self.model is None:
            return None

        row = {
            "switch": role,
            "port": port_name,
            **metrics,
        }

        try:
            frame = pd.DataFrame(
                [[row.get(name, 0) for name in self.model_features]],
                columns=self.model_features,
            )

            prediction = self.model.predict(frame)[0]
            return int(prediction)

        except Exception as exc:
            self.logger.warning(
                "MODEL_PREDICTION_FAILED switch=%s port=%s error=%s",
                role, port_name, exc,
            )
            return None

    # ========================================================
    # SWITCH REGISTRATION AND PORT DISCOVERY
    # ========================================================

    @set_ev_cls(
        ofp_event.EventOFPStateChange,
        [MAIN_DISPATCHER, DEAD_DISPATCHER],
    )
    def state_change(self, ev):
        dp = ev.datapath

        if ev.state == MAIN_DISPATCHER:
            self.datapaths[dp.id] = dp

            self.logger.info(
                "DATAPATH_ACTIVE dpid=%016x",
                dp.id,
            )

        elif ev.state == DEAD_DISPATCHER:
            self.datapaths.pop(dp.id, None)
            self.port_names.pop(dp.id, None)
            self.port_numbers.pop(dp.id, None)

            self._identify_roles()

            self.logger.warning(
                "DATAPATH_DISCONNECTED dpid=%016x",
                dp.id,
            )

    @set_ev_cls(
        ofp_event.EventOFPSwitchFeatures,
        CONFIG_DISPATCHER,
    )
    def switch_features(self, ev):
        dp = ev.msg.datapath

        self.logger.info(
            "SWITCH_FEATURES_RECEIVED dpid=%016x",
            dp.id,
        )

        request = dp.ofproto_parser.OFPPortDescStatsRequest(dp, 0)
        dp.send_msg(request)

    @set_ev_cls(
        ofp_event.EventOFPPortDescStatsReply,
        MAIN_DISPATCHER,
    )
    def port_description_reply(self, ev):
        dp = ev.msg.datapath

        names = {}
        numbers = {}

        for port in ev.msg.body:
            name = port.name

            if isinstance(name, bytes):
                name = name.decode(errors="ignore")

            name = name.rstrip("\x00")

            if name and not name.startswith("LOCAL"):
                names[name] = port.port_no
                numbers[port.port_no] = name

        self.port_names[dp.id] = names
        self.port_numbers[dp.id] = numbers

        self.logger.info(
            "PORTS_DISCOVERED dpid=%016x ports=%s",
            dp.id,
            names,
        )

        self._identify_roles()

    def _identify_roles(self):
        """Identify bridges from their discovered OVS port names."""

        roles = {}

        for dpid, names in self.port_names.items():
            available = set(names.keys())

            for role, required_ports in ROLE_PORT_SIGNATURES.items():
                if required_ports.issubset(available):
                    roles[role] = dpid

        self.roles = roles

        if self._roles_ready():
            self.logger.info(
                "ALL_ROLES_IDENTIFIED roles=%s",
                {
                    role: "%016x" % self.roles[role]
                    for role in sorted(REQUIRED_ROLES)
                },
            )
        else:
            missing = sorted(REQUIRED_ROLES - set(roles.keys()))

            self.logger.info(
                "WAITING_FOR_SWITCH_ROLES missing=%s identified=%s",
                missing,
                sorted(roles.keys()),
            )

    def _roles_ready(self):
        return REQUIRED_ROLES.issubset(set(self.roles.keys()))

    # ========================================================
    # OPENFLOW ERROR / BARRIER / FLOW-STATISTICS HANDLERS
    # ========================================================

    @set_ev_cls(
        ofp_event.EventOFPErrorMsg,
        [CONFIG_DISPATCHER, MAIN_DISPATCHER],
    )
    def openflow_error(self, ev):
        msg = ev.msg

        try:
            data_hex = bytes(msg.data).hex() if msg.data else ""
        except Exception:
            data_hex = str(msg.data)

        self.logger.error(
            "OPENFLOW_ERROR dpid=%016x xid=%s type=%s code=%s data=%s",
            msg.datapath.id,
            msg.xid,
            msg.type,
            msg.code,
            data_hex,
        )

    @set_ev_cls(
        ofp_event.EventOFPBarrierReply,
        MAIN_DISPATCHER,
    )
    def barrier_reply(self, ev):
        dp = ev.msg.datapath
        dpid_hex = format(dp.id, "016x") # i added this
        route = self.barrier_xids.pop((dp.id, ev.msg.xid), "unknown")

        self.logger.info(
            "BARRIER_CONFIRMED dpid=%016x xid=%s route=%s",
            dp.id,
            ev.msg.xid,
            route,
        )
        # i added this
        # Safely check and remove using the proper variable name
        if hasattr(self, 'pending_barriers') and dp.id in self.pending_barriers:
            self.pending_barriers.remove(dp.id)
        elif hasattr(self, 'pending_barriers') and dpid_hex in self.pending_barriers:
            self.pending_barriers.remove(dpid_hex)

    @set_ev_cls(
        ofp_event.EventOFPFlowStatsReply,
        MAIN_DISPATCHER,
    )
    def flow_stats_reply(self, ev):
        dp = ev.msg.datapath

        app_flows = [
            flow for flow in ev.msg.body
            if flow.cookie == COOKIE
        ]

        self.logger.info(
            "FLOW_STATS dpid=%016x total=%d app_cookie_flows=%d",
            dp.id,
            len(ev.msg.body),
            len(app_flows),
        )

    def _request_flow_stats(self):
        for dp in list(self.datapaths.values()):
            try:
                request = dp.ofproto_parser.OFPFlowStatsRequest(dp)
                dp.send_msg(request)
            except Exception:
                self.logger.exception(
                    "FLOW_STATS_REQUEST_FAILED dpid=%016x",
                    dp.id,
                )

    # ========================================================
    # PORT STATISTICS AND LIVE METRICS
    # ========================================================

    def _request_port_stats(self):
        for dp in list(self.datapaths.values()):
            try:
                request = dp.ofproto_parser.OFPPortStatsRequest(
                    dp, 0, dp.ofproto.OFPP_ANY
                )
                dp.send_msg(request)
            except Exception:
                self.logger.exception(
                    "PORT_STATS_REQUEST_FAILED dpid=%016x",
                    dp.id,
                )

    @set_ev_cls(
        ofp_event.EventOFPPortStatsReply,
        MAIN_DISPATCHER,
    )
    def port_stats_reply(self, ev):
        dp = ev.msg.datapath
        now = time.time()

        for stat in ev.msg.body:
            port_no = stat.port_no

            if port_no == dp.ofproto.OFPP_LOCAL:
                continue

            key = (dp.id, port_no)

            counters = {
                "rx_bytes": stat.rx_bytes,
                "tx_bytes": stat.tx_bytes,
                "rx_packets": stat.rx_packets,
                "tx_packets": stat.tx_packets,
                "rx_drops": stat.rx_dropped,
                "tx_drops": stat.tx_dropped,
                "rx_errors": stat.rx_errors,
                "tx_errors": stat.tx_errors,
            }

            previous = self.previous.get(key)
            metrics = {
                "rx_mbps": 0.0,
                "tx_mbps": 0.0,
                "rx_pps": 0.0,
                "tx_pps": 0.0,
                "rx_drops": float(stat.rx_dropped),
                "tx_drops": float(stat.tx_dropped),
                "rx_errors": float(stat.rx_errors),
                "tx_errors": float(stat.tx_errors),
            }

            if previous is not None:
                elapsed = now - previous["time"]

                if elapsed > 0:
                    for counter, rate_name in [
                        ("rx_bytes", "rx_mbps"),
                        ("tx_bytes", "tx_mbps"),
                    ]:
                        delta = counters[counter] - previous[counter]

                        if delta >= 0:
                            metrics[rate_name] = (
                                delta * 8.0 / elapsed / 1_000_000.0
                            )

                    for counter, rate_name in [
                        ("rx_packets", "rx_pps"),
                        ("tx_packets", "tx_pps"),
                    ]:
                        delta = counters[counter] - previous[counter]

                        if delta >= 0:
                            metrics[rate_name] = delta / elapsed

            self.previous[key] = {
                **counters,
                "time": now,
            }

            self.live[key] = metrics

            role = next(
                (
                    r for r, role_dpid in self.roles.items()
                    if role_dpid == dp.id
                ),
                "unknown",
            )

            port_name = self.port_numbers.get(dp.id, {}).get(
                port_no, str(port_no)
            )

            prediction = self._predict_congestion(
                role, port_name, metrics
            )

            if (
                metrics["rx_mbps"] > 0
                or metrics["tx_mbps"] > 0
                or prediction == 1
            ):
                self.logger.info(
                    "PORT_METRIC switch=%s port=%s "
                    "rx_mbps=%.6f tx_mbps=%.6f "
                    "rx_pps=%.2f tx_pps=%.2f prediction=%s",
                    role,
                    port_name,
                    metrics["rx_mbps"],
                    metrics["tx_mbps"],
                    metrics["rx_pps"],
                    metrics["tx_pps"],
                    prediction,
                )

    def _primary_route_is_congested(self):
        """Check measured traffic and model predictions on primary egress ports."""

        for role, port_name in PRIMARY_EGRESS_PORTS:
            dpid = self.roles.get(role)

            if dpid is None:
                continue

            port_no = self.port_names.get(dpid, {}).get(port_name)

            if port_no is None:
                continue

            metrics = self.live.get((dpid, port_no))

            if metrics is None:
                continue

            rate = max(
                metrics.get("rx_mbps", 0.0),
                metrics.get("tx_mbps", 0.0),
            )

            prediction = self._predict_congestion(
                role, port_name, metrics
            )

            if rate > THRESHOLD_MBPS or prediction == 1:
                self.logger.warning(
                    "CONGESTION_DETECTED switch=%s port=%s "
                    "rate_mbps=%.6f threshold=%.6f prediction=%s",
                    role,
                    port_name,
                    rate,
                    THRESHOLD_MBPS,
                    prediction,
                )
                return True

        return False

    # ========================================================
    # ROUTE INSTALLATION
    # ========================================================

    def _install_route(self, route):
        if not self._roles_ready():
            self.logger.error(
                "ROUTE_INSTALL_ABORTED: roles are not ready"
            )
            return False

        forward = (
            PRIMARY_FORWARD
            if route == "PRIMARY"
            else ALTERNATE_FORWARD
        )

        # Build the reverse direction by reversing each hop's ports.
        reverse = [
            (role, out_port, in_port)
            for role, in_port, out_port in reversed(forward)
        ]

        # Validate every port before modifying any flow table.
        for role, in_name, out_name in forward + reverse:
            dpid = self.roles.get(role)
            ports = self.port_names.get(dpid, {})

            if in_name not in ports or out_name not in ports:
                self.logger.error(
                    "ROUTE_INSTALL_ABORTED route=%s switch=%s "
                    "missing_in=%s missing_out=%s available=%s",
                    route,
                    role,
                    in_name,
                    out_name,
                    sorted(ports.keys()),
                )
                return False

            if dpid not in self.datapaths:
                self.logger.error(
                    "ROUTE_INSTALL_ABORTED route=%s switch=%s "
                    "datapath_not_active",
                    route,
                    role,
                )
                return False

        self.logger.warning(
            "FLOW_UPDATE_START route=%s priority=%d cookie=0x%x",
            route,
            PRIORITY,
            COOKIE,
        )

        # Remove only flows owned by this application.
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

        # Add IPv4 and ARP forwarding rules in both directions.
        for is_forward, hops in [
            (True, forward),
            (False, reverse),
        ]:
            for role, in_name, out_name in hops:
                if is_forward:
                    src_ip, dst_ip = SRC, DST
                else:
                    src_ip, dst_ip = DST, SRC

                self._add_rule(
                    role=role,
                    in_name=in_name,
                    out_name=out_name,
                    eth_type=0x0800,
                    match_fields={
                        "ipv4_src": src_ip,
                        "ipv4_dst": dst_ip,
                    },
                    route=route,
                )

                # ARP has no IPv4 source/destination fields.
                self._add_rule(
                    role=role,
                    in_name=in_name,
                    out_name=out_name,
                    eth_type=0x0806,
                    match_fields={},
                    route=route,
                )

        # Barriers help confirm that each switch processed prior messages.
        for role in sorted(REQUIRED_ROLES):
            dpid = self.roles[role]
            dp = self.datapaths[dpid]

            barrier = dp.ofproto_parser.OFPBarrierRequest(dp)
            dp.set_xid(barrier)
            self.barrier_xids[(dp.id, barrier.xid)] = route
            dp.send_msg(barrier)

        self.current_route = route
        self.last_change = time.time()

        self.logger.warning(
            "FLOW_UPDATE_SENT route=%s; waiting for barrier replies",
            route,
        )

        return True

    def _add_rule(
        self,
        role,
        in_name,
        out_name,
        eth_type,
        match_fields,
        route,
    ):
        dpid = self.roles[role]
        dp = self.datapaths.get(dpid)

        if dp is None:
            self.logger.error(
                "FLOW_MOD_FAILED switch=%s datapath_missing",
                role,
            )
            return

        parser = dp.ofproto_parser
        ofp = dp.ofproto

        in_port = self.port_names[dpid][in_name]
        out_port = self.port_names[dpid][out_name]

        fields = {
            "in_port": in_port,
            "eth_type": eth_type,
            **match_fields,
        }

        match = parser.OFPMatch(**fields)
        actions = [parser.OFPActionOutput(out_port)]

        instructions = [
            parser.OFPInstructionActions(
                ofp.OFPIT_APPLY_ACTIONS,
                actions,
            )
        ]

        msg = parser.OFPFlowMod(
            datapath=dp,
            cookie=COOKIE,
            table_id=0,
            command=ofp.OFPFC_ADD,
            priority=PRIORITY,
            match=match,
            instructions=instructions,
        )

        dp.send_msg(msg)

        self.logger.info(
            "FLOW_MOD_SENT route=%s switch=%s dpid=%016x "
            "in=%s(%s) out=%s(%s) eth_type=0x%04x match=%s",
            route,
            role,
            dpid,
            in_name,
            in_port,
            out_name,
            out_port,
            eth_type,
            fields,
        )

    # ========================================================
    # MAIN MONITOR LOOP
    # ========================================================

    def _monitor(self):
        self.logger.info("MONITOR_LOOP_STARTED")
        while True:
            try:
                self.logger.info("MONITOR_STEP _request_port_stats_start")
                self._request_port_stats()
                self.logger.info("MONITOR_STEP roles_ready_check")

                if self._roles_ready():
                    self.logger.info("MONITOR_STEP roles_are_ready_true")
                    if (
                        self.current_route is None
                        and not self.initial_install_attempted
                    ):
                        self.initial_install_attempted = True
                        self._install_route("PRIMARY")

                    elif self.current_route == "PRIMARY":
                        #added this line
                        self.logger.info("DEBUG_STATE current_route=%s elapsed=%.2f last_change=%s", 
                                         self.current_route, 
                                         time.time() - getattr(self, 'last_change', 0),
                                         getattr(self, 'last_change', None))
                        elapsed = time.time() - self.last_change
                        # self.logger.info("MONITOR_TICK elapsed=%.2f route=PRIMARY", elapsed) # <-- Add this line

                        if elapsed >= COOLDOWN_SECONDS:
                            congested = (
                                SIMULATE_CONGESTION
                                or self._primary_route_is_congested()
                            )

                            if congested:
                                self.logger.warning(
                                    "ROUTE_DECISION PRIMARY -> ALTERNATE "
                                    "simulated=%s",
                                    SIMULATE_CONGESTION,
                                )
                                self._install_route("ALTERNATE")

                    elif self.current_route == "ALTERNATE":
                        if not SIMULATE_CONGESTION:
                            elapsed = time.time() - self.last_change

                            if (
                                elapsed >= COOLDOWN_SECONDS
                                and not self._primary_route_is_congested()
                            ):
                                self.logger.info(
                                    "ROUTE_DECISION ALTERNATE -> PRIMARY"
                                )
                                self._install_route("PRIMARY")

                else:
                    self.logger.warning("MONITOR_STEP roles_are_ready_FALSE")
                    self.logger.debug(
                        "MONITOR_WAITING_FOR_ROLES identified=%s",
                        sorted(self.roles.keys()),
                    )

                now = time.time()

                if now - self.last_flow_check >= 15:
                    self._request_flow_stats()
                    self.last_flow_check = now

            except Exception:
                self.logger.exception("MONITOR_LOOP_ERROR")

            hub.sleep(POLL_SECONDS)
