"""Every test is offline. Real subprocess creation and external sockets are forbidden."""

import asyncio
import socket
import subprocess
from pathlib import Path

import pytest

from auto_windscribe.models import Candidate, Location, Profile
from auto_windscribe.storage import Paths, Store


@pytest.fixture(autouse=True)
def offline_tripwire(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError("OFFLINE TEST: real subprocess/network access is forbidden")

    async def async_forbidden(*args, **kwargs):
        forbidden()

    monkeypatch.setenv("AUTO_WINDSCRIBE_OFFLINE", "1")
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", async_forbidden)
    monkeypatch.setattr(asyncio, "create_subprocess_shell", async_forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg-cache"))


@pytest.fixture
def candidate():
    return Candidate(
        Location("Test Region", "Test City", "Example", country="Test Country"), "stealth", 443
    )


@pytest.fixture
def profile():
    return Profile(protocols=["stealth"], settle=0, timeout=3, interval=1)


@pytest.fixture
def paths(tmp_path):
    paths = Paths.under(tmp_path)
    paths.ensure()
    return paths


@pytest.fixture
def store(paths):
    result = Store(paths.data / "history.sqlite3")
    yield result
    result.close()


def fixture_text(name: str) -> str:
    return (Path(__file__).parent / "fixtures" / name).read_text()
