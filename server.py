#!/usr/bin/env python3

import asyncio
import socket
import ipaddress
from os import environ as env
from collections import deque
from typing import Optional, List, Tuple
from dnslib import *
from redis import RedisPool

NO_UVLOOP = env.get("NO_UVLOOP", 0)

if NO_UVLOOP:
    import uvloop  # type: ignore

    uvloop.install()

DB = RedisPool(env.get("REDIS", "127.0.0.1"), db=int(env.get("REDIS_DB", 0)))
DNS_RELAY = (env.get("DNS_RELAY", get_default_resolver()), 53)
HOST = env.get("BIND", "0.0.0.0")


class DNSServer:
    def __init__(self, loop=asyncio.get_event_loop()):
        self.loop = loop

        self.sock = None
        self.event = asyncio.Event()
        self.queue = deque()

    async def on_data_received(self, data: bytes, addr: Tuple[str, int]):
        trans_id, queries = parse_query(data)
        for q in queries:
            domain = get_domain(q)
            # print(trans_id, domain)
            res = await DB.get(domain)
            if not res and "." in domain:
                on_exit = self.loop.create_future()
                transport, _ = await self.loop.create_datagram_endpoint(
                    lambda: DNSForward(data, on_exit), remote_addr=DNS_RELAY
                )
                try:
                    self.send(await on_exit, addr)
                finally:
                    transport.close()
                return
            ip = ipaddress.IPv4Address(res).packed if res else None
            self.send(build_answer(trans_id, queries, answer=ip), addr)

    def run(self, host: str = "0.0.0.0", port: int = 53):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, 0)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.setblocking(False)
        self.sock.bind((host, port))
        asyncio.ensure_future(self.recv_periodically(), loop=self.loop)
        asyncio.ensure_future(self.send_periodically(), loop=self.loop)

    def sock_recv(
        self, fut: Optional[asyncio.Future] = None, registered: bool = False
    ) -> Optional[asyncio.Future]:
        fd = self.sock.fileno()

        if not fut:
            fut = self.loop.create_future()

        if fut:
            if registered:
                self.loop.remove_reader(fd)
            try:
                data, addr = self.sock.recvfrom(2048)
            except (BlockingIOError, InterruptedError):
                self.loop.add_reader(fd, self.sock_recv, fut, True)
            except Exception as ex:
                print(ex)
                fut.set_result(0)
            else:
                fut.set_result((data, addr))
        return fut

    async def recv_periodically(self):
        while True:
            data, addr = await self.sock_recv()
            asyncio.ensure_future(self.on_data_received(data, addr), loop=self.loop)

    def send(self, data: bytes, addr: Tuple[str, int]):
        self.queue.append((data, addr))
        self.event.set()

    def sock_send(
        self,
        data: bytes,
        addr: Tuple[str, int],
        fut: Optional[asyncio.Future] = None,
        registered: bool = False,
    ) -> Optional[asyncio.Future]:
        fd = self.sock.fileno()
        if not fut:
            fut = self.loop.create_future()
        if fut:
            if registered:
                self.loop.remove_writer(fd)

            try:
                sent = self.sock.sendto(data, addr)
            except (BlockingIOError, InterruptedError):
                self.loop.add_writer(fd, self.sock_send, data, addr, fut, True)
            except Exception as ex:
                print(ex)
                fut.set_result(0)
            else:
                fut.set_result(sent)
        return fut

    async def send_periodically(self):
        while True:
            await self.event.wait()
            try:
                while self.queue:
                    data, addr = self.queue.popleft()
                    _ = await self.sock_send(data, addr)
            finally:
                self.event.clear()


async def main(loop):
    dns = DNSServer(loop)
    dns.run(host=HOST)


if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main(loop))
    loop.run_forever()
