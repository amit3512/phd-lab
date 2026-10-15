from ryu.base import app_manager
from ryu.topology import event
from ryu.controller.handler import set_ev_cls


class TopologyMonitor(app_manager.RyuApp):

    @set_ev_cls(event.EventSwitchEnter)
    def switch_enter(self, ev):
        dpid = ev.switch.dp.id
        self.logger.info(
            "TOPOLOGY_SWITCH_ENTER dpid=%016x", dpid
        )

    @set_ev_cls(event.EventSwitchLeave)
    def switch_leave(self, ev):
        dpid = ev.switch.dp.id
        self.logger.info(
            "TOPOLOGY_SWITCH_LEAVE dpid=%016x", dpid
        )

    @set_ev_cls(event.EventLinkAdd)
    def link_add(self, ev):
        src = ev.link.src
        dst = ev.link.dst
        self.logger.info(
            "TOPOLOGY_LINK_ADD %016x:%s -> %016x:%s",
            src.dpid, src.port_no, dst.dpid, dst.port_no
        )

    @set_ev_cls(event.EventLinkDelete)
    def link_delete(self, ev):
        src = ev.link.src
        dst = ev.link.dst
        self.logger.info(
            "TOPOLOGY_LINK_DELETE %016x:%s -> %016x:%s",
            src.dpid, src.port_no, dst.dpid, dst.port_no
        )
