"""Chart-based Case 1 marshal/entry reference, separate from observed aircraft tracks.

Coordinates are game X, ASL metres, game Z. BRC follows native carrier heading;
the final intercept follows the angled runway. Intermediate leg lengths provide
a schematic reference rather than predicting a pilot's actual maneuver.
"""
import math
from wirejudge.approach.approach_geometry import case1_volume,NM,point
from wirejudge.telemetry.telemetry_protocol import heading_from_quaternion
from wirejudge.approach.case3_geometry import FT,turn_radius


def case1_marshal_geometry(carrier,sea_offset,glide=3.5,offset=10.):
    if sea_offset is None or not math.isfinite(sea_offset):return None
    origin=carrier.get('position_world_m');brc=heading_from_quaternion(carrier.get('rotation_xyzw'))
    final=case1_volume(carrier,glide,offset)
    if not point(origin) or brc is None or final is None:return None
    angle=math.radians(brc);forward=(math.sin(angle),math.cos(angle));right=(math.cos(angle),-math.sin(angle))
    def at(lateral,along,altitude):
        return [origin[0]+right[0]*lateral+forward[0]*along,altitude,
                origin[2]+right[1]*lateral+forward[1]*along]
    def segments(path):return [p for a,b in zip(path,path[1:]) for p in (a,b)]
    def arrow(a,b,target,length=160):
        dx,dz=b[0]-a[0],b[2]-a[2];norm=math.hypot(dx,dz)
        if norm<1e-9:return
        dx,dz=dx/norm,dz/norm
        target.extend((b,[b[0]-dx*length-dz*length*.45,b[1],b[2]-dz*length+dx*length*.45],
                       b,[b[0]-dx*length+dz*length*.45,b[1],b[2]-dz*length-dx*length*.45]))

    radius=3*NM;altitude=2000*FT;marshal_shift=.25*NM;port=-6*NM+marshal_shift
    hold=[at(-radius+marshal_shift+radius*math.cos(2*math.pi*i/96),radius*math.sin(2*math.pi*i/96),altitude) for i in range(97)]
    hold[-1]=hold[0].copy()
    marshal=segments(hold)
    for i in (0,24,48,72):arrow(hold[i],hold[i+3],marshal)
    push=at(port,0,altitude)
    # Descend continuously from marshal through the left turn to upwind entry.
    # The upwind reference is 0.2 NM starboard of the carrier.
    upwind_lateral=.2*NM;loop_radius=(upwind_lateral-port)/2
    loop_start_aft=3*NM
    entry_aft=math.sqrt(NM*NM-upwind_lateral*upwind_lateral)
    turn_length=math.pi*loop_radius
    descent_length=loop_start_aft+turn_length+(loop_start_aft-entry_aft)

    def descent_alt(distance):
        return altitude+(800*FT-altitude)*distance/descent_length

    commence=[push,at(port,-loop_start_aft,descent_alt(loop_start_aft))]
    for i in range(1,49):
        a=math.pi-math.pi*i/48
        lateral=(port+upwind_lateral)/2+loop_radius*math.cos(a)
        along=-loop_start_aft-loop_radius*math.sin(a)
        commence.append(at(lateral,along,descent_alt(loop_start_aft+turn_length*i/48)))
    entry=at(upwind_lateral,-entry_aft,800*FT)
    commence.append(entry)
    arrival=segments(commence)
    halfway_to_turn=loop_start_aft/2
    arrow(push,at(port,-halfway_to_turn,descent_alt(halfway_to_turn)),arrival)
    arrow(commence[24],commence[27],arrival)

    # A semicircular break joins both straight legs tangentially.
    break_start=at(upwind_lateral,.5*NM,800*FT)
    pattern=[entry,break_start]
    downwind_lateral = -1.2 * NM
    break_radius = (upwind_lateral - downwind_lateral) / 2
    for i in range(1,33):
        a=math.pi*i/32
        pattern.append(at(upwind_lateral-break_radius+break_radius*math.cos(a),
                          .5*NM+break_radius*math.sin(a),(800-200*i/32)*FT))
    downwind=at(downwind_lateral,-1.3*NM,600*FT)
    pattern.append(downwind)
    # Smooth base turn to the existing 1-NM glideslope/localizer gate.
    h=math.radians(final['runway_heading_deg']);wire=final['origin']
    intercept=[wire[0]-math.sin(h)*NM,wire[1]+sea_offset+NM*math.tan(math.radians(glide)),wire[2]-math.cos(h)*NM]
    p0=downwind
    p1=at(downwind_lateral,-2.4*NM,600*FT)
    p2=[intercept[0]-math.sin(h)*1.1*NM,intercept[1],intercept[2]-math.cos(h)*1.1*NM]
    for i in range(1,33):
        t=i/32;u=1-t
        pattern.append([u**3*p0[j]+3*u*u*t*p1[j]+3*u*t*t*p2[j]+t**3*intercept[j] for j in range(3)])
    approach=segments(pattern)
    arrow(entry,at(upwind_lateral,-.2*NM,800*FT),approach)
    arrow(at(downwind_lateral,0,600*FT),at(downwind_lateral,-.6*NM,600*FT),approach)
    # Schematic waveoff: climb along final to 600 ft, then turn left onto
    # native BRC reciprocal. A 1-NM climb segment avoids a vertical path cue.
    missed_start=[wire[0],wire[1]+sea_offset,wire[2]]
    missed_turn=[wire[0]+math.sin(h)*NM,600*FT,wire[2]+math.cos(h)*NM]
    missed_path=[missed_start,missed_turn];mr=turn_radius(200)
    center=[missed_turn[0]-math.cos(h)*mr,600*FT,missed_turn[2]+math.sin(h)*mr]
    turn_degrees=(final['runway_heading_deg']-(brc+180))%360
    for i in range(1,33):
        bearing=h-math.radians(turn_degrees)*i/32
        missed_path.append([center[0]+math.cos(bearing)*mr,600*FT,center[2]-math.sin(bearing)*mr])
    end=missed_path[-1]
    missed_path.append([end[0]-forward[0]*2*NM,600*FT,end[2]-forward[1]*2*NM])
    missed=segments(missed_path);arrow(missed_path[-2],missed_path[-1],missed)
    labels=[(at(-3*NM+marshal_shift,3*NM,altitude),'2000ft - 350kt'),
            (entry,'800ft - 350kt'),(downwind,'600ft - 8 AOA'),(missed_turn,'600 ft - 8 AOA')]
    return dict(marshal=marshal,arrival=arrival,approach=approach,labels=labels,
                hold=hold,push=push,entry=entry,commence=commence,pattern=pattern,
                missed=missed,missed_path=missed_path,
                intercept=intercept,center=at(-radius+marshal_shift,0,altitude),radius_m=radius,
                brc_deg=brc,downwind=downwind)
