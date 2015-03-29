#!/usr/bin/env python
# encoding: utf-8

import collections
import asyncio

import logging
logging.basicConfig(level=logging.DEBUG)
import serial

_serials = {}

DEF_HIGHWATER = 1024


@asyncio.coroutine
def create_connection(loop, protocol_factory, host=None, port=None, *,
                      local_addr=None):
    # NOT FUNCTIONNAL RIGHT NOW
    # Server or client defined by protocol
    protocol = protocol_factory()
    waiter = asyncio.futures.Future(loop=loop)
    # use the future to create the transport
    transport = None
    make_transport(port, protocol, waiter)
    yield from waiter
    return transport, protocol


@asyncio.coroutine
def create_datagram_endpoint(loop,
                             protocol_factory,
                             local_addr=None,
                             remote_addr=None):
    # NOT FUNCTIONNAL RIGHT NOW
    # Server or client defined by protocol
    transport = None
    protocol = protocol_factory
    return transport, protocol


def _create_server(loop, protocol_factory, serialport):
    if serialport not in _serials:
        serialproto = protocol_factory()
        transport = SerialTransport(loop,
                                    serialport,
                                    serialproto,
                                    None)
        server = SerialServer(loop, serialproto, transport)
        _serials[serialport] = (server, serialproto, transport)
        # yield from server.serve()
        return server
    else:
        return _serials['serialport']

def create_server(loop, protocol_factory, serialport):
    # NOT FUNCTIONNAL RIGHT NOW
    lowlevelserver = _create_server(loop, SerialProtocol, serialport)
    transport = SerialBaseTransport(loop,
                                    serialport,
                                    protocol,
                                    serialprotocol)
    # Create server
    # add the reader in this server


class SerialServer:
    def __init__(self, loop, protocol, transport):
        self.loop = loop
        self.protocol = protocol
        self.transport = transport
        self.future = None

    @asyncio.coroutine
    def serve(self):
        self.future = asyncio.Future()
        serialport = self.transport.get_serial()
        self.loop.add_reader(serialport, self.transport._read_ready)
        yield from asyncio.wait([self.future])

    def close(self):
        self.transport.close()
        self.protocol.connection_lost(None)
        self.future.set_result(True)

    @asyncio.coroutine
    def wait_closed(self):
        pass


class SerialBaseTransport(asyncio.DatagramTransport):
    """
    NOT FUNCTIONNAL RIGHT NOW
    Compute crc in and out
    Add the frame separator
    Escape if needed
    """

    def __init__(self, loop, serialport, protocol, serialprotocol, extra=None):
        self._loop = loop
        self._paused = False
        self._closing = False
        self._protocol = protocol
        self._serialtransport = None
        self._serialprotocol = serialprotocol
        self._inbuffer = []
        #self._reader = self._loop.create_task(self.read_serial())
        self._loop.call_soon(self._protocol.connection_made, self)

    @asyncio.coroutine
    def read_serial(self):
        while True:
            data = yield from self._serialprotocol.get_data()
            if data is None:
                continue
            self._inbuffer.append(data)
            # DO something here (try framing)
            # self._loop.call_soon
            print("trying to frame")
            self._loop.call_soon(self.baseframer)

    def baseframer(self):
        raise NotImplementedError()

    def abort(self):
        print("Aborting Transport")
        self._reader.cancel()
        self._serialprotocol._transport.abort()
        del(self._inbuffer[:])
        # remove writer somewhere here

    def close(self):
        print("Closing Transport")
        if self._closing:
            return
        self._closing = True
        self._reader.cancel()

    def pause_reading(self):
        assert not self._closing, 'Cannot pause_reading() when closing'
        assert not self._paused, 'Already paused'
        self._paused = True
        self._reader.cancel()

    def resume_reading(self):
        assert self._paused, 'Not paused'
        self._paused = False
        if self._closing:
            return
        self._reader = self._loop.create_task(self.read_serial())

    def write(self, data):
        assert isinstance(data, bytes), repr(type(data))
        if not data:
            return
        self._loop.call_soon(self._serialprotocol.send, data)

    def can_write_eof(self):
        return False


class SerialProtocol(asyncio.Protocol):
    def __init__(self, reader=None):
        self.in_buffer = collections.deque()
        self._out_buffer = collections.deque()
        self._reader = reader
        self.readflag = asyncio.Event()

    def connection_made(self, transport):
        self._transport = transport
        print("Connection made in serial port")

    def data_received(self, data):
        self.in_buffer.extend(data)
        self.readflag.set()

    @asyncio.coroutine
    def get_data(self):
        yield from self.readflag.wait()
        try:
            return self.in_buffer.popleft()
        except IndexError:
            self.readflag.clear()

    def connection_lost(self, exc):
        print("Connection lost")
        print(b''.join(self.in_buffer))

    def send(self, data):
        self._transport.write(data)


class SerialTransport(asyncio.Transport):
    max_size = 1024

    def __init__(self, loop, serialport, protocol, extra):
        super().__init__(extra)
        self._loop = loop
        self._paused = False
        self._closing = False
        self._buffer = collections.deque()
        self._serial = serial.Serial(serialport)
        self._fileno = self._serial.fileno()
        self._protocol = protocol
        self._loop.call_soon(self._protocol.connection_made, self)

    def _read_ready(self):
        data = self._serial.read(self._serial.inWaiting())
        # Smelling code ahead
        # TODO: there should be a better way to do that
        print(data)
        self._protocol.data_received([bytes([datum]) for datum in data])

    def close(self):
        print("Closing Transport")
        if self._closing:
            return
        self._closing = True
        self._loop.remove_reader(self._fileno)
        if not self._buffer:
            self._loop.call_soon(self._send_conn_lost, None)
        self._serial.close()

    def pause_reading(self):
        assert not self._closing, 'Cannot pause_reading() when closing'
        assert not self._paused, 'Already paused'
        self._paused = True
        self._loop.remove_reader(self._fileno)

    def resume_reading(self):
        assert self._paused, 'Not paused'
        self._paused = False
        if self._closing:
            return
        self._loop.add_reader(self._serial, self._read_ready)

    def set_write_buffer_limits(self, high=None, low=None):
        if high is None:
            if low is None:
                high = DEF_HIGHWATER
            else:
                high = 4 * low
        if low is None:
            low = high // 4
        assert 0 <= low <= high, repr((low, high))
        self._high_water = high
        self._low_water = low

    def get_write_buffer_size(self):
        return sum(len(data) for data in self._buffer)

    def write(self, data):
        assert isinstance(data, bytes), repr(type(data))
        if not data:
            return
        self._buffer.append(data)
        self._loop.add_writer(self._fileno, self._send_toserial)
        # self._loop.call_soon(self._send_toserial)

    def _send_toserial(self):
        data = b''.join(self._buffer)
        self._buffer.clear()
        self._serial.write(data)
        if not self._buffer:
            self._loop.remove_writer(self._fileno)
            if self._closing:
                self._serial.close()

    def can_write_eof(self):
        return False

    def abort(self):
        print("Aborting Transport")
        self._buffer.clear()
        self._loop.remove_writer(self._fileno)
        self._loop.call_soon(self._send_conn_lost, None)

    def _send_conn_lost(self, args):
        print("Sending conn lost")
        try:
            self._loop.call_soon(self._protocol.connection_lost, args)
        except Exception as e:
            print(e)
        finally:
            self._serial.close()
            self._serial = None
            self._fileno = None
            self._protocol = None
            # self._loop = None

    def get_serial(self):
        return self._serial


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.DEBUG)

    @asyncio.coroutine
    def test_send():
        for a in range(100,110):
            _serials['/tmp/master'][1].send('abcdefghijkl'.encode('utf8'))
            yield from asyncio.sleep(1)
    loop = asyncio.get_event_loop()
    server = _create_server(loop, SerialProtocol, '/tmp/master')
    coro = server.serve()
    try:
        loop.create_task(test_send())
        loop.run_until_complete(coro)
    except KeyboardInterrupt:
        # server.close()
        coro.close()
        server.close()
        # time.sleep(1)
        print('OK')
        loop.close()
