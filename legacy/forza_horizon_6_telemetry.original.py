import json
import math
import socket
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


UDP_IP = "127.0.0.1"
UDP_PORT = 5607

WEB_IP = "127.0.0.1"
WEB_PORT = 8080

latest_data = {
    "connected": False,
    "last_packet_time": None,
    "packet_length": 0,
    "telemetry": {},
}

lock = threading.Lock()


FIELDS = [
    ("IsRaceOn", "i", 0),
    ("TimestampMS", "I", 4),

    ("EngineMaxRpm", "f", 8),
    ("EngineIdleRpm", "f", 12),
    ("CurrentEngineRpm", "f", 16),

    ("AccelerationX", "f", 20),
    ("AccelerationY", "f", 24),
    ("AccelerationZ", "f", 28),

    ("VelocityX", "f", 32),
    ("VelocityY", "f", 36),
    ("VelocityZ", "f", 40),

    ("AngularVelocityX", "f", 44),
    ("AngularVelocityY", "f", 48),
    ("AngularVelocityZ", "f", 52),

    ("Yaw", "f", 56),
    ("Pitch", "f", 60),
    ("Roll", "f", 64),

    ("NormalizedSuspensionTravelFrontLeft", "f", 68),
    ("NormalizedSuspensionTravelFrontRight", "f", 72),
    ("NormalizedSuspensionTravelRearLeft", "f", 76),
    ("NormalizedSuspensionTravelRearRight", "f", 80),

    ("TireSlipRatioFrontLeft", "f", 84),
    ("TireSlipRatioFrontRight", "f", 88),
    ("TireSlipRatioRearLeft", "f", 92),
    ("TireSlipRatioRearRight", "f", 96),

    ("WheelRotationSpeedFrontLeft", "f", 100),
    ("WheelRotationSpeedFrontRight", "f", 104),
    ("WheelRotationSpeedRearLeft", "f", 108),
    ("WheelRotationSpeedRearRight", "f", 112),

    ("WheelOnRumbleStripFrontLeft", "i", 116),
    ("WheelOnRumbleStripFrontRight", "i", 120),
    ("WheelOnRumbleStripRearLeft", "i", 124),
    ("WheelOnRumbleStripRearRight", "i", 128),

    ("WheelInPuddleFrontLeft", "i", 132),
    ("WheelInPuddleFrontRight", "i", 136),
    ("WheelInPuddleRearLeft", "i", 140),
    ("WheelInPuddleRearRight", "i", 144),

    ("SurfaceRumbleFrontLeft", "f", 148),
    ("SurfaceRumbleFrontRight", "f", 152),
    ("SurfaceRumbleRearLeft", "f", 156),
    ("SurfaceRumbleRearRight", "f", 160),

    ("TireSlipAngleFrontLeft", "f", 164),
    ("TireSlipAngleFrontRight", "f", 168),
    ("TireSlipAngleRearLeft", "f", 172),
    ("TireSlipAngleRearRight", "f", 176),

    ("TireCombinedSlipFrontLeft", "f", 180),
    ("TireCombinedSlipFrontRight", "f", 184),
    ("TireCombinedSlipRearLeft", "f", 188),
    ("TireCombinedSlipRearRight", "f", 192),

    ("SuspensionTravelMetersFrontLeft", "f", 196),
    ("SuspensionTravelMetersFrontRight", "f", 200),
    ("SuspensionTravelMetersRearLeft", "f", 204),
    ("SuspensionTravelMetersRearRight", "f", 208),

    ("CarOrdinal", "i", 212),
    ("CarClass", "i", 216),
    ("CarPerformanceIndex", "i", 220),
    ("DrivetrainType", "i", 224),
    ("NumCylinders", "i", 228),
    ("CarGroup", "I", 232),

    ("SmashableVelDiff", "f", 236),
    ("SmashableMass", "f", 240),

    ("PositionX", "f", 244),
    ("PositionY", "f", 248),
    ("PositionZ", "f", 252),

    ("Speed", "f", 256),
    ("Power", "f", 260),
    ("Torque", "f", 264),

    ("TireTempFrontLeft", "f", 268),
    ("TireTempFrontRight", "f", 272),
    ("TireTempRearLeft", "f", 276),
    ("TireTempRearRight", "f", 280),

    ("Boost", "f", 284),
    ("Fuel", "f", 288),
    ("DistanceTraveled", "f", 292),

    ("BestLap", "f", 296),
    ("LastLap", "f", 300),
    ("CurrentLap", "f", 304),
    ("CurrentRaceTime", "f", 308),

    ("LapNumber", "H", 312),
    ("RacePosition", "B", 314),

    ("Accel", "B", 315),
    ("Brake", "B", 316),
    ("Clutch", "B", 317),
    ("HandBrake", "B", 318),
    ("Gear", "B", 319),
    ("Steer", "b", 320),
    ("NormalizedDrivingLine", "b", 321),
    ("NormalizedAIBrakeDifference", "b", 322),
]


CAR_CLASSES = {
    0: "D",
    1: "C",
    2: "B",
    3: "A",
    4: "S1",
    5: "S2",
    6: "R",
    7: "X",
}

DRIVETRAIN_TYPES = {
    0: "FWD",
    1: "RWD",
    2: "AWD",
}


def unpack_value(data, fmt, offset):
    size = struct.calcsize("<" + fmt)
    if len(data) < offset + size:
        return None
    return struct.unpack_from("<" + fmt, data, offset)[0]


def safe_round(value, digits=2):
    if value is None:
        return None
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return round(value, digits)
    return value


def parse_packet(data):
    telemetry = {}

    for name, fmt, offset in FIELDS:
        telemetry[name] = unpack_value(data, fmt, offset)

    speed_ms = telemetry.get("Speed") or 0
    power_w = telemetry.get("Power") or 0
    torque_nm = telemetry.get("Torque") or 0
    rpm = telemetry.get("CurrentEngineRpm") or 0
    max_rpm = telemetry.get("EngineMaxRpm") or 1

    telemetry["SpeedKmh"] = speed_ms * 3.6
    telemetry["PowerHp"] = power_w / 745.7
    telemetry["PowerKw"] = power_w / 1000
    telemetry["TorqueNm"] = torque_nm
    telemetry["RpmPercent"] = max(0, min(100, (rpm / max_rpm) * 100 if max_rpm else 0))

    telemetry["AccelPercent"] = ((telemetry.get("Accel") or 0) / 255) * 100
    telemetry["BrakePercent"] = ((telemetry.get("Brake") or 0) / 255) * 100
    telemetry["ClutchPercent"] = ((telemetry.get("Clutch") or 0) / 255) * 100
    telemetry["HandBrakePercent"] = ((telemetry.get("HandBrake") or 0) / 255) * 100
    telemetry["SteerPercent"] = ((telemetry.get("Steer") or 0) / 127) * 100

    telemetry["FuelPercent"] = (telemetry.get("Fuel") or 0) * 100
    telemetry["DistanceKm"] = (telemetry.get("DistanceTraveled") or 0) / 1000

    telemetry["CarClassLabel"] = CAR_CLASSES.get(telemetry.get("CarClass"), "Unknown")
    telemetry["DrivetrainLabel"] = DRIVETRAIN_TYPES.get(telemetry.get("DrivetrainType"), "Unknown")

    cleaned = {}
    for key, value in telemetry.items():
        cleaned[key] = safe_round(value, 3)

    return cleaned


def udp_listener():
    global latest_data

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((UDP_IP, UDP_PORT))

    print(f"[UDP] Listening on {UDP_IP}:{UDP_PORT}")
    print("[FORZA] Set Data Out IP Address to 127.0.0.1")
    print(f"[FORZA] Set Data Out IP Port to {UDP_PORT}")

    while True:
        data, addr = sock.recvfrom(2048)

        try:
            telemetry = parse_packet(data)
            now = time.time()

            with lock:
                latest_data = {
                    "connected": True,
                    "last_packet_time": now,
                    "packet_length": len(data),
                    "source": f"{addr[0]}:{addr[1]}",
                    "telemetry": telemetry,
                }

        except Exception as e:
            print(f"[ERROR] Failed to parse packet: {e}")


HTML = r"""
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <title>Forza Horizon 6 Telemetry Dashboard</title>
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />

  <style>
    :root {
      --bg: #080b10;
      --panel: #111827;
      --panel2: #0f172a;
      --text: #f8fafc;
      --muted: #94a3b8;
      --good: #22c55e;
      --warn: #f59e0b;
      --bad: #ef4444;
      --accent: #38bdf8;
      --line: rgba(255,255,255,0.10);
      --card-radius: 22px;
    }

    * {
      box-sizing: border-box;
    }

    body {
      margin: 0;
      background:
        radial-gradient(circle at top left, rgba(56,189,248,0.18), transparent 35%),
        radial-gradient(circle at top right, rgba(245,158,11,0.10), transparent 28%),
        var(--bg);
      color: var(--text);
      font-family: Inter, Segoe UI, Arial, sans-serif;
    }

    header {
      padding: 26px 34px 12px 34px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 18px;
      flex-wrap: wrap;
    }

    h1 {
      margin: 0;
      font-size: 28px;
      letter-spacing: -0.04em;
    }

    .sub {
      color: var(--muted);
      margin-top: 6px;
      font-size: 14px;
    }

    .status {
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 10px 14px;
      border: 1px solid var(--line);
      border-radius: 999px;
      background: rgba(15,23,42,0.75);
      backdrop-filter: blur(10px);
      font-size: 14px;
      color: var(--muted);
    }

    .dot {
      width: 10px;
      height: 10px;
      border-radius: 999px;
      background: var(--bad);
      box-shadow: 0 0 16px var(--bad);
    }

    .dot.on {
      background: var(--good);
      box-shadow: 0 0 16px var(--good);
    }

    .layout {
      padding: 18px 34px 34px 34px;
      display: grid;
      grid-template-columns: 1.2fr 0.8fr;
      gap: 18px;
    }

    .grid {
      display: grid;
      gap: 18px;
    }

    .grid-3 {
      grid-template-columns: repeat(3, minmax(0, 1fr));
    }

    .grid-2 {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }

    .card {
      background: linear-gradient(180deg, rgba(17,24,39,0.92), rgba(15,23,42,0.92));
      border: 1px solid var(--line);
      border-radius: var(--card-radius);
      padding: 20px;
      box-shadow: 0 18px 40px rgba(0,0,0,0.25);
      overflow: hidden;
    }

    .card-title {
      font-size: 13px;
      text-transform: uppercase;
      letter-spacing: 0.12em;
      color: var(--muted);
      margin-bottom: 12px;
    }

    .big-number {
      font-size: 64px;
      line-height: 0.95;
      font-weight: 800;
      letter-spacing: -0.07em;
    }

    .unit {
      font-size: 18px;
      color: var(--muted);
      margin-left: 4px;
      letter-spacing: 0;
    }

    .medium-number {
      font-size: 36px;
      font-weight: 750;
      letter-spacing: -0.04em;
    }

    .metric-row {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      margin-top: 9px;
      color: var(--muted);
      font-size: 14px;
    }

    .bar-wrap {
      height: 12px;
      border-radius: 999px;
      background: rgba(255,255,255,0.09);
      overflow: hidden;
      margin-top: 10px;
    }

    .bar {
      height: 100%;
      width: 0%;
      background: linear-gradient(90deg, var(--accent), #a78bfa);
      border-radius: 999px;
      transition: width 0.08s linear;
    }

    .bar.warn {
      background: linear-gradient(90deg, var(--warn), #ef4444);
    }

    .bar.good {
      background: linear-gradient(90deg, var(--good), #38bdf8);
    }

    .gauge {
      width: 210px;
      height: 210px;
      border-radius: 50%;
      background:
        conic-gradient(var(--accent) 0deg, var(--accent) 0deg, rgba(255,255,255,0.08) 0deg 360deg);
      display: flex;
      align-items: center;
      justify-content: center;
      margin: 8px auto;
      position: relative;
    }

    .gauge::after {
      content: "";
      position: absolute;
      width: 154px;
      height: 154px;
      background: var(--panel2);
      border-radius: 50%;
      border: 1px solid var(--line);
    }

    .gauge-content {
      z-index: 1;
      text-align: center;
    }

    .pill-row {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-top: 12px;
    }

    .pill {
      padding: 10px 12px;
      background: rgba(255,255,255,0.06);
      border: 1px solid var(--line);
      border-radius: 999px;
      color: var(--muted);
      font-size: 13px;
    }

    .pill strong {
      color: var(--text);
    }

    .input-grid {
      display: grid;
      grid-template-columns: 1fr;
      gap: 12px;
    }

    .input-label {
      display: flex;
      justify-content: space-between;
      color: var(--muted);
      font-size: 13px;
      margin-bottom: 6px;
    }

    .tire-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 12px;
    }

    .tire {
      background: rgba(255,255,255,0.05);
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 14px;
    }

    .tire-name {
      font-size: 12px;
      color: var(--muted);
      margin-bottom: 8px;
      text-transform: uppercase;
      letter-spacing: 0.1em;
    }

    .tire-value {
      font-size: 24px;
      font-weight: 750;
    }

    .raw {
      grid-column: 1 / -1;
    }

    .raw-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      margin-bottom: 12px;
      flex-wrap: wrap;
    }

    input {
      background: rgba(255,255,255,0.06);
      color: var(--text);
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 10px 12px;
      outline: none;
      min-width: 260px;
    }

    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
    }

    th, td {
      border-bottom: 1px solid var(--line);
      padding: 10px 8px;
      text-align: left;
    }

    th {
      color: var(--muted);
      font-weight: 600;
      position: sticky;
      top: 0;
      background: var(--panel);
    }

    .table-wrap {
      max-height: 520px;
      overflow: auto;
      border: 1px solid var(--line);
      border-radius: 16px;
    }

    .muted {
      color: var(--muted);
    }

    @media (max-width: 1100px) {
      .layout {
        grid-template-columns: 1fr;
      }

      .grid-3 {
        grid-template-columns: 1fr;
      }

      .grid-2 {
        grid-template-columns: 1fr;
      }
    }
  </style>
</head>

<body>
  <header>
    <div>
      <h1>Forza Horizon 6 Telemetry Dashboard</h1>
      <div class="sub">Live UDP Data Out Monitor · Speed · RPM · Inputs · Tires · Suspension · Raw Telemetry</div>
    </div>

    <div class="status">
      <div id="statusDot" class="dot"></div>
      <span id="statusText">Waiting for telemetry...</span>
    </div>
  </header>

  <main class="layout">
    <section class="grid">
      <div class="grid grid-3">
        <div class="card">
          <div class="card-title">Speed</div>
          <div class="big-number"><span id="speed">0</span><span class="unit">km/h</span></div>
          <div class="metric-row">
            <span>Raw m/s</span>
            <strong id="speedMs">0</strong>
          </div>
        </div>

        <div class="card">
          <div class="card-title">Gear</div>
          <div class="big-number"><span id="gear">N</span></div>
          <div class="metric-row">
            <span>Race Position</span>
            <strong id="racePosition">-</strong>
          </div>
        </div>

        <div class="card">
          <div class="card-title">RPM</div>
          <div class="medium-number"><span id="rpm">0</span> <span class="unit">RPM</span></div>
          <div class="bar-wrap">
            <div id="rpmBar" class="bar warn"></div>
          </div>
          <div class="metric-row">
            <span>Max RPM</span>
            <strong id="maxRpm">0</strong>
          </div>
        </div>
      </div>

      <div class="grid grid-2">
        <div class="card">
          <div class="card-title">Power / Torque</div>
          <div class="grid grid-2">
            <div>
              <div class="medium-number"><span id="powerHp">0</span><span class="unit">hp</span></div>
              <div class="muted">Power</div>
            </div>
            <div>
              <div class="medium-number"><span id="torque">0</span><span class="unit">Nm</span></div>
              <div class="muted">Torque</div>
            </div>
          </div>
          <div class="pill-row">
            <div class="pill">Boost: <strong id="boost">0</strong> PSI</div>
            <div class="pill">Fuel: <strong id="fuel">0</strong>%</div>
            <div class="pill">Distance: <strong id="distance">0</strong> km</div>
          </div>
        </div>

        <div class="card">
          <div class="card-title">Car Info</div>
          <div class="pill-row">
            <div class="pill">Class: <strong id="carClass">-</strong></div>
            <div class="pill">PI: <strong id="carPi">-</strong></div>
            <div class="pill">Drivetrain: <strong id="drivetrain">-</strong></div>
            <div class="pill">Cylinders: <strong id="cylinders">-</strong></div>
            <div class="pill">Car ID: <strong id="carOrdinal">-</strong></div>
          </div>
        </div>
      </div>

      <div class="grid grid-2">
        <div class="card">
          <div class="card-title">Player Inputs</div>

          <div class="input-grid">
            <div>
              <div class="input-label"><span>Throttle</span><strong id="accelText">0%</strong></div>
              <div class="bar-wrap"><div id="accelBar" class="bar good"></div></div>
            </div>

            <div>
              <div class="input-label"><span>Brake</span><strong id="brakeText">0%</strong></div>
              <div class="bar-wrap"><div id="brakeBar" class="bar warn"></div></div>
            </div>

            <div>
              <div class="input-label"><span>Clutch</span><strong id="clutchText">0%</strong></div>
              <div class="bar-wrap"><div id="clutchBar" class="bar"></div></div>
            </div>

            <div>
              <div class="input-label"><span>Handbrake</span><strong id="handbrakeText">0%</strong></div>
              <div class="bar-wrap"><div id="handbrakeBar" class="bar warn"></div></div>
            </div>

            <div>
              <div class="input-label"><span>Steering</span><strong id="steerText">0%</strong></div>
              <div class="bar-wrap"><div id="steerBar" class="bar"></div></div>
            </div>
          </div>
        </div>

        <div class="card">
          <div class="card-title">Lap / Race</div>
          <div class="pill-row">
            <div class="pill">Lap: <strong id="lapNumber">0</strong></div>
            <div class="pill">Best Lap: <strong id="bestLap">0</strong>s</div>
            <div class="pill">Last Lap: <strong id="lastLap">0</strong>s</div>
            <div class="pill">Current Lap: <strong id="currentLap">0</strong>s</div>
            <div class="pill">Race Time: <strong id="raceTime">0</strong>s</div>
            <div class="pill">Race On: <strong id="raceOn">0</strong></div>
          </div>
        </div>
      </div>
    </section>

    <aside class="grid">
      <div class="card">
        <div class="card-title">Speed Gauge</div>
        <div id="speedGauge" class="gauge">
          <div class="gauge-content">
            <div class="medium-number"><span id="speedGaugeText">0</span></div>
            <div class="muted">km/h</div>
          </div>
        </div>
      </div>

      <div class="card">
        <div class="card-title">Tire Temperatures</div>
        <div class="tire-grid">
          <div class="tire">
            <div class="tire-name">Front Left</div>
            <div class="tire-value"><span id="tempFL">0</span>°</div>
          </div>
          <div class="tire">
            <div class="tire-name">Front Right</div>
            <div class="tire-value"><span id="tempFR">0</span>°</div>
          </div>
          <div class="tire">
            <div class="tire-name">Rear Left</div>
            <div class="tire-value"><span id="tempRL">0</span>°</div>
          </div>
          <div class="tire">
            <div class="tire-name">Rear Right</div>
            <div class="tire-value"><span id="tempRR">0</span>°</div>
          </div>
        </div>
      </div>

      <div class="card">
        <div class="card-title">Grip / Slip</div>
        <div class="pill-row">
          <div class="pill">Slip FL: <strong id="slipFL">0</strong></div>
          <div class="pill">Slip FR: <strong id="slipFR">0</strong></div>
          <div class="pill">Slip RL: <strong id="slipRL">0</strong></div>
          <div class="pill">Slip RR: <strong id="slipRR">0</strong></div>
        </div>
      </div>
    </aside>

    <section class="card raw">
      <div class="raw-header">
        <div>
          <div class="card-title">Raw Telemetry</div>
          <div class="muted">Forza’dan parse edilen tüm alanlar burada listelenir.</div>
        </div>
        <input id="filterInput" placeholder="Filter telemetry field..." />
      </div>

      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Field</th>
              <th>Value</th>
            </tr>
          </thead>
          <tbody id="rawTable"></tbody>
        </table>
      </div>
    </section>
  </main>

  <script>
    let lastTelemetry = {};
    const filterInput = document.getElementById("filterInput");

    function v(obj, key, fallback = 0) {
      const val = obj[key];
      if (val === null || val === undefined || Number.isNaN(val)) return fallback;
      return val;
    }

    function fmt(num, digits = 0) {
      if (num === null || num === undefined || Number.isNaN(num)) return "-";
      return Number(num).toFixed(digits);
    }

    function pct(num) {
      return Math.max(0, Math.min(100, Number(num) || 0));
    }

    function setText(id, value) {
      document.getElementById(id).textContent = value;
    }

    function setBar(id, value) {
      document.getElementById(id).style.width = pct(value) + "%";
    }

    function gearLabel(g) {
      if (g === 0) return "R/N";
      return g;
    }

    function updateDashboard(payload) {
      const t = payload.telemetry || {};
      lastTelemetry = t;

      const now = Date.now() / 1000;
      const last = payload.last_packet_time || 0;
      const isLive = payload.connected && (now - last < 2);

      document.getElementById("statusDot").classList.toggle("on", isLive);
      setText(
        "statusText",
        isLive
          ? `Live · packet ${payload.packet_length} bytes · ${payload.source || ""}`
          : "Waiting for telemetry..."
      );

      const speedKmh = v(t, "SpeedKmh");
      const speedMs = v(t, "Speed");

      setText("speed", fmt(speedKmh, 0));
      setText("speedMs", fmt(speedMs, 2));
      setText("speedGaugeText", fmt(speedKmh, 0));

      const speedDeg = Math.max(0, Math.min(360, (speedKmh / 420) * 360));
      document.getElementById("speedGauge").style.background =
        `conic-gradient(var(--accent) 0deg, var(--accent) ${speedDeg}deg, rgba(255,255,255,0.08) ${speedDeg}deg 360deg)`;

      setText("gear", gearLabel(v(t, "Gear", "-")));
      setText("racePosition", v(t, "RacePosition", "-"));

      setText("rpm", fmt(v(t, "CurrentEngineRpm"), 0));
      setText("maxRpm", fmt(v(t, "EngineMaxRpm"), 0));
      setBar("rpmBar", v(t, "RpmPercent"));

      setText("powerHp", fmt(v(t, "PowerHp"), 0));
      setText("torque", fmt(v(t, "TorqueNm"), 0));
      setText("boost", fmt(v(t, "Boost"), 2));
      setText("fuel", fmt(v(t, "FuelPercent"), 1));
      setText("distance", fmt(v(t, "DistanceKm"), 2));

      setText("carClass", `${v(t, "CarClassLabel", "-")}`);
      setText("carPi", v(t, "CarPerformanceIndex", "-"));
      setText("drivetrain", v(t, "DrivetrainLabel", "-"));
      setText("cylinders", v(t, "NumCylinders", "-"));
      setText("carOrdinal", v(t, "CarOrdinal", "-"));

      setText("accelText", fmt(v(t, "AccelPercent"), 0) + "%");
      setText("brakeText", fmt(v(t, "BrakePercent"), 0) + "%");
      setText("clutchText", fmt(v(t, "ClutchPercent"), 0) + "%");
      setText("handbrakeText", fmt(v(t, "HandBrakePercent"), 0) + "%");
      setText("steerText", fmt(v(t, "SteerPercent"), 0) + "%");

      setBar("accelBar", v(t, "AccelPercent"));
      setBar("brakeBar", v(t, "BrakePercent"));
      setBar("clutchBar", v(t, "ClutchPercent"));
      setBar("handbrakeBar", v(t, "HandBrakePercent"));
      setBar("steerBar", Math.abs(v(t, "SteerPercent")));

      setText("lapNumber", v(t, "LapNumber", 0));
      setText("bestLap", fmt(v(t, "BestLap"), 2));
      setText("lastLap", fmt(v(t, "LastLap"), 2));
      setText("currentLap", fmt(v(t, "CurrentLap"), 2));
      setText("raceTime", fmt(v(t, "CurrentRaceTime"), 2));
      setText("raceOn", v(t, "IsRaceOn", 0));

      setText("tempFL", fmt(v(t, "TireTempFrontLeft"), 1));
      setText("tempFR", fmt(v(t, "TireTempFrontRight"), 1));
      setText("tempRL", fmt(v(t, "TireTempRearLeft"), 1));
      setText("tempRR", fmt(v(t, "TireTempRearRight"), 1));

      setText("slipFL", fmt(v(t, "TireCombinedSlipFrontLeft"), 3));
      setText("slipFR", fmt(v(t, "TireCombinedSlipFrontRight"), 3));
      setText("slipRL", fmt(v(t, "TireCombinedSlipRearLeft"), 3));
      setText("slipRR", fmt(v(t, "TireCombinedSlipRearRight"), 3));

      renderRawTable();
    }

    function renderRawTable() {
      const tbody = document.getElementById("rawTable");
      const filter = filterInput.value.toLowerCase().trim();

      const rows = Object.entries(lastTelemetry)
        .filter(([key]) => key.toLowerCase().includes(filter))
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([key, value]) => {
          return `<tr><td>${key}</td><td>${value}</td></tr>`;
        })
        .join("");

      tbody.innerHTML = rows || `<tr><td colspan="2" class="muted">No telemetry yet</td></tr>`;
    }

    async function poll() {
      try {
        const res = await fetch("/api/telemetry", { cache: "no-store" });
        const data = await res.json();
        updateDashboard(data);
      } catch (e) {
        document.getElementById("statusDot").classList.remove("on");
        setText("statusText", "Dashboard API not reachable");
      }
    }

    filterInput.addEventListener("input", renderRawTable);

    setInterval(poll, 75);
    poll();
  </script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/" or self.path.startswith("/index"):
            body = HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path.startswith("/api/telemetry"):
            with lock:
                payload = latest_data.copy()

            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        self.send_response(404)
        self.end_headers()

    def log_message(self, format, *args):
        return


def start_web_server():
    server = ThreadingHTTPServer((WEB_IP, WEB_PORT), Handler)
    print(f"[WEB] Dashboard running at http://{WEB_IP}:{WEB_PORT}")
    server.serve_forever()


if __name__ == "__main__":
    udp_thread = threading.Thread(target=udp_listener, daemon=True)
    udp_thread.start()

    start_web_server()