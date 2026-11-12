"""Incremental VTS2 transport decoding and direct-game field extraction."""
import json
import math
import struct
import zlib
from wirejudge.telemetry.control_fields import unify_controls

MAX_FRAME = 16 * 1024 * 1024
MAX_JSON = 32 * 1024 * 1024


class ProtocolError(ValueError):
    pass


class Decoder:
    """TCP recv boundaries have no relationship to VTS2 message boundaries."""
    def __init__(self):
        self.buffer = bytearray()
        self.discarded = 0

    def feed(self, chunk):
        self.buffer.extend(chunk)
        while len(self.buffer) >= 8:
            if self.buffer[:4] != b'VTS2':
                index = self.buffer.find(b'VTS2', 1)
                count = index if index >= 0 else len(self.buffer) - 3
                self.discarded += count
                del self.buffer[:count]
                if self.discarded > MAX_FRAME:
                    raise ProtocolError('No VTS2 framing found within 16 MiB.')
                continue
            length = struct.unpack_from('<I', self.buffer, 4)[0]
            if not 0 < length <= MAX_FRAME:
                raise ProtocolError('Invalid VTS2 compressed frame length.')
            # Observed VTS2 layout: magic, gzip length, gzip payload, opaque 4-byte trailer.
            # Do not guess the trailer's meaning. gzip integrity is validated by zlib.
            size = 12 + length
            if len(self.buffer) < size:
                return
            compressed = bytes(self.buffer[8:8 + length])
            trailer = bytes(self.buffer[8 + length:size])
            del self.buffer[:size]
            try:
                dec = zlib.decompressobj(31)
                raw = dec.decompress(compressed, MAX_JSON + 1)
                if len(raw) > MAX_JSON or not dec.eof or dec.unused_data:
                    raise ProtocolError('Oversized or incomplete gzip telemetry payload.')
                source = json.loads(raw)
            except (ValueError, zlib.error, UnicodeError) as exc:
                raise ProtocolError(f'Cannot decode telemetry: {exc}') from exc
            if not isinstance(source, dict) or not isinstance(source.get('aircraft'), list):
                raise ProtocolError('Telemetry snapshot must contain an aircraft array.')
            yield source, trailer.hex()

    def finish(self):
        if self.buffer:
            raise ProtocolError(f'Connection ended with {len(self.buffer)} incomplete frame bytes.')


def pick(source, names):
    if not isinstance(source, dict):
        return None
    return {key: source[key] for key in names if key in source}


def items(source, names):
    if not isinstance(source, list):
        return None
    return [pick(x, names) for x in source if isinstance(x, dict)]


def heading_from_quaternion(q):
    """App-derived Unity +Z forward heading about +Y; degrees clockwise from +Z."""
    if not isinstance(q, (tuple, list)) or len(q) != 4:
        return None
    if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in q):
        return None
    norm = sum(v * v for v in q)
    if norm < 1e-12:
        return None
    x, y, z, w = q
    return math.degrees(math.atan2(2 * (x * z + w * y) / norm,
                                  1 - 2 * (x * x + y * y) / norm)) % 360


IDENTITY = ('actor_name', 'designation', 'team', 'actor_id', 'is_human',
            'control_owner_id', 'network_entity_id', 'network_owner_id',
            'seat_occupants', 'player_vehicle', 'sync_component', 'sync_initialized')
FLIGHT = ('pitch', 'roll', 'heading', 'airspeed', 'indicatedAirspeed', 'playerGs',
          'radarAltitude', 'sweptRadarAltitude', 'altitudeASL', 'aoa', 'relativeWind',
          'wind', 'windSpeed', 'verticalSpeed', 'isLanded', 'mach', 'machNumber')
KINEMATICS = ('position_world_m', 'rotation_xyzw', 'velocity_world_mps',
              'angular_velocity_world_radps', 'sea_level_world_y_m')
CONTROLS = ('input_pyr', 'assisted_pyr', 'network_control_pyr', 'network_throttle',
            'network_brakes', 'network_flaps', 'network_flaps_enabled', 'autopilot')
GEAR = ('state', 'targetState', 'normTime', 'remoteOnly', 'component_id', 'component_name')
CABLE = ('instance_id', 'name', 'position_world_m', 'rotation_xyzw',
         'endpoint1_world_m', 'endpoint2_world_m', 'broken', 'hook_component_id')
RUNWAY = ('instance_id', 'position_world_m', 'rotation_xyzw', 'ils_glide_angle_game_value',
          'ols_position_world_m', 'ols_rotation_xyzw', 'ols_scale', 'ols_waving_off')


def normalize(source):
    """Keep native values, names, nulls and seat-specific controls. No invented defaults."""
    result = {'source': pick(source, ('protocol', 'game_version', 'mission_time_s',
              'real_time_s', 'utc', 'seq', 'process_id', 'epoch', 'flight_ready',
              'time_scale', 'unity_frame', 'physics_step_s', 'exporter_version')),
              'aircraft': [], 'carriers': []}
    for a in source.get('aircraft', []):
        if not isinstance(a, dict):
            continue
        k, c, s = a.get('kinematics') or {}, a.get('controls') or {}, a.get('systems') or {}
        identity = pick(a.get('identity'), IDENTITY) or {}
        kin = pick(k, KINEMATICS) or {}
        kin['flight_info'] = pick(k.get('flight_info'), FLIGHT)
        controls = pick(c, CONTROLS) or {}
        controls['flight_assist']=pick(c.get('flight_assist'),('_flaps',))
        controls['sticks'] = items(c.get('sticks'), ('CurrentStick', 'grabbed', 'controlMode',
                  'component_id', 'component_name', 'active_in_hierarchy', 'enabled'))
        controls['throttles'] = items(c.get('throttles'), ('currentThrottle', 'grabbed',
                  'component_id', 'component_name', 'active_in_hierarchy', 'enabled'))
        aircraft = {'instance_id': a.get('instance_id'), 'identity': identity,
                    'kinematics': kin, 'controls': controls,
                    'systems': {'gear': items(s.get('gear'), GEAR),
                        'engines': items(s.get('engines'), ('inputThrottle', 'engineEnabled', 'startedUp',
                                                          'failed', 'active_in_hierarchy', 'enabled')),
                        'airbrakes':items(s.get('airbrakes'),('brake','component_id','active_in_hierarchy','enabled')),
                        'wheels':pick(s.get('wheels'),('brake','brakeL','brakeR')),
                        'fuel_tanks':items(s.get('fuel_tanks'),('fuel','maxFuel','component_id','active_in_hierarchy','enabled')),
                        'battery': pick(s.get('battery'), ('currentCharge', 'maxCharge', 'connected', 'energized')),
                        'tailhook': pick(s.get('tailhook'), ('isDeployed', 'isRemote')),
                        'hook_point_world_m': s.get('hook_point_world_m'),
                        'wheel_suspensions': items(s.get('wheel_suspensions'),
                            ('component_id', 'isTouching', 'point', 'normal','brakePedal','active_in_hierarchy','enabled'))},
                    'landing': pick(a.get('landing'), ('current_cable_id',)),
                    'provenance': {'kinematics': k.get('source'), 'controls': c.get('source'),
                                   'systems': s.get('source')}}
        aircraft['unified_controls'] = unify_controls(controls, c.get('source'),
            airframe=identity.get('player_vehicle'), engines=aircraft['systems']['engines'])
        result['aircraft'].append(aircraft)
    for c in source.get('carriers') or []:
        if not isinstance(c, dict):
            continue
        carrier = pick(c, ('instance_id', 'airport_name', 'actor_name', 'position_world_m',
                          'rotation_xyzw', 'velocity_world_mps', 'angular_velocity_world_radps'))
        carrier['cables'] = items(c.get('cables'), CABLE)
        carrier['runways'] = items(c.get('runways'), RUNWAY)
        carrier['derived'] = {'heading_deg': heading_from_quaternion(c.get('rotation_xyzw'))}
        result['carriers'].append(carrier)
    return result
