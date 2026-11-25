"""Controller I/O for the CR3A: dashboard (port 29999) and real-time feedback recorder (port 30004).

`Dashboard` and `Recorder` are copied unchanged from the kit's episode.py (v0.4.5).
"""
import socket
import struct
import threading
import time

from .kit import dobot_proto as dp


# ---------------------------------------------------------------- dashboard (port 29999)
class Dashboard:
    def __init__(self, host, port):
        self.s = socket.create_connection((host, port), timeout=5)

    def cmd(self, text, ok_codes=(0,), quiet=False):
        """Send one command and wait for 'ErrorID,{...},Cmd();'. ok_codes=None accepts any code."""
        self.s.sendall(text.encode())
        buf = b""
        while not buf.rstrip().endswith(b";"):
            chunk = self.s.recv(4096)
            if not chunk:
                raise ConnectionError("dashboard connection closed")
            buf += chunk
        reply = buf.decode().strip()
        code = int(reply.split(",", 1)[0])
        if not quiet:
            print(f"  > {text:<55} < {reply}")
        if ok_codes is not None and code not in ok_codes:
            raise RuntimeError(f"command rejected: {text} -> {reply}")
        return reply

    def robot_mode(self):
        reply = self.cmd("RobotMode()", ok_codes=None, quiet=True)
        try:
            return int(reply.split("{", 1)[1].split("}", 1)[0])
        except (IndexError, ValueError):
            return None


# ---------------------------------------------------------------- recorder (port 30004)
class Recorder(threading.Thread):
    """Reads port 30004. Packets are framed on the fixed TestValue at byte 48: if the stream
    ever gets misaligned (e.g. the controller sends a packet of a different length), the
    recorder searches for the next TestValue and carries on, logging what it skipped."""
    NAMES = ["RobotMode", "TimeStamp", "TestValue", "SpeedScaling", "QActual", "QDActual",
             "IActual", "ToolVectorActual", "MessageSize"]
    MAGIC = struct.pack("<Q", dp.TEST_VALUE)

    def __init__(self, host, port, raw_path):
        super().__init__(daemon=True)
        self.sock = socket.create_connection((host, port), timeout=5)
        self.raw = open(raw_path, "wb")
        self.rows = []
        self.resyncs = []          # list of (packet index, skipped bytes)
        self.sizes_seen = {}       # MessageSize values seen
        self.dropped_frames = 0    # frames rejected because the next frame was not aligned
        self.first_packet = None
        self.stop_evt = threading.Event()
        self.last_mode = None
        self.last_valid_time = time.time()
        self.error = None

    def run(self):
        buf = bytearray()
        P, M = dp.PACKET_SIZE, self.MAGIC
        try:
            while not self.stop_evt.is_set():
                chunk = self.sock.recv(65536)
                if not chunk:
                    raise ConnectionError("feedback stream closed by the controller")
                t = time.time_ns()
                buf += chunk
                while len(buf) >= P:
                    if buf[48:56] != M:
                        idx = buf.find(M, 49)
                        if idx == -1:          # keep the tail, a magic may straddle chunks
                            skip = len(buf) - 55
                            del buf[:skip]
                            self.resyncs.append((len(self.rows), skip))
                            break
                        skip = idx - 48
                        del buf[:skip]
                        self.resyncs.append((len(self.rows), skip))
                        continue
                    # look-ahead: accept a frame only if the NEXT frame starts exactly 1440 bytes later.
                    # A truncated packet followed by a full one would otherwise be glued into one bogus frame.
                    if len(buf) < P + 56:
                        break
                    if buf[P + 48:P + 56] != M:
                        idx = buf.find(M, 49)
                        skip = (idx - 48) if idx != -1 else len(buf) - 55
                        del buf[:skip]
                        self.resyncs.append((len(self.rows), skip))
                        self.dropped_frames += 1
                        continue
                    pkt = bytes(buf[:P]); del buf[:P]
                    d = dp.unpack(pkt, self.NAMES)
                    self.sizes_seen[d["MessageSize"]] = self.sizes_seen.get(d["MessageSize"], 0) + 1
                    if self.first_packet is None:
                        self.first_packet = pkt
                    self.raw.write(pkt)
                    self.last_mode = d["RobotMode"]
                    self.last_valid_time = time.time()
                    self.rows.append([d["TimeStamp"], t, d["RobotMode"], d["SpeedScaling"],
                                      *d["QActual"], *d["QDActual"], *d["IActual"], *d["ToolVectorActual"]])
        except Exception as e:  # noqa: BLE001
            self.error = e
        finally:
            self.raw.close()
            self.sock.close()
