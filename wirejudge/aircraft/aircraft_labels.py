"""Attached data tags using native values and app-derived approach geometry."""
import re
import math
from wirejudge.aircraft.aircraft_list import flight_identity
from wirejudge.approach.approach_detection import measurement,number
from wirejudge.approach.approach_geometry import case1_volume,inside_ils,NM,point
from wirejudge.aircraft.flight_instruments import gear_state,fuel_values,low_fuel
from wirejudge.map.marshal_vectors import vector_owner

LABEL_FIELDS={'flight':'Flight','name':'Name','ias':'Speed IAS','altitude':'Alt','dme':'DME','aoa':'AoA','rtb':'RTB Time','warnings':'Warnings'}
DEFAULT_LABEL_FIELDS=['flight','name','ias','altitude','dme','aoa','rtb','warnings']


def aircraft_callsign(aircraft):
    identity = aircraft.get('identity') or {}
    designation=str(identity.get('designation') or '').strip()
    return flight_identity(aircraft)[2].split(' ',1)[0] if re.fullmatch(r'[A-Za-z]+(?:-[A-Za-z]+)?\s*\d+\s*-\s*\d+',designation) else designation


def rtb_speed(aircraft):
    speed = ((aircraft.get('kinematics') or {}).get('flight_info') or {}).get('airspeed')
    return speed if number(speed) and speed >= 0 else vector_owner(aircraft)[1]


def rtb_distance(aircraft, carrier):
    position = (aircraft.get('kinematics') or {}).get('position_world_m')
    origin = (carrier or {}).get('position_world_m')
    if point(position) and point(origin):
        return math.hypot(position[0] - origin[0], position[2] - origin[2])
    return None


def aircraft_label(aircraft,fields,units,volume,case,glide,resting=False,rtb_seconds=None):
    if resting:fields=[key for key in fields if key in ('flight','name')]
    identity=aircraft.get('identity') or {};f=(aircraft.get('kinematics') or {}).get('flight_info') or {}
    frame=identity.get('player_vehicle') or '';actor=identity.get('actor_name') or ''
    pilot=actor[len(frame)+2:-1] if frame and actor.startswith(frame+' (') and actor.endswith(')') else actor
    lines=[]
    if 'name' in fields:lines.append((pilot or '—',False))
    if 'flight' in fields:lines.append((aircraft_callsign(aircraft) or '—',False))
    if 'ias' in fields:
        speed=units.speed(f.get('indicatedAirspeed'))
        lines.append(('—' if speed is None else f'{speed:.0f} {units.speed_symbol}',False))
    if 'altitude' in fields:
        alt=units.altitude(f.get('altitudeASL'))
        lines.append(('— ASL' if alt is None else f'{alt:.0f} {units.altitude_symbol} ASL',False))
    if 'dme' in fields:
        position=(aircraft.get('kinematics') or {}).get('position_world_m')
        dme=units.distance(math.dist(position,volume['origin'])) if volume and point(position) else None
        lines.append(('— DME' if dme is None else f'{dme:.2f} DME',False))
    if 'aoa' in fields:
        aoa = f.get('aoa')
        lines.append((f'{aoa:.1f}° AoA' if number(aoa) else '— AoA', False))
    if 'rtb' in fields:
        if rtb_seconds is None:
            lines.append(('— RTB', False))
        else:
            hours, remainder = divmod(round(rtb_seconds), 3600)
            minutes, seconds = divmod(remainder, 60)
            lines.append((f'{hours:02d}:{minutes:02d}:{seconds:02d} RTB', False))
    if 'warnings' in fields and volume:
        m=measurement(aircraft,volume,glide)
        limit=3*NM if case=='Case 3' else NM
        if m and 0<m['aft']<=limit and inside_ils(m['gs'],m['loc']):
            systems=aircraft.get('systems') or {};warnings=[]
            gear=gear_state(systems.get('gear'))
            if gear in ('up','moving'):warnings.append('GEAR '+gear.upper())
            if (systems.get('tailhook') or {}).get('isDeployed') is False:warnings.append('HOOK UP')
            lines.extend((warning,True) for warning in warnings)
    if 'warnings' in fields:
        percent=fuel_values((aircraft.get('systems') or {}).get('fuel_tanks'))[2]
        if low_fuel(percent):lines.append(('Fuel',True))
    return tuple(lines)


def label_volume(carrier,glide,offset):
    return case1_volume(carrier,glide,offset) if carrier else None


from wirejudge.aircraft.landed_labels import settled_aircraft
