from ryu.base import app_manager

from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, set_ev_cls

from ryu.ofproto import ofproto_v1_3

from ryu.app.wsgi import WSGIApplication, ControllerBase, route

from webob import Response

import json


class RoutingAPI(ControllerBase):

    def __init__(self, req, link, data, **config):

        super().__init__(req, link, data, **config)

        self.app = data["app"]

    @route("routing", "/routing", methods=["POST"])
    def routing(self, req, **kwargs):

        body = json.loads(req.body.decode("utf-8"))

        print("Routing decision:", body)

        sensor = body["sensor"]

        destination_ip = body["destination_ip"]

        output_port = body["output_port"]

        self.app.install_flow(destination_ip, output_port)

        response = {
            "status": "Flow installed",
            "sensor": sensor,
            "destination_ip": destination_ip,
            "output_port": output_port,
        }

        return Response(
            content_type="application/json", body=json.dumps(response).encode("utf-8")
        )


class SDNController(app_manager.RyuApp):

    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    _CONTEXTS = {"wsgi": WSGIApplication}

    def __init__(self, *args, **kwargs):

        super().__init__(*args, **kwargs)

        self.datapath = None

        wsgi = kwargs["wsgi"]

        wsgi.register(RoutingAPI, {"app": self})

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features(self, ev):

        self.datapath = ev.msg.datapath

        print("OVS connected:", self.datapath.id)

    def install_flow(self, destination_ip, output_port):

        if self.datapath is None:

            print("No OVS switch connected")

            return

        dp = self.datapath

        parser = dp.ofproto_parser

        ofproto = dp.ofproto

        if output_port == "veth0":

            out_port = 3

        else:

            out_port = 4

        match = parser.OFPMatch(eth_type=0x0800, ipv4_dst=destination_ip)

        actions = [parser.OFPActionOutput(out_port)]

        instructions = [
            parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)
        ]

        flow = parser.OFPFlowMod(
            datapath=dp, priority=100, match=match, instructions=instructions
        )

        dp.send_msg(flow)

        print(f"Installed {destination_ip} -> {output_port}")
