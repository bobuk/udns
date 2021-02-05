from typing import Optional, Tuple, List
import struct
import asyncio
import pathlib


def parse_query(data: bytes) -> Tuple[int, List[bytes]]:
    header = data[:12]
    payload = data[12:]

    (
        transaction_id,
        flags,
        num_queries,
        num_answers,
        num_authority,
        num_additional,
    ) = struct.unpack(">6H", header)

    queries: List[bytes] = []
    for i in range(num_queries):
        res = payload.index(0) + 5
        queries.append(payload[:res])
        payload = payload[res:]

    return transaction_id, queries


def get_domain(query: bytes) -> str:
    parts = []
    while True:
        length = query[0]
        query = query[1:]
        if length == 0:
            break
        parts.append(query[:length])
        query = query[length:]
    return ".".join(x.decode("ascii") for x in parts)


def build_answer(
    trans_id: int,
    queries: List[bytes],
    answer: Optional[bytes] = None,
    ttl: int = 128,
) -> bytes:
    flags = 0
    flags |= 0x8000
    flags |= 0x0400

    if not answer:
        flags |= 0x0003  # NXDOMAIN

    header = struct.pack(">6H", trans_id, flags, len(queries), 1 if answer else 0, 0, 0)
    payload = b"".join(queries)

    if answer:
        payload += b"\xc0\x0c\x00\x01\x00\x01\x00\x00\x00\x70\x00\x04" + answer
    return header + payload


def get_default_resolver(resolv_conf: str = "/etc/resolv.conf") -> str:
    rc = pathlib.Path(resolv_conf)
    if rc.is_file():
        with rc.open() as file:
            while line := file.readline():
                parsed = line.strip().split("#", 1)[0].split()
                if len(parsed) == 2 and parsed[0] == "nameserver":
                    return parsed[1]
    return "8.8.8.8"


class DNSForward(asyncio.DatagramProtocol):
    def __init__(self, message: bytes, on_exit: asyncio.Future):
        self.message = message
        self.on_exit = on_exit
        self.result: Optional[bytes] = None
        self.transport: asyncio.DatagramTransport = asyncio.DatagramTransport()

    def datagram_received(self, data: Optional[bytes], addr: Tuple[str, int]):
        self.result = data
        if self.transport:
            self.transport.close()

    def connection_made(self, transport: asyncio.DatagramTransport):  # type: ignore[override]
        self.transport = transport
        self.transport.sendto(self.message)

    def error_received(self, exc):
        pass

    def connection_lost(self, exc):
        self.on_exit.set_result(self.result)
