"""Shared control names with explicit origin; never infer remote hardware or AP state."""
import math


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def vector(value):
    return isinstance(value, list) and len(value) == 3 and all(number(v) for v in value)


def field(value, source, kind, reason=None):
    return {'value': value, 'available': value is not None, 'source': source,
            'kind': kind, 'reason': reason}


def active_components(components):
    return [c for c in (components or []) if isinstance(c, dict)
            and c.get('active_in_hierarchy') is not False and c.get('enabled') is not False]


def physical_value(components, key, valid):
    candidates = [c for c in active_components(components) if valid(c.get(key))]
    grabbed = [c for c in candidates if c.get('grabbed') is True]
    pool = grabbed if grabbed else candidates
    if not pool:
        return None, 'No active local control value supplied'
    values = [c[key] for c in pool]
    if all(value == values[0] for value in values):
        return values[0], None
    return None, 'Multiple local controls disagree; controlling seat is not established'


MODE_NAMES = {'heading': 'headingHold', 'altitude': 'altitudeHold',
              'hover': 'hoverMode', 'navigation': 'navMode', 'speed': 'speedMode'}


def multicrew_throttle(airframe, provenance, network, engines):
    if (airframe != 'EF-24G' or provenance != 'received_multicrew_controls'
            or (number(network) and network != 0)):
        return None
    inputs = [engine['inputThrottle'] for engine in active_components(engines)
              if engine.get('engineEnabled') is True and engine.get('startedUp') is True
              and engine.get('failed') is not True and number(engine.get('inputThrottle'))
              and 0 <= engine['inputThrottle'] <= 1]
    if inputs:
        return field(sum(inputs) / len(inputs), 'systems.engines[].inputThrottle', 'replicated_engine_input')
    return None


def aircraft_throttle(aircraft):
    saved = (aircraft.get('unified_controls') or {}).get('throttle') or {}
    controls = aircraft.get('controls') or {}
    source = (aircraft.get('provenance') or {}).get('controls')
    if saved.get('available'):
        return saved
    if controls:
        # Older recordings retain the local engine controller's input and seat levers.
        return unify_controls(controls, source)['throttle']
    return saved


def unify_controls(controls, provenance=None, *, airframe=None, engines=None):
    c = controls or {}
    descriptor = str(provenance or '')
    remote = descriptor.startswith('received') or descriptor.startswith('remote')
    local = descriptor.startswith('local')
    network_throttle = c.get('network_throttle')
    network_stick = c.get('network_control_pyr')
    if remote or (not local and (number(network_throttle) or vector(network_stick))):
        throttle = multicrew_throttle(airframe, provenance, network_throttle, engines) or field(
            network_throttle if number(network_throttle) else None,
            'controls.network_throttle', 'received_control_command',
            None if number(network_throttle) else 'No remote throttle command supplied')
        stick = field(network_stick if vector(network_stick) else None,
            'controls.network_control_pyr', 'received_control_command',
            None if vector(network_stick) else 'No remote pitch/yaw/roll command supplied')
    else:
        value, reason = physical_value(c.get('throttles'), 'currentThrottle', number)
        throttle = field(value, 'controls.throttles[].currentThrottle', 'local_physical_control', reason)
        if value is None and local:
            command = (c.get('autopilot') or {}).get('throttleInput')
            if number(command) and any(lever.get('currentThrottle') == command
                                       for lever in active_components(c.get('throttles'))):
                throttle = field(command, 'controls.autopilot.throttleInput', 'local_throttle_input')
        if vector(c.get('input_pyr')):
            stick = field(c['input_pyr'], 'controls.input_pyr', 'local_input')
        else:
            value, reason = physical_value(c.get('sticks'), 'CurrentStick', vector)
            stick = field(value, 'controls.sticks[].CurrentStick', 'local_physical_control', reason)
    ap = c.get('autopilot')
    modes = {name: ap.get(raw) if isinstance(ap, dict) and isinstance(ap.get(raw), bool) else None
             for name, raw in MODE_NAMES.items()}
    autopilot = {'available': any(value is not None for value in modes.values()),
        'source': 'controls.autopilot', 'modes': modes,
        'targets': {name: ap.get(raw) if isinstance(ap, dict) else None for name, raw in
            {'heading': 'headingToHold', 'altitude': 'altitudeToHold', 'speed': 'speedToHold'}.items()},
        'outputs': {name: ap.get(raw) if isinstance(ap, dict) else None for name, raw in
            {'stick_pyr': 'stickPYR', 'throttle_input': 'throttleInput', 'ap_throttle': 'apThrottle'}.items()}}
    remote_flaps=remote or (not local and number(c.get('network_flaps')))
    raw_flaps=c.get('network_flaps') if remote_flaps else (c.get('flight_assist') or {}).get('_flaps')
    flaps=field(raw_flaps if number(raw_flaps) else None,
        'controls.network_flaps' if remote_flaps else 'controls.flight_assist._flaps',
        'received_control_command' if remote_flaps else 'local_control_setting',
        None if number(raw_flaps) else 'No flap setting supplied')
    flaps['enabled']=c.get('network_flaps_enabled') if remote_flaps else None
    return {'throttle': throttle, 'stick_pyr': stick, 'autopilot': autopilot,'flaps':flaps}
