"""Approval policies: who allows a real robot to move for one job (decision D5).

- OnSiteConsole: prints the program; the operator at the robot types ENABLE (today's kit behaviour).
- SimulatorOnly: approves without asking, but only for a robot on this machine (the fake controller).
- Never: dry runs; always declines.
An `auto` policy for real robots comes later (it needs the cell's interlocks and the camera check).
"""
from __future__ import annotations

import ipaddress
import socket
from typing import Callable, Protocol


class Approval(Protocol):
    name: str

    def request(self, program_text: str, host: str) -> bool:
        """Block until the job is approved (True) or declined (False). Called before anything moves."""


def is_local_host(host: str) -> bool:
    try:
        return all(ipaddress.ip_address(a[4][0]).is_loopback for a in socket.getaddrinfo(host, None))
    except (socket.gaierror, ValueError):
        return False


class OnSiteConsole:
    name = "on-site"

    def __init__(self, ask: Callable[[str], str] = input, show: Callable[[str], None] = print):
        self._ask, self._show = ask, show

    def request(self, program_text: str, host: str) -> bool:
        self._show("\n  PROGRAM TO RUN ON THE ROBOT\n" + program_text)
        ans = self._ask("\n  Emergency stop within reach and work area clear? Type ENABLE to proceed: ")
        return ans.strip().upper() == "ENABLE"


class SimulatorOnly:
    name = "simulator-only"

    def request(self, program_text: str, host: str) -> bool:
        if not is_local_host(host):
            raise PermissionError(f"policy '{self.name}' never approves a robot at {host}: use on-site approval")
        return True


class Never:
    name = "never"

    def request(self, program_text: str, host: str) -> bool:
        return False
