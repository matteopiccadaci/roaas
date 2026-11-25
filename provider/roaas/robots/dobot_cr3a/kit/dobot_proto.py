"""Layout of the real-time feedback packet (port 30004) of Dobot V4 controllers.

Source: "DOBOT TCP/IP Remote Control Interface Guide" V4.6.6, section 3.
One 1440-byte little-endian packet every 8 ms (125 Hz).
"""
import struct

PACKET_SIZE = 1440
TEST_VALUE = 0x0123456789ABCDEF  # fixed value in the TestValue field, used to check alignment

# name: (byte offset, struct format, count)
FIELDS = {
    "MessageSize":       (0,    "H", 1),
    "DigitalInputs":     (8,    "Q", 1),
    "DigitalOutputs":    (16,   "Q", 1),
    "RobotMode":         (24,   "Q", 1),
    "TimeStamp":         (32,   "Q", 1),   # Unix ms, controller clock
    "RunTime":           (40,   "Q", 1),
    "TestValue":         (48,   "Q", 1),
    "SpeedScaling":      (64,   "d", 1),
    "VRobot":            (88,   "d", 1),
    "IRobot":            (96,   "d", 1),
    "ProgramState":      (104,  "d", 1),
    "QTarget":           (192,  "d", 6),
    "QDTarget":          (240,  "d", 6),
    "QDDTarget":         (288,  "d", 6),
    "ITarget":           (336,  "d", 6),
    "MTarget":           (384,  "d", 6),
    "QActual":           (432,  "d", 6),   # joint positions
    "QDActual":          (480,  "d", 6),   # joint speeds
    "IActual":           (528,  "d", 6),   # joint currents
    "ActualTCPForce":    (576,  "d", 6),
    "ToolVectorActual":  (624,  "d", 6),   # tool pose x, y, z, rx, ry, rz
    "TCPSpeedActual":    (672,  "d", 6),
    "TCPForce":          (720,  "d", 6),
    "ToolVectorTarget":  (768,  "d", 6),
    "TCPSpeedTarget":    (816,  "d", 6),
    "MotorTemperatures": (864,  "d", 6),
    "JointModes":        (912,  "d", 6),
    "VActual":           (960,  "d", 6),
    "CRRobotType":       (1031, "B", 1),
    "SixForceOnline":    (1037, "B", 1),
    "CollisionState":    (1038, "B", 1),
    "CurrentCommandId":  (1112, "Q", 1),
    "MActual":           (1120, "d", 6),
    "Load":              (1168, "d", 1),   # payload setting, kg
    "CenterX":           (1176, "d", 1),   # payload offset, mm
    "CenterY":           (1184, "d", 1),
    "CenterZ":           (1192, "d", 1),
    "UserFrame":         (1200, "d", 6),   # active user frame   (UNVERIFIED split of bytes 1200-1295)
    "ToolFrame":         (1248, "d", 6),   # active tool frame   (UNVERIFIED split of bytes 1200-1295)
    "SafetyState":       (1420, "B", 1),   # bit meaning not yet verified on hardware
}

# RobotMode codes. 4 (disabled) and 5/7 confirmed on a CR3A with controller 4.5.1.1.
ROBOT_MODE = {4: "disabled", 5: "enabled_idle", 7: "running", 9: "error", 10: "paused"}
MODE_DISABLED, MODE_IDLE, MODE_RUNNING, MODE_ERROR = 4, 5, 7, 9

# CR3A datasheet limits: joint range and max joint speed.
CR3A_SPEC = {
    "joint_range_deg": [[-360, 360], [-360, 360], [-155, 155], [-360, 360], [-360, 360], [-360, 360]],
    "joint_speed_max_deg_s": [180, 180, 223, 223, 223, 223],
}


def unpack(buf, names=None):
    if len(buf) != PACKET_SIZE:
        raise ValueError(f"packet is {len(buf)} bytes, expected {PACKET_SIZE}")
    out = {}
    for name in (names or FIELDS):
        off, fmt, n = FIELDS[name]
        vals = struct.unpack_from("<" + fmt * n, buf, off)
        out[name] = vals[0] if n == 1 else list(vals)
    return out


def pack(values):
    buf = bytearray(PACKET_SIZE)
    values = dict(values)
    values["MessageSize"] = PACKET_SIZE
    values["TestValue"] = TEST_VALUE
    for name, v in values.items():
        off, fmt, n = FIELDS[name]
        vals = [v] if n == 1 else list(v)
        struct.pack_into("<" + fmt * n, buf, off, *vals)
    return bytes(buf)
