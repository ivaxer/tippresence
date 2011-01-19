# -*- coding: utf-8 -*-

import json

from twisted.internet import defer, protocol

from txamqp.protocol import AMQClient
from txamqp.client import TwistedDelegate
from txamqp.content import Content
import txamqp.spec

from tippresence import aggregate_status


class AMQPublisher(object):
    exchange_name = 'presence'
    routing_key = 'status_changes'

    def __init__(self, factory, presence_service):
        presence_service.watch(self.statusChanged)
        self.factory = factory

    @defer.inlineCallbacks
    def statusChanged(self, resource, status):
        r = aggregate_status(status)
        msg = json.dumps([resource, r])
        yield self.factory.publish(self.exchange_name, msg, self.routing_key)


class AMQFactory(protocol.ReconnectingClientFactory):
    VHOST = '/'

    def __init__(self, creds, specpath):
        self.spec = txamqp.spec.load(specpath)
        self.creds = creds
        self.client = None
        self.channel  = None

    def buildProtocol(self, addr):
        self.resetDelay()
        delegate = TwistedDelegate()
        self.client = AMQClient(delegate=delegate, vhost=self.VHOST, spec=self.spec)
        self.client.start(self.creds)
        return self.client

    @defer.inlineCallbacks
    def publish(self, exchange, msg, routing_key):
        if not self.client:
            raise NotImplementedError
        if not self.channel:
            yield self._createChannel()
        content = Content(msg)
        yield self.channel.basic_publish(exchange=exchange, content=content, routing_key=routing_key)

    @defer.inlineCallbacks
    def _createChannel(self):
        self.channel = yield self.client.channel(1)
        yield self.channel.channel_open()

