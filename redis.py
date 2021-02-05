#!/usr/bin/env python3

from typing import Optional, Tuple
import asyncio
from collections import deque


class RedisException(Exception):
    """Any exception from Redis"""


class Redis:
    def __init__(self, host: str = "127.0.0.1", port: int = 6379, db: int = 0):
        self.host = host
        self.port = port
        self.db = db

        self.connection: Optional[
            Tuple[asyncio.StreamReader, asyncio.StreamWriter]
        ] = None

    async def execute(self, cmd: str = "") -> Optional[bytes]:
        if not self.connection:
            self.connection = await asyncio.open_connection(self.host, self.port)
            if len(self.connection) != 2:
                raise RedisException(f"can't connect to {self.host}:{self.port}")
            if self.db != 0:
                await self.execute("select " + str(self.db))
        command = (cmd + "\r\n").encode()
        self.connection[1].write(command)
        res = (await self.connection[0].read(128)).rstrip()
        if res == b"$-1":
            return None
        if res[0] in (ord("+"), ord("-")):
            return None
        length, value = res.split(b"\r\n")
        return value

    async def close(self):
        if self.connection:
            self.connection[1].close()


class RedisPool:
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 6379,
        db: int = 0,
        pool_size: int = 20,
    ):
        self.host = host
        self.port = port
        self.db = db
        self.size = pool_size
        self.queue: deque = deque()
        self.sem = asyncio.Semaphore(self.size)
        for i in range(self.size):
            self.queue.append(Redis(host, port, db))

    async def execute(self, cmd: str) -> Optional[str]:
        async with self.sem:
            conn = self.queue.popleft()
            res = await conn.execute(cmd)
            self.queue.append(conn)
            return res.decode() if res else None

    async def get(self, key: str) -> Optional[str]:
        return await self.execute("GET " + key)
