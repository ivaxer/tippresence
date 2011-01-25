# -*- coding: utf-8 -*-

import re
from collections import defaultdict

from twisted.internet import reactor, defer

from tippresence import aggregate_status
from tipsip import SIPUA, SIPError
from tipsip.header import Header


s2p = {
        'online':   'open',
        'offline':  'closed',
        }

def status2pidf(resource, statuses):
    pidf = []
    a = pidf.append
    status = aggregate_status(statuses)
    a('<?xml version="1.0" encoding="UTF-8"?>')
    a('<presence xmlns="urn:ietf:params:xml:ns:pidf" entity="pres:%s">' % resource)
    s = s2p[status['presence']['status']]
    a('\t<tuple id="%s">' % resource)
    a('\t\t<status>')
    a('\t\t\t<basic>%s</basic>' % s)
    a('\t\t</status>')
    a('\t\t<contact>sip:%s</contact>' % resource)
    a('\t</tuple>')
    a('</presence>')
    return '\n'.join(pidf)


class SIPPresence(SIPUA):
    DEFAULT_PUBLISH_EXPIRES = 3600
    MIN_PUBLISH_EXPIRES = 60
    WATCHERS_SET_NAME = 'sys:watchers_by_resource:%s'
    RESOURCE_BY_WATCHER = 'sys:resource_by_watcher'
    WATCHER_TIMERS = 'sys:watcher_timers'

    online_re = re.compile('.*<status><basic>open</basic></status>.*')

    def __init__(self, storage, dialog_store, transport, presence_service):
        SIPUA.__init__(self, dialog_store, transport)
        storage.addCallbackOnConnected(self._loadWatcherTimers)
        self.storage = storage
        presence_service.watch(self.statusChangedCallback)
        self.presence_service = presence_service
        self.watcher_expires_tid = {}

    @defer.inlineCallbacks
    def handle_PUBLISH(self, publish):
        resource = publish.ruri.user + '@' + publish.ruri.host
        expires = publish.headers.get('expires', self.DEFAULT_PUBLISH_EXPIRES)
        expires = int(expires)
        pidf = publish.content
        tag = publish.headers.get('SIP-If-Match')
        if publish.headers.get('Event') != 'presence':
            response = publish.createResponse(489, 'Bad Event')
            response.headers['allow-event'] = 'presence'
            self.sendResponse(response)
            defer.returnValue(None)
        if  pidf and publish.headers.get('content-type') != 'application/pidf+xml':
            response = publish.createResponse(415, 'Unsupported Media Type')
            response.headers['accept'] = 'application/pidf+xml'
            self.sendResponse(response)
            defer.returnValue(None)
        if expires and expires < self.MIN_PUBLISH_EXPIRES:
            raise SIPError(423, 'Interval Too Brief')

        if expires == 0:
            r = yield self.presence_service.removeStatus(resource, tag)
            if r == 'not_found':
                raise SIPError(412, 'Conditional Request Failed')
        elif tag:
            r = yield self.presence_service.updateStatus(resource, tag, expires)
            if r == 'not_found':
                raise SIPError(412, 'Conditional Request Failed')
        else:
            tag = yield self.putStatus(resource, pidf, expires, tag)
        response = publish.createResponse(200, 'OK')
        response.headers['SIP-ETag'] = tag
        response.headers['Expires'] = str(expires)
        self.sendResponse(response)

    @defer.inlineCallbacks
    def putStatus(self, resource, pidf, expires, tag):
        pidf = ''.join(pidf.split())
        if self.online_re.match(pidf):
            presence = {'status': 'online'}
        else:
            presence = {'status': 'offline'}
        tag = yield self.presence_service.putStatus(resource, presence, expires, tag=tag)
        defer.returnValue(tag)

    @defer.inlineCallbacks
    def handle_SUBSCRIBE(self, subscribe):
        if subscribe.headers.get('Event') != 'presence':
            response = subscribe.createResponse(489, 'Bad Event')
            response.headers['allow-event'] = 'presence'
            self.sendResponse(response)
        elif not subscribe.dialog and subscribe.has_totag:
            raise SIPError(481, 'Call/Transaction Does Not Exist')
        else:
            yield self.processSubscription(subscribe)

    @defer.inlineCallbacks
    def statusChangedCallback(self, resource, status):
        watchers = yield self._getResourceWatchers(resource)
        if not watchers:
            return
        for watcher in watchers:
            self.notifyWatcher(watcher)

    @defer.inlineCallbacks
    def processSubscription(self, subscribe):
        expires = int(subscribe.headers['Expires'])
        if not expires and subscribe.dialog:
            watcher = subscribe.dialog.id
            yield self.notifyWatcher(watcher, status='terminated', expires=0)
            yield self.removeWatcher(watcher)
        elif subscribe.dialog:
            watcher = subscribe.dialog.id
            yield self.updateWatcher(watcher, expires)
            yield self.notifyWatcher(watcher, status='active', expires=expires, dialog=subscribe.dialog)
        else:
            if not subscribe.ruri.user:
                raise SIPError(404, 'Bad resource URI')
            resource = subscribe.ruri.user + '@' + subscribe.ruri.host
            yield self.createDialog(subscribe)
            watcher = subscribe.dialog.id
            yield self.addWatcher(watcher, resource, expires)
            yield self.notifyWatcher(watcher, status='active', expires=expires, dialog=subscribe.dialog)
        response = subscribe.createResponse(200, 'OK')
        response.headers['Expires'] = str(expires)
        self.sendResponse(response)

    @defer.inlineCallbacks
    def addWatcher(self, watcher, resource, expires):
        yield self._addResourceWatcher(resource, watcher)
        yield self._setWatcherTimer(watcher, expires)

    @defer.inlineCallbacks
    def updateWatcher(self, watcher, expires):
        if watcher not in self.watcher_expires_tid:
            raise SIPError(500, "Server Internal Error")
        yield self._setWatcherTimer(watcher, expires)

    @defer.inlineCallbacks
    def removeWatcher(self, watcher):
        if watcher not in self.watcher_expires_tid:
            raise SIPError(404, 'Not Found')
        resource = yield self._getResourceByWatcher(watcher)
        yield self._removeResourceWatcher(resource, watcher)
        yield self.removeDialog(id=watcher)
        yield self._cancelWatcherTimer(watcher)

    @defer.inlineCallbacks
    def notifyWatcher(self, watcher, pidf=None, dialog=None, status='active', expires=None):
        if pidf is None:
            resource = yield self._getResourceByWatcher(watcher)
            statuses = yield self.presence_service.getStatus(resource)
            pidf = status2pidf(resource, statuses)
        if dialog is None:
            dialog = yield self.dialog_store.get(watcher)
            if not dialog:
                raise SIPError(500, "Server Internal Error")
        if expires is None:
            expires = self.watcher_expires_tid[watcher].getTime() - reactor.seconds()
            expires = int(expires)
        yield self.sendNotify(dialog, pidf, status, expires)

    @defer.inlineCallbacks
    def sendNotify(self, dialog, pidf, status, expires):
        notify = dialog.createRequest('NOTIFY')
        h = notify.headers
        h['subscription-state'] = Header(status, {'expires': str(expires)})
        h['content-type'] = 'application/pidf+xml'
        h['Event'] = 'presence'
        notify.content = pidf
        yield self.sendRequest(notify)

    @defer.inlineCallbacks
    def _getResourceWatchers(self, resource):
        s = self.WATCHERS_SET_NAME % resource
        try:
            watchers = yield self.storage.sgetall(s)
        except KeyError:
            defer.returnValue(None)
        r = [tuple(w.split(':')) for w in watchers]
        defer.returnValue(r)

    @defer.inlineCallbacks
    def _addResourceWatcher(self, resource, watcher):
        w = ':'.join(watcher)
        s = self.WATCHERS_SET_NAME % resource
        yield self.storage.sadd(s, w)
        yield self.storage.hset(self.RESOURCE_BY_WATCHER, w, resource)

    @defer.inlineCallbacks
    def _removeResourceWatcher(self, resource, watcher):
        s = self.WATCHERS_SET_NAME % resource
        w = ':'.join(watcher)
        yield self.storage.srem(s, w)
        yield self.storage.hdel(self.RESOURCE_BY_WATCHER, w)

    @defer.inlineCallbacks
    def _getResourceByWatcher(self, watcher):
        w = ':'.join(watcher)
        r = yield self.storage.hget(self.RESOURCE_BY_WATCHER, w)
        defer.returnValue(r)

    @defer.inlineCallbacks
    def _setWatcherTimer(self, watcher, delay, memonly=False):
        if watcher in self.watcher_expires_tid:
            self.watcher_expires_tid[watcher].reset(delay)
        else:
            self.watcher_expires_tid[watcher] = reactor.callLater(delay, self.removeWatcher, watcher)
        if not memonly:
            w = ':'.join(watcher)
            expiresat = reactor.seconds() + delay
            yield self.storage.hset(self.WATCHER_TIMERS, w, expiresat)

    @defer.inlineCallbacks
    def _cancelWatcherTimer(self, watcher):
        if watcher not in self.watcher_expires_tid:
            defer.returnValue(None)
        w = ':'.join(watcher)
        yield self.storage.hdel(self.WATCHER_TIMERS, w)
        tid = self.watcher_expires_tid.pop(watcher)
        if tid.active():
            tid.cancel()

    @defer.inlineCallbacks
    def _loadWatcherTimers(self):
        try:
            timers = yield self.storage.hgetall(self.WATCHER_TIMERS)
        except KeyError:
            defer.returnValue(None)
        for w, expiresat in timers.iteritems():
            expires = float(expiresat) - reactor.seconds()
            if expires <= 0:
                yield self.storage.hdel(self.WATCHER_TIMERS, w)
            else:
                watcher = tuple(w.split(':'))
                yield self._setWatcherTimer(watcher, expires, memonly=True)

