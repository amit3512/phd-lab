import eventlet
eventlet.monkey_patch()

import time
from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
from ryu.lib import hub
from ryu.ofproto import ofproto_v1_3

# Topology and Role Definitions for the 7-Switch Fabric
REQUIRED_ROLES = ["br-ryu-test", "s2", "s3", "s4", "s5", "s6", "s7"]

# IP Addresses for the data flow rules
SRC = "192.168.100.1"
DST = "192.168.100.2"

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

PRIORITY = 500
COOKIE = 0xEA100001
COOLDOWN_SECONDS = 10
POLL_SECONDS = 5
SIMULATE_CONGESTION = True


class ForceEdgeAIRyu(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(ForceEdgeAIRyu, self).__init__(*args, **kwargs)
        self.datapaths = {}  # dpid -> datapath object
        self.port_names = {}  # dpid -> {port_name: port_no}
        self.roles = {}  # role_name -> dpid
        self.barrier_xids = {}  # (dpid, xid) -> route_name
        self.pending_barriers = set()

        self.current_route = None
        self.last_change = 0.0
        self.initial_install_attempted = False

        # Spawn background monitoring and state loop safely
        self.monitor_thread = hub.spawn(self._monitor)

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        dp = ev.msg.datapath
        self.datapaths[dp.id] = dp
        self.logger.info("SWITCH_FEATURES_RECEIVED dpid=%016x", dp.id)

        # Request port descriptions to map names to port numbers
        parser = dp.ofproto_parser
        req = parser.OFPPortDescStatsRequest(dp)
        dp.send_msg(req)

    @set_ev_cls(ofp_event.EventOFPPortDescStatsReply, MAIN_DISPATCHER)
    def port_desc_stats_reply_handler(self, ev):
        dp = ev.msg.datapath
        dpid = dp.id
        
        # Decode port names safely in case they arrive as bytestrings
        ports = {}
        for p in ev.msg.body:
            name = p.name
            if isinstance(name, bytes):
                name = name.decode('utf-8', errors='ignore')
            ports[name] = p.port_no
            
        self.port_names[dpid] = ports

        self.logger.info("DATAPATH_ACTIVE dpid=%016x", dpid)
        self.logger.info("PORTS_DISCOVERED dpid=%016x ports=%s", dpid, ports)

        # Infer and assign roles based on unique port signatures
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
        pass

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
        in_port = self.port_names[dpid][in_name]
        out_port = self.port_names[dpid][out_name]

        fields = {"in_port": in_port, "eth_type": eth_type, **match_fields}
        match = parser.OFPMatch(**fields)
        actions = [parser.OFPActionOutput(out_port)]
        instructions = [parser.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, actions)]

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
            "FLOW_MOD_SENT route=%s switch=%s dpid=%016x in=%s(%s) out=%s(%s) eth_type=0x%04x",
            route, role, dpid, in_name, in_port, out_name, out_port, eth_type
        )

    def _install_route(self, route):
        if not self._roles_ready():
            self.logger.error("ROUTE_INSTALL_ABORTED: roles not ready")
            return False

        forward = PRIMARY_FORWARD if route == "PRIMARY" else ALTERNATE_FORWARD
        reverse = [(role, out_port, in_port) for role, in_port, out_port in reversed(forward)]

        self.logger.warning("FLOW_UPDATE_START route=%s priority=%d cookie=0x%x", route, PRIORITY, COOKIE)

        # Clear existing controller flows
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

        # Install forward and reverse rules
        for is_forward, hops in [(True, forward), (False, reverse)]:
            for role, in_name, out_name in hops:
                src_ip, dst_ip = (SRC, DST) if is_forward else (DST, SRC)
                self._add_rule(role, in_name, out_name, 0x0800, {"ipv4_src": src_ip, "ipv4_dst": dst_ip}, route)
                self._add_rule(role, in_name, out_name, 0x0806, {}, route)

        # Track and dispatch barriers
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
        """Manually forces a path transition to PRIMARY or ALTERNATE instantly."""
        self.logger.warning("FORCED_ROUTE_UPDATE initiating switch to %s", target_route)
        return self._install_route(target_route)

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
                            congested = SIMULATE_CONGESTION
                            if congested:
                                self.logger.info("ROUTE_DECISION PRIMARY -> ALTERNATE simulated=%s", SIMULATE_CONGESTION)
                                self._install_route("ALTERNATE")

                    elif self.current_route == "ALTERNATE":
                        elapsed = time.time() - self.last_change
                        if elapsed >= COOLDOWN_SECONDS:
                            pass

            except Exception:
                self.logger.exception("MONITOR_LOOP_ERROR")

            hub.sleep(POLL_SECONDS)