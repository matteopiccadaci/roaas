"""Approval policies: who allows a real robot to move for one job (decision D5).

- OnSiteConsole: prints the program; the operator at the robot types ENABLE (today's kit behaviour).
- SimulatorOnly: approves without asking, but only for a robot on this machine (the fake controller).
- Never: dry runs; always declines.
An `auto` policy for real robots comes later (it needs the cell's interlocks and the camera check).
"""
from __future__ import annotations

import ipaddress
import os
import socket
import sys
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


def flush_typeahead(stream=None) -> None:
    """Throw away keys typed BEFORE the approval prompt appeared (as Crow's pilot bridge does), so that text typed
    earlier, or left in the buffer, can never answer it. Windows console: msvcrt; elsewhere: termios. Does nothing
    when the input is not a terminal (tests, pipes)."""
    stream = sys.stdin if stream is None else stream
    try:
        if stream is None or not stream.isatty():
            return
        if os.name == "nt":
            import msvcrt
            while msvcrt.kbhit():
                msvcrt.getwch()
        else:
            import termios
            termios.tcflush(stream.fileno(), termios.TCIFLUSH)
    except (OSError, ValueError, ImportError):
        pass


class OnSiteConsole:
    name = "on-site"

    def __init__(self, ask: Callable[[str], str] = input, show: Callable[[str], None] = print,
                 flush: Callable[[], None] = flush_typeahead):
        self._ask, self._show, self._flush = ask, show, flush

    def request(self, program_text: str, host: str) -> bool:
        self._show("\n  PROGRAM TO RUN ON THE ROBOT\n" + program_text)
        self._flush()
        ans = self._ask("\n  Emergency stop within reach and work area clear? Type ENABLE to proceed: ")
        return ans.strip().upper() == "ENABLE"


class OnSiteJobConsole:
    """On-site approval of ONE queued job. The operator must type ENABLE followed by the job's 4-character code
    (shown in the prompt), so that a prompt left on screen cannot approve another job by accident.
    DECLINE refuses the job for good; anything else leaves it waiting (`roaas ops retry` asks again)."""
    name = "on-site"

    def __init__(self, job_id: str, header: str = "", ask: Callable[[str], str] = input, show: Callable[[str], None] = print,
                 flush: Callable[[], None] = flush_typeahead):
        self.code = job_id[-4:].upper()
        self.header, self._ask, self._show, self._flush = header, ask, show, flush

    def request(self, program_text: str, host: str) -> bool:
        from .model import Declined
        self._show("\n  ======== JOB WAITING FOR APPROVAL ========\n" + self.header + "\n" + program_text)
        self._flush()                               # nothing typed before this point can answer the prompt
        ans = " ".join(self._ask(f"\n  Emergency stop within reach and work area clear?\n"
                                 f"  Type  ENABLE {self.code}  to run,  DECLINE  to refuse it,  or Enter to leave it: ").split()).upper()
        if ans == "DECLINE":
            raise Declined("declined by the operator at the robot")
        return ans == f"ENABLE {self.code}"


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
