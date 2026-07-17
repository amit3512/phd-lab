from ryu.base import app_manager

from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls

from ryu.ofproto import ofproto_v1_3

from ryu.app.wsgi import WSGIApplication, ControllerBase, route

from ryu.topology import event
from ryu.topology.api import get_switch

from webob import Response

import json
import threading


class RoutingAPI(ControllerBase):

    def __init__(self, req, link, data, **config):

        super().__init__(req, link, data, **config)

        self.app = data["app"]

    @route("routing", "/routing", methods=["POST"])
    def routing(self, req, **kwargs):

        body = json.loads(req.body.decode("utf-8"))

        print("Routing request:", body)

        sensor = body["sensor"]

        destination_ip = body["destination_ip"]

        self.app.install_flow(destination_ip)

        response = {
            "status": "Flow installed",
            "sensor": sensor,
            "destination_ip": destination_ip,
        }

        return Response(
            content_type="application/json", body=json.dumps(response).encode()
        )


class SDNController(app_manager.RyuApp):

    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    _CONTEXTS = {"wsgi": WSGIApplication}

    def __init__(self, *args, **kwargs):

        super().__init__(*args, **kwargs)

        self.datapath = None

        self.lock = threading.Lock()

        #
        # Dynamic table
        #
        # Example:
        #
        # {
        #  "172.22.251.30":3
        # }
        #

        self.destination_ports = {}

        wsgi = kwargs["wsgi"]

        wsgi.register(RoutingAPI, {"app": self})

    # ---------------------------
    # OVS connected
    # ---------------------------

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features(self, ev):

        self.datapath = ev.msg.datapath

        print("OVS connected:", self.datapath.id)

        self.add_table_miss()

    # ---------------------------
    # Allow packet in
    # ---------------------------

    def add_table_miss(self):

        dp = self.datapath

        parser = dp.ofproto_parser

        ofproto = dp.ofproto

        actions = [
            parser.OFPActionOutput(ofproto.OFPP_CONTROLLER, ofproto.OFPCML_NO_BUFFER)
        ]

        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]

        flow = parser.OFPFlowMod(datapath=dp, priority=0, instructions=inst)

        dp.send_msg(flow)

    # ---------------------------
    # Topology discovery
    # ---------------------------

    @set_ev_cls(event.EventSwitchEnter)
    def switch_enter(self, ev):

        switch = get_switch(self, ev.switch.dp.id)

        print("Switch discovered")

        for sw in switch:

            for port in sw.ports:

                print("Port:", port.port_no, port.name)

    # ---------------------------
    # Install flow
    # ---------------------------

    def install_flow(self, destination_ip):

        if self.datapath is None:

            print("No OVS connected")

            return

        #
        # Find output port
        #

        out_port = self.find_port(destination_ip)

        if out_port is None:

            print("No port found for", destination_ip)

            return

        dp = self.datapath

        parser = dp.ofproto_parser

        ofproto = dp.ofproto

        match = parser.OFPMatch(eth_type=0x0800, ipv4_dst=destination_ip)

        actions = [parser.OFPActionOutput(out_port)]

        instructions = [
            parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)
        ]

        flow = parser.OFPFlowMod(
            datapath=dp, priority=100, match=match, instructions=instructions
        )

        dp.send_msg(flow)

        print("Installed:", destination_ip, "-> port", out_port)

    # ---------------------------
    # Destination lookup
    # ---------------------------

    def find_port(self, destination_ip):

        #
        # Temporary dynamic table
        #
        # This will later be filled
        # automatically from Kubernetes
        # node discovery + topology
        #

        with self.lock:

            return self.destination_ports.get(destination_ip)
