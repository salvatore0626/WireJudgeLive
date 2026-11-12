"""Instrument values from native telemetry, with explicitly app-derived alignment."""
import math
from wirejudge.approach.approach_detection import measurement, number
from wirejudge.approach.approach_geometry import case1_volume, point, NM, inside_ils
from wirejudge.map.carrier_wireframe import deck_height_at, deck_triangles_world
from wirejudge.telemetry.telemetry_protocol import heading_from_quaternion
from wirejudge.telemetry.control_fields import aircraft_throttle, unify_controls


def gear_state(gear):
    states=[str(g.get('state') or '').casefold() for g in (gear or [])]
    if not states:return 'unknown'
    if any(s in ('extending','retracting','moving','transitioning') for s in states):return 'moving'
    if all(s=='extended' for s in states):return 'down'
    if all(s=='retracted' for s in states):return 'up'
    if all(s in ('extended','retracted') for s in states):return 'moving'
    return 'unknown'


def fuel_values(tanks):
    active=[t for t in (tanks or []) if isinstance(t,dict) and t.get('active_in_hierarchy') is not False and t.get('enabled') is not False]
    if not active:return None,None,None
    unique={t.get('component_id') if t.get('component_id') else ('row',i):t for i,t in enumerate(active)}
    if any(not number(t.get('fuel')) or not number(t.get('maxFuel')) for t in unique.values()):return None,None,None
    fuel=sum(t['fuel'] for t in unique.values());capacity=sum(t['maxFuel'] for t in unique.values())
    return fuel,capacity,100*fuel/capacity if capacity>0 else None



def low_fuel(percent):
    return number(percent) and percent < 10.



def brake_values(systems):
    def active(items):
        return [c for c in (items or []) if isinstance(c,dict)
                and c.get('active_in_hierarchy') is not False and c.get('enabled') is not False]
    air=[c.get('brake') for c in active(systems.get('airbrakes')) if number(c.get('brake'))]
    wheels=systems.get('wheels') or {}
    wheel=[wheels.get(k) for k in ('brake','brakeL','brakeR') if number(wheels.get(k))]
    if not wheel:
        wheel=[c.get('brakePedal') for c in active(systems.get('wheel_suspensions')) if number(c.get('brakePedal'))]
    percent=lambda values:100*max(0,min(1,max(values))) if values else None
    return percent(air),percent(wheel)


def carrier_wind(sample, carrier):
    """Prefer valid wind from aircraft on this deck, falling back to all aircraft."""
    deck = None
    deck_readings = []
    all_readings = []
    for aircraft in sample.get('aircraft', []):
        if aircraft.get('_radar_stale'):
            continue
        kinematics = aircraft.get('kinematics') or {}
        flight = kinematics.get('flight_info') or {}
        vector = flight.get('wind')
        if not isinstance(vector, (list, tuple)) or len(vector) != 3 or not all(
                isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
                for v in vector):
            continue
        all_readings.append(vector)
        if not carrier:
            continue
        wheels = (aircraft.get('systems') or {}).get('wheel_suspensions') or ()
        contacts = [wheel.get('point') for wheel in wheels if wheel.get('isTouching') is True]
        if not contacts and flight.get('isLanded') is not True:
            continue
        if deck is None:
            heading = (carrier.get('derived') or {}).get('heading_deg')
            if heading is None:
                heading = heading_from_quaternion(carrier.get('rotation_xyzw'))
            deck = deck_triangles_world(carrier, heading)
        positions = [kinematics.get('position_world_m'), *contacts]
        if any(deck_height_at((p[0], p[2], p[1]), deck) is not None
               for p in positions if point(p)):
            deck_readings.append(vector)

    readings = deck_readings or all_readings
    if readings:
        return (sum(v[0] for v in readings) / len(readings), 0.,
                sum(v[2] for v in readings) / len(readings))
    return None


def wind_from(vector):
    if not isinstance(vector,(list,tuple)) or len(vector)!=3 or not all(number(v) for v in vector):return None
    if math.hypot(vector[0],vector[2])<1e-6:return None
    return math.degrees(math.atan2(-vector[0],-vector[2]))%360


def g_extrema(sample,history,aircraft_id):
    """Extrema in the available displayed buffer, excluding future/other sessions."""
    values=[];now=sample['capture']['monotonic_s'];connection=sample['capture']['connection_id']
    for frame in history:
        c=frame['capture']
        if c['connection_id']!=connection or c['monotonic_s']>now:continue
        aircraft=next((a for a in frame.get('aircraft',[]) if a['instance_id']==aircraft_id),None)
        value=(((aircraft or {}).get('kinematics') or {}).get('flight_info') or {}).get('playerGs')
        if number(value):values.append(value)
    aircraft=next((a for a in sample.get('aircraft',[]) if a['instance_id']==aircraft_id),None)
    current=(((aircraft or {}).get('kinematics') or {}).get('flight_info') or {}).get('playerGs')
    if number(current):values.append(current)
    return (min(values),max(values)) if values else (None,None)


def flight_values(sample, aircraft, carrier_id, case, glide, offset, units):
    k=aircraft.get('kinematics') or {}; f=k.get('flight_info') or {}
    systems=aircraft.get('systems') or {}
    valid=lambda key:f.get(key) if number(f.get(key)) else None
    vs=units.altitude(valid('verticalSpeed'))
    throttle=aircraft_throttle(aircraft)
    raw_throttle=throttle.get('value') if throttle.get('available') else None
    stick=(aircraft.get('unified_controls') or {}).get('stick_pyr') or {}
    pyr=stick.get('value') if stick.get('available') else None
    if not isinstance(pyr,(list,tuple)) or len(pyr)!=3 or not all(number(v) for v in pyr):pyr=None
    fuel,capacity,fuel_percent=fuel_values(systems.get('fuel_tanks'))
    battery = systems.get('battery') or {}
    charge, max_charge = battery.get('currentCharge'), battery.get('maxCharge')
    battery_percent = None
    if number(charge) and number(max_charge) and max_charge > 0:
        battery_percent = 100 * max(0., min(1., charge / max_charge))
    airbrake,wheel_brake=brake_values(systems)
    flaps=(aircraft.get('unified_controls') or {}).get('flaps') or {}
    if not flaps:
        flaps=unify_controls(aircraft.get('controls'),(aircraft.get('provenance') or {}).get('controls'))['flaps']
    heading=valid('heading')
    if heading is None:heading=heading_from_quaternion(k.get('rotation_xyzw'))
    ground_speed = track = flight_path_angle = None
    velocity = k.get('velocity_world_mps')
    if isinstance(velocity, (list, tuple)) and len(velocity) == 3 and all(map(number, velocity)):
        east, up, north = velocity
        horizontal = math.hypot(east, north)
        ground_speed = units.speed(horizontal)
        if horizontal >= 1.:
            track = math.degrees(math.atan2(east, north)) % 360
        if math.hypot(horizontal, up) >= 1.:
            flight_path_angle = math.degrees(math.atan2(up, horizontal))
    value={'ias':units.speed(valid('indicatedAirspeed')),'tas':units.speed(valid('airspeed')),
        'ground_speed':ground_speed,'track':track,'flight_path_angle':flight_path_angle,
        'altitude':units.altitude(valid('altitudeASL')),'radar_alt':units.altitude(valid('radarAltitude')),
        'vertical_speed':vs*60 if vs is not None else None,'aoa':valid('aoa'),
        'wind':units.speed(valid('windSpeed')),'throttle':raw_throttle*100 if number(raw_throttle) else None,
        'gs':None,'loc':None,'dme':None,'course':None,'heading':heading,
        'gear':gear_state(systems.get('gear')),'hook':'unknown','inside':False,
        'pitch':valid('pitch'),'roll':valid('roll'),'gs_load':valid('playerGs'),
        'input_pitch':pyr[0] if pyr else None,'input_yaw':pyr[1] if pyr else None,
        'input_roll':pyr[2] if pyr else None,
        'fuel':fuel,'fuel_capacity':capacity,'fuel_percent':fuel_percent,
        'battery_percent': battery_percent,
        'battery_connected': battery.get('connected'),
        'battery_energized': battery.get('energized'),
        'flaps':flaps.get('value') if flaps.get('available') else None,
        'wind_from':wind_from(f.get('wind')),'wind_calm':valid('windSpeed') is not None and abs(valid('windSpeed'))<.05,
        'airbrake':airbrake,'wheel_brake':wheel_brake}
    hook=(systems.get('tailhook') or {}).get('isDeployed')
    if isinstance(hook,bool):value['hook']='down' if hook else 'up'
    carrier=next((c for c in sample.get('carriers',[]) if c['instance_id']==carrier_id),None)
    volume=case1_volume(carrier,glide,offset) if carrier else None
    if volume:
        value['course']=volume['runway_heading_deg']
        p=k.get('position_world_m')
        if point(p):value['dme']=units.distance(math.dist(p,volume['origin']))
        if case in ('Case 1','Case 3'):
            m=measurement(aircraft,volume,glide)
            if m:
                value.update(gs=m['gs'],loc=m['loc'])
                value['inside']=(0<m['aft']<=(3*NM if case=='Case 3' else NM) and inside_ils(m['gs'],m['loc']))
    return value


def source_clock(sample):
    source=sample.get('source') or {}
    return next((source.get(k) for k in ('mission_time_s','real_time_s') if number(source.get(k))),sample['capture']['monotonic_s'])


def radar_trend(current, previous, aircraft_id):
    if not previous or current['capture']['connection_id']!=previous['capture']['connection_id']:return None
    dt=source_clock(current)-source_clock(previous)
    if not 0<dt<=2:return None
    def radar(frame):
        a=next((a for a in frame.get('aircraft',[]) if a['instance_id']==aircraft_id),None)
        return ((a or {}).get('kinematics') or {}).get('flight_info') or {}
    now,before=radar(current).get('radarAltitude'),radar(previous).get('radarAltitude')
    if not number(now) or not number(before):return None
    rate=(now-before)/dt
    return 1 if rate>.5 else (-1 if rate<-.5 else 0)
