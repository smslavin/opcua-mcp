"""Fixtures: a real OPC-UA simulator (simulator.py) and the server's connection pointed at it."""

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

import server

ROOT = Path(__file__).resolve().parent.parent


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _listening(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.2)
        return s.connect_ex(("127.0.0.1", port)) == 0


class Simulator:
    """simulator.py in a child process, restartable on the same port."""

    def __init__(self):
        self.port = _free_port()
        self.url = f"opc.tcp://127.0.0.1:{self.port}/avevawaterSimulator"
        self._env = {**os.environ, "OPCUA_PORT": str(self.port),
                     "FAULT_HTTP_PORT": str(_free_port()), "PUBLISH_INTERVAL": "0.5"}
        self._proc: subprocess.Popen | None = None

    def start(self) -> None:
        self._proc = subprocess.Popen(
            [sys.executable, "simulator.py"], cwd=ROOT, env=self._env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + 30
        while not _listening(self.port):
            if self._proc.poll() is not None:
                raise RuntimeError(f"simulator.py exited with {self._proc.returncode}")
            if time.monotonic() > deadline:
                raise RuntimeError("simulator.py did not start listening within 30 s")
            time.sleep(0.2)

    def stop(self) -> None:
        if self._proc is not None:
            self._proc.kill()
            self._proc.wait()
            self._proc = None


@pytest.fixture(scope="session")
def simulator():
    sim = Simulator()
    sim.start()
    yield sim
    sim.stop()


@pytest.fixture
async def conn(simulator, monkeypatch):
    """The server's connection, to the session simulator. Not connected until a tool runs."""
    c = server._Connection(simulator.url)
    monkeypatch.setattr(server, "_conn", c)
    yield c
    await c.close()
