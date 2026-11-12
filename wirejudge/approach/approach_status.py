"""Shared approach gates, speed colors, warnings and wire-three scope projection."""
import math
from wirejudge.ui import theme
from wirejudge.approach.approach_geometry import case1_volume,inside_ils,ILS_LOC_FULL_SCALE_DEG,ILS_GS_FULL_SCALE_DEG,NM
from wirejudge.approach.approach_detection import measurement,number,ENTRY_HEADING_LIMIT_DEG
from wirejudge.aircraft.flight_instruments import gear_state,fuel_values,low_fuel
from wirejudge.aircraft.aircraft_list import flight_identity
from wirejudge.telemetry.telemetry_protocol import heading_from_quaternion


COLOR_LOC_LIMIT_DEG=2*ILS_LOC_FULL_SCALE_DEG
COLOR_GS_LIMIT_DEG=2*ILS_GS_FULL_SCALE_DEG


def inside_color_zone(gs,loc):
    return number(gs) and number(loc) and abs(gs)<=COLOR_GS_LIMIT_DEG+1e-9 and abs(loc)<=COLOR_LOC_LIMIT_DEG+1e-9


def aoa_band(aoa,target=8.,tolerance=1.):
    if not number(aoa):return None
    return 'fast' if aoa<target-tolerance else ('slow' if aoa>target+tolerance else 'on')


def ias_band(ias_mps,target_knots):
    if not number(ias_mps):return None
    error=ias_mps*3600/1852-target_knots
    return 'fast' if error>25+1e-9 else ('slow' if error<-25-1e-9 else 'on')


def approach_states(sample,carrier_id,case,glide=3.5,offset=10.):
    carrier=next((c for c in (sample or {}).get('carriers',[]) if c.get('instance_id')==carrier_id),None)
    volume=case1_volume(carrier,glide,offset) if carrier else None
    states={}
    if not volume:return volume,states
    for contact in sample.get('aircraft',[]):
        observation = contact.get('_radar_observation')
        aircraft, observed_volume = contact, volume
        if observation:
            aircraft, observed_carrier = observation
            observed_volume = case1_volume(observed_carrier, glide, offset)
        if not observed_volume:
            continue
        m=measurement(aircraft,observed_volume,glide)
        if not m or m['aft']<=0:continue
        p=aircraft['kinematics']['position_world_m']
        dme=math.dist(p,observed_volume['origin'])/NM
        in_range=m['aft']<=(3*NM if case=='Case 3' else NM)+1e-6
        final=in_range and inside_color_zone(m['gs'],m['loc'])
        warning_gate=in_range and inside_ils(m['gs'],m['loc'])
        lateral=abs(m['loc'])<=COLOR_LOC_LIMIT_DEG+1e-9
        band=None;target=None
        if final:band=aoa_band(m['aoa'])
        elif case=='Case 3' and lateral and 3-1e-9<=dme<=10+1e-9:
            target=250 if dme>6+1e-9 else 200
            band=ias_band((aircraft['kinematics'].get('flight_info') or {}).get('indicatedAirspeed'),target)
        systems=aircraft.get('systems') or {};warnings=[]
        if warning_gate:
            if gear_state(systems.get('gear')) in ('up','moving'):warnings.append('Gear')
            if (systems.get('tailhook') or {}).get('isDeployed') is False:warnings.append('Hook')
            if low_fuel(fuel_values(systems.get('fuel_tanks'))[2]):warnings.append('Fuel')
        identity=flight_identity(aircraft)[2]
        designation=(aircraft.get('identity') or {}).get('designation')
        label=identity.split(' ',1)[0] if designation else str(aircraft['instance_id'])
        f=aircraft['kinematics'].get('flight_info') or {}
        heading=f.get('heading')
        if not number(heading):heading=heading_from_quaternion(aircraft['kinematics'].get('rotation_xyzw'))
        bearing=math.degrees(math.atan2(observed_volume['origin'][0]-p[0],observed_volume['origin'][2]-p[2]))
        heading_error = abs((heading-bearing+180)%360-180) if number(heading) else None
        nose_on = heading_error is not None and heading_error <= 7+1e-9
        inbound = heading_error is not None and heading_error <= ENTRY_HEADING_LIMIT_DEG+1e-9
        states[aircraft['instance_id']]=dict(m,id=aircraft['instance_id'],dme=dme,final=final,warning_gate=warning_gate,
            lateral=lateral,band=band,target_ias=target,warnings=tuple(warnings),label=label,
            inbound=inbound,resting=(aircraft.get('display') or {}).get('resting') is True,nose_on=nose_on,pitch=f.get('pitch') if number(f.get('pitch')) else None,roll=f.get('roll') if number(f.get('roll')) else None,aircraft=contact)
    return volume,states


def scope_projection(state,glide):
    """Perspective camera at wire 3 looking aft, pitched up by the glide angle.

    Aircraft left (pilot's view) appears on the right to an LSO facing inbound.
    Returned x/y are camera-plane ratios, not a flat bearing/elevation shortcut.
    """
    angle=math.radians(glide);height,aft=state['height'],state['aft']
    depth=aft*math.cos(angle)+height*math.sin(angle)
    if depth<=0:return None
    return state['left']/depth,(height*math.cos(angle)-aft*math.sin(angle))/depth


def status_color(base,state,phase=1.,warning=None):
    """Float RGBA, with speed colors and a visible red pulse even for fast red."""
    if state and state.get('aircraft', {}).get('_radar_stale'):
        return base
    warning=warning or theme.PALETTE['warning']
    red=tuple(int(warning.lstrip('#')[i:i+2],16)/255 for i in (0,2,4))
    if state and state.get('warnings'):
        brightness=.35+.65*phase
        return tuple(c*brightness for c in red)+(1.,)
    band=(state or {}).get('band')
    return {**{role:tuple(int(theme.PALETTE[role][i:i+2],16)/255 for i in (1,3,5))+(1.,) for role in ('slow','on')},'fast':red+(1.,)}.get(band,base)


LANDING_WINGSPAN_M=18.
LANDING_MIN_SPAN_PX=18.


def scope_appearance(distance,nearest,farthest,focal_pixels=1000.,max_span_px=144.,depth_m=None):
    """18 m perspective span plus a small legibility floor, capped near the wire.

    The full wingspan is six circle radii. The additive floor keeps distant
    traffic readable without flattening the inverse-distance size cue.
    """
    distance=max(0.,distance)
    depth=max(1.,depth_m if number(depth_m) else distance*NM)
    projected=LANDING_WINGSPAN_M*max(1.,focal_pixels)/depth
    span=min(max(LANDING_MIN_SPAN_PX,max_span_px),LANDING_MIN_SPAN_PX+projected)
    fade=0. if farthest-nearest<1e-9 else max(0,min(1,(distance-nearest)/(farthest-nearest)))
    return span/6.,1-.7*fade


def landing_scope_state(state):
    """Scope-only AoA gate: visible and nose-on, independent of map color gates."""
    return dict(state,band=aoa_band(state.get('aoa')) if state.get('nose_on') else None)
