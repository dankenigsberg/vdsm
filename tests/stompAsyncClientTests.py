#
# Copyright 2015 Red Hat, Inc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#
from uuid import uuid4

from testlib import VdsmTestCase as TestCaseBase
from yajsonrpc.stomp import AsyncClient, Command, Frame, Headers, StompError
from stompAdapterTests import TestSubscription


class AsyncClientTest(TestCaseBase):

    def test_connect(self):
        client = AsyncClient()
        client.handle_connect()

        req_frame = client.pop_message()

        self.assertEquals(req_frame.command, Command.CONNECT)
        self.assertEquals(req_frame.headers[Headers.ACCEPT_VERSION], '1.2')
        self.assertEquals(req_frame.headers[Headers.HEARTEBEAT], '0,5000')

    def test_subscribe(self):
        client = AsyncClient()

        id = str(uuid4())
        client.subscribe(destination='jms.queue.events', ack='client',
                         sub_id=id)

        req_frame = client.pop_message()
        self.assertTrue(len(client._subscriptions) == 1)
        self.assertEquals(req_frame.command, Command.SUBSCRIBE)
        self.assertEquals(req_frame.headers['destination'], 'jms.queue.events')
        self.assertEquals(req_frame.headers['id'], id)
        self.assertEquals(req_frame.headers['ack'], 'client')

    def test_manage_subscription(self):
        client = AsyncClient()

        subscription = client.subscribe(destination='jms.queue.events',
                                        ack='client',
                                        sub_id=str(uuid4()))
        client.unsubscribe(subscription)
        self.assertTrue(len(client._subscriptions) == 0)

    def test_unsubscribe_with_different_id(self):
        client = AsyncClient()

        client.subscribe(destination='jms.queue.events',
                         ack='client-individual',
                         sub_id=str(uuid4()))
        # ignore subscribe frame
        client.pop_message()

        client.unsubscribe(TestSubscription('jms.queue.events',
                                            'ad052acb-a934-4e10-8ec3'))

        self.assertTrue(len(client._subscriptions) == 1)
        self.assertFalse(client.has_outgoing_messages)

    def test_send(self):
        client = AsyncClient()
        data = ('{"jsonrpc":"2.0","method":"Host.getAllVmStats","params":{},'
                '"id":"e8a936a6-d886-4cfa-97b9-2d54209053ff"}')
        headers = {Headers.REPLY_TO: 'jms.topic.vdsm_responses',
                   Headers.CONTENT_LENGTH: '103'}
        # make sure that client can send messages
        client._connected.set()

        client.send('jms.topic.vdsm_requests', data, headers)

        req_frame = client.pop_message()
        self.assertEquals(req_frame.command, Command.SEND)
        self.assertEquals(req_frame.headers['destination'],
                          'jms.topic.vdsm_requests')
        self.assertEquals(req_frame.headers[Headers.REPLY_TO],
                          'jms.topic.vdsm_responses')
        self.assertEquals(req_frame.body, data)

    def test_receive_connected(self):
        client = AsyncClient()
        frame = Frame(Command.CONNECTED,
                      {'version': '1.2', Headers.HEARTEBEAT: '8000,0'})

        client.handle_frame(None, frame)

        self.assertTrue(client.connected)

    def test_receive_message(self):
        client = AsyncClient()
        id = 'ad052acb-a934-4e10-8ec3-00c7417ef8d1'
        headers = {Headers.CONTENT_LENGTH: '78',
                   Headers.DESTINATION: 'jms.topic.vdsm_responses',
                   Headers.CONTENT_TYPE: 'application/json',
                   Headers.SUBSCRIPTION: id}
        body = ('{"jsonrpc": "2.0", "id": "e8a936a6-d886-4cfa-97b9-2d54209053f'
                'f", "result": []}')
        frame = Frame(command=Command.MESSAGE, headers=headers, body=body)

        def message_handler(sub, frame):
            client.queue_frame(frame)

        client.subscribe('', 'auto', id, message_handler)
        # ignore subscribe frame
        client.pop_message()

        client.handle_frame(None, frame)

        self.assertEquals(frame, client.pop_message())

    def test_receive_error(self):
        client = AsyncClient()
        frame = Frame(command=Command.ERROR, body='Test error')

        with self.assertRaises(StompError):
            client.handle_frame(None, frame)
