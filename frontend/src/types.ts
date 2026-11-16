// Mirrors the backend frame contract (backend/telemetry/hub.py, docs/ARCHITECTURE.md).
// Every numeric value may be null: null means missing/invalid, never "zero".

export type N = number | null;
export type ConnectionState = "starting" | "waiting" | "receiving" | "stale" | "error";
export type Mode = "live" | "demo";

export interface Status {
  connection_state: ConnectionState;
  packet_age_ms: N;
  traffic_warning: "invalid_packets" | "foreign_source" | null;
  last_rejected_reason: string | null;
  observed_hz: N;
  mode: Mode;
  receiver_state: "starting" | "listening" | "error" | "stopped";
  receiver_error: string | null;
  receiver_address: string | null;
  session_id: string | null;
  source: string | null;
  sequence: number;
}

export interface Wheel {
  suspension_norm: N;
  suspension_m: N;
  slip_ratio: N;
  slip_angle: N;
  combined_slip: N;
  rotation_rad_s: N;
  on_rumble_strip: boolean | null;
  in_puddle: boolean | null;
  surface_rumble: N;
  tire_temp: N;
}

export type WheelKey = "fl" | "fr" | "rl" | "rr";
type XYZ = { x: N; y: N; z: N };

export interface Normalized {
  race_on: boolean | null;
  timestamp_ms: number;
  speed_mps: N;
  rpm: N;
  rpm_max: N;
  rpm_idle: N;
  gear: { raw: N; label: string | null; verified: boolean };
  inputs: { throttle: N; brake: N; clutch: N; handbrake: N; steer: N };
  power_w: N;
  torque_nm: N;
  boost_psi: N;
  fuel_fraction: N;
  distance_m: N;
  car: {
    ordinal: N;
    class_code: N;
    class_label: string | null;
    pi: N;
    drivetrain_code: N;
    drivetrain_label: string | null;
    cylinders: N;
    group: N;
  };
  wheels: Record<WheelKey, Wheel>;
  motion: {
    accel_local_mps2: XYZ;
    velocity_local_mps: XYZ;
    angular_velocity_rad_s: XYZ;
    orientation_rad: { yaw: N; pitch: N; roll: N };
    position_world_m: XYZ;
  };
  race: { lap_number: N; position: N; best_lap_s: N; last_lap_s: N; current_lap_s: N; race_time_s: N };
  smashable: { vel_diff_mps: N; mass_kg: N };
  driving_line: N;
  ai_brake_difference: N;
}

export interface Derived {
  speed_kmh: N;
  speed_mph: N;
  power_kw: N;
  power_hp: N;
  rpm_fraction: N;
  g_lateral: N;
  g_longitudinal: N;
  g_vertical: N;
}

export interface FieldQuality {
  status: "missing" | "non_finite" | "out_of_range" | "not_applicable";
  detail?: string;
}

export interface Frame {
  schema_version: number;
  api_version: number;
  session_id: string;
  sequence: number;
  game_timestamp_ms: number;
  received_at: number;
  mode: Mode;
  source: string;
  raw?: Record<string, number | null>;
  normalized: Normalized;
  derived: Derived;
  quality: {
    fields: Record<string, FieldQuality>;
    invalid_field_count: number;
    flags: string[];
    tail_hex: string;
    packet_length: number;
  };
}

export interface Hello {
  type: "hello";
  api_version: number;
  schema_version: number;
  app_version: string;
  ui_rate_hz: number;
}

export interface FieldSpec {
  name: string;
  type: string;
  offset: number;
  unit: string | null;
  doc: string;
  range: [number, number] | null;
}

export interface Capabilities {
  app_version: string;
  api_version: number;
  schema: { id: string; version: number; documented_packet_length: number; fields: FieldSpec[] };
  features: Record<string, boolean>;
  verification: Record<string, string>;
  demo_car_ordinal: number;
  udp: { host: string; port: number };
  lan: boolean;
}
