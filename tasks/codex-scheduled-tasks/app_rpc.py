"""Codex App Server transport; scheduler logs only lifecycle metadata."""
import asyncio
import contextlib
import json
import os
from pathlib import Path


class RPCError(RuntimeError):
    def __init__(self, error):
        self.error = error
        super().__init__(error.get('message', 'RPC failed'))


class AppServer:
    """Keep native auth, stream turns, and reject interactive requests unattended."""

    def __init__(self, binary, folder, env=None):
        self.binary, self.folder, self.env = binary, Path(folder), env or dict(os.environ)
        self.pending, self.events, self.index = {}, asyncio.Queue(), 0

    async def __aenter__(self):
        self.stderr = (self.folder / 'app-server.stderr.log').open('a')
        self.proc = await asyncio.create_subprocess_exec(
            self.binary, 'app-server', stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=self.stderr, env=self.env,
            limit=16 * 1024 * 1024)
        self.reader = asyncio.create_task(self.receive())
        try:
            await self.send('initialize', {'clientInfo': {
                'name': 'macmini_scheduled_tasks', 'title': 'Mac mini scheduled tasks', 'version': '1'},
                'capabilities': {'experimentalApi': True}})
            await self.send('initialized', {}, notification=True)
            return self
        except BaseException:
            await self.__aexit__(None, None, None)
            raise

    async def receive(self):
        try:
            while line := await self.proc.stdout.readline():
                message = json.loads(line)
                if 'id' in message and 'method' not in message:
                    future = self.pending.pop(message['id'], None)
                    if future and not future.done():
                        future.set_result(message)
                elif 'id' in message:
                    self.proc.stdin.write((json.dumps({'id': message['id'], 'error': {
                        'code': -32601, 'message': 'Scheduled task: interactive requests unavailable'}}) + '\n').encode())
                    await self.proc.stdin.drain()
                else:
                    await self.events.put(message)
        finally:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(RuntimeError('App Server disconnected'))

    async def send(self, method, params, notification=False):
        message = {'method': method, 'params': params}
        future = None
        if not notification:
            self.index += 1
            message['id'] = self.index
            future = asyncio.get_running_loop().create_future()
            self.pending[self.index] = future
        self.proc.stdin.write((json.dumps(message, ensure_ascii=False) + '\n').encode())
        await self.proc.stdin.drain()
        if future is None:
            return None
        result = await asyncio.wait_for(future, 120)
        if 'error' in result:
            raise RPCError(result['error'])
        return result['result']

    async def __aexit__(self, *_):
        self.reader.cancel()
        with contextlib.suppress(asyncio.CancelledError, ValueError):
            await self.reader
        self.proc.stdin.close()
        try:
            await asyncio.wait_for(self.proc.communicate(), 8)
        except asyncio.TimeoutError:
            self.proc.kill()
            await self.proc.wait()
        self.stderr.close()
