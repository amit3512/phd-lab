from ryu.base import app_manager

from ryu.controller import ofp_event

from ryu.controller.handler import CONFIG_DISPATCHER, set_ev_cls

from ryu.app.wsgi import WSGIApplication, ControllerBase, route

from webob import Response

import json


class RoutingAPI(ControllerBase):

    def __init__(self, req, link, data, **config):

        super().__init__(req, link, data, **config)

        self.app = data["app"]

    @route("routing", "/routing", methods=["POST"])
    def routing(self, req):

        body = json.loads(req.body.decode())

        print("Routing decision:", body)

        self.app.install_flow(
            body["sensor"], body["destination_ip"], body["output_port"]
        )

        return Response(body="Flow installed")


class SDNController(app_manager.RyuApp):

    OFP_VERSIONS = [0x04]

    _CONTEXTS = {"wsgi": WSGIApplication}

    def __init__(self, *args, **kwargs):

        super().__init__(*args, **kwargs)

        self.datapath = None

        wsgi = kwargs["wsgi"]

        wsgi.register(RoutingAPI, {"app": self})

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_connected(self, ev):

        self.datapath = ev.msg.datapath

        print("OVS connected:", self.datapath.id)

    def install_flow(self, sensor, destination_ip, output_port):

        if self.datapath is None:

            print("No OVS connection")

            return

        datapath = self.datapath

        parser = datapath.ofproto_parser

        ofproto = datapath.ofproto

        if output_port == "veth0":

            port = 3

        elif output_port == "veth1":

            port = 4

        else:

            print("Unknown port")

            return

        match = parser.OFPMatch(eth_type=0x0800, ipv4_dst=destination_ip)

        actions = [parser.OFPActionOutput(port)]

        instructions = [
            parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)
        ]

        flow = parser.OFPFlowMod(
            datapath=datapath, priority=100, match=match, instructions=instructions
        )

        datapath.send_msg(flow)

        print(f"Installed {sensor}: " f"{destination_ip} -> {output_port}")
