from twisted.application import service, internet
from twisted.web import resource, server
from twisted.internet import defer

from tippresence import PresenceService
from tipsip.storage import MemoryStorage
from tipsip.transport import Address, UDPTransport
from tipsip.dialog import DialogStore, Dialog

from tippresence.http import HTTPStats, HTTPPresence
from tippresence.sip import SIPPresence
from tippresence.amqp import AMQPublisher, AMQFactory

storage = MemoryStorage()

presence_service = PresenceService(storage)

root = resource.Resource()
root.putChild("stats", HTTPStats())
root.putChild("presence", HTTPPresence(presence_service))

http_site = server.Site(root)

application = service.Application("TipSIP PresenceServer")
http_service = internet.TCPServer(18082, http_site)
http_service.setServiceParent(application)

dialog_store = DialogStore(storage)

udp_transport = UDPTransport(Address('127.0.0.1', 5060, 'UDP'))
sip_ua = SIPPresence(dialog_store, udp_transport, presence_service)

sip_service = internet.UDPServer(5060, udp_transport)
sip_service.setServiceParent(application)

creds = {"LOGIN": "guest", "PASSWORD": "guest"}
amq_factory = AMQFactory(creds, 'tippresence/amqp/amqp0-8.xml')
amq_publisher = AMQPublisher(amq_factory, presence_service)
amq_client = internet.TCPClient("ivaxer.tipmeet.com", 5672, amq_factory)
amq_client.setServiceParent(application)

