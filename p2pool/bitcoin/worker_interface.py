from __future__ import division

import base64
import random

from twisted.internet import defer

import p2pool
from p2pool import data as p2pool_data
from p2pool.util import jsonrpc, variable
from p2pool.bitcoin import getwork

def get_memory(request):
    if request.getHeader('X-Miner-Extensions') is not None and 'workidentifier' in request.getHeader('X-Miner-Extensions').split(' '):
        return 0
    if request.getHeader('X-Work-Identifier') is not None:
        return 0
    user_agent = request.getHeader('User-Agent')
    user_agent2 = '' if user_agent is None else user_agent.lower()
    if 'java' in user_agent2 or 'diablominer' in user_agent2: return 0 # hopefully diablominer...
    if 'cpuminer' in user_agent2: return 0
    if 'tenebrix miner' in user_agent2: return 0
    if 'ufasoft' in user_agent2: return 0 # not confirmed
    if 'cgminer' in user_agent2: return 0
    if 'jansson' in user_agent2: return 0 # a version of optimized scrypt miner, once in Wuala. works fine here
    if 'poclbm' in user_agent2: return 1
    if 'phoenix' in user_agent2: return 2
    print 'Unknown miner User-Agent:', repr(user_agent)
    return 0

def get_username(request):
    try:
        return base64.b64decode(request.getHeader('Authorization').split(' ', 1)[1]).split(':')[0]
    except: # XXX
        return None

def get_id(request):
    return request.getClientIP(), request.getHeader('Authorization')

class LongPollingWorkerInterface(jsonrpc.Server):
    def __init__(self, parent):
        jsonrpc.Server.__init__(self)
        self.parent = parent
    
    def rpc_getwork(self, request, data=None):
        return self.parent.getwork(request, data, long_poll=True)

class WorkerInterface(jsonrpc.Server):
    def __init__(self, compute, response_callback, new_work_event=variable.Event()):
        jsonrpc.Server.__init__(self)
        
        self.compute = compute
        self.response_callback = response_callback
        self.new_work_event = new_work_event
        
        self.worker_views = {}
        
        self.putChild('long-polling', LongPollingWorkerInterface(self))
        self.putChild('', self)
    
    def rpc_getwork(self, request, data=None):
        return self.getwork(request, data, long_poll=False)
    
    @defer.inlineCallbacks
    def getwork(self, request, data, long_poll):
        request.setHeader('X-Long-Polling', '/long-polling')
        request.setHeader('X-Roll-NTime', 'expire=60')
        
        if data is not None:
            defer.returnValue(self.response_callback(getwork.decode_data(data), request))
        
        request_id = get_id(request)
        memory = get_memory(request)
        
        if p2pool.DEBUG:
            id = random.randrange(1000, 10000)
            print 'POLL %i START long_poll=%r user_agent=%r x-work-identifier=%r user=%r' % (id, long_poll, request.getHeader('User-Agent'), request.getHeader('X-Work-Identifier'), get_username(request))
        
        if request_id not in self.worker_views:
            self.worker_views[request_id] = variable.Variable((0, (None, None))) # times, (previous_block/-1, previous_block/-2)
        
        thought_times, thought_work = self.worker_views[request_id].value
        
        if long_poll and thought_times == self.new_work_event.times:
            if p2pool.DEBUG:
                print 'POLL %i WAITING user=%r' % (id, get_username(request))
            yield defer.DeferredList([self.new_work_event.get_deferred(), self.worker_views[request_id].changed.get_deferred()], fireOnOneCallback=True)
        
        res, identifier = self.compute(request)
        
        if thought_work[-1] is not None and self.new_work_event.times != thought_times and any(x is None or res.previous_block == x for x in thought_work[-memory or len(thought_work):]):
            # clients won't believe the update
            res = res.update(previous_block=random.randrange(2**256))
            if p2pool.DEBUG:
                print 'POLL %i FAKED user=%r' % (id, get_username(request))
        
        self.worker_views[request_id].set((self.new_work_event.times if long_poll else thought_times, (thought_work[-1], res.previous_block)))
        if p2pool.DEBUG:
            print 'POLL %i END %s user=%r' % (id, p2pool_data.format_hash(identifier), get_username(request)) # XXX identifier is hack
        
        defer.returnValue(res.getwork(identifier=str(identifier)))
