from ryu.base import app_manager

from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls

from ryu.app.wsgi import WSGIApplication, ControllerBase, route

from webob import Response

import json


class RoutingAPI(ControllerBase):

    def __init__(self, req, link, data, **config):
        super().__init__(req, link, data, **config)

        self.app = data["app"]

    @route("routing", "/routing", methods=["POST"])
    def routing(self, req):

        body = json.loads(req.body.decode("utf-8"))

        print("Routing decision:", body)

        sensor = body["sensor"]

        # Example:
        # send all sensor traffic to veth1

        self.app.install_sensor_flow()

        return Response(body="Flow installed")


class SDNRoutingController(app_manager.RyuApp):

    OFP_VERSIONS = [0x04]

    _CONTEXTS = {"wsgi": WSGIApplication}

    def __init__(self, *args, **kwargs):

        super().__init__(*args, **kwargs)

        self.datapath = None

        wsgi = kwargs["wsgi"]

        wsgi.register(RoutingAPI, {"app": self})

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):

        self.datapath = ev.msg.datapath

        print("OVS connected:", self.datapath.id)

    def install_sensor_flow(self):

        if self.datapath is None:

            print("No switch connected")

            return

        datapath = self.datapath

        parser = datapath.ofproto_parser

        ofproto = datapath.ofproto

        match = parser.OFPMatch()

        actions = [parser.OFPActionOutput(4)]

        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]

        flow = parser.OFPFlowMod(
            datapath=datapath, priority=10, match=match, instructions=inst
        )

        datapath.send_msg(flow)

        print("Installed flow: ALL -> veth1")
