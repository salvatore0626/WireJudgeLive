"""Case 1 display geometry derived from native ship rotation and cable positions."""
import math
from wirejudge.telemetry.telemetry_protocol import heading_from_quaternion

NM = 1852.0
ILS_GS_FULL_SCALE_DEG = .7
ILS_LOC_FULL_SCALE_DEG = 2.5


def inside_ils(gs,loc):
    """Include the exact full-scale boundary despite roundoff in trig conversion."""
    return all(isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v)
               and abs(v)<=limit+1e-9 for v,limit in
               ((gs,ILS_GS_FULL_SCALE_DEG),(loc,ILS_LOC_FULL_SCALE_DEG)))


def point(value):
    return isinstance(value, list) and len(value) == 3 and all(
        isinstance(x, (int,float)) and math.isfinite(x) for x in value)


def case1_volume(carrier, glide_deg=3.5, runway_offset_deg=10.):
    heading = heading_from_quaternion(carrier.get('rotation_xyzw'))
    if heading is None:
        return None
    runway_heading = (heading-runway_offset_deg) % 360
    angle = math.radians(runway_heading)
    forward = (math.sin(angle), math.cos(angle))  # Game X, Z
    right = (math.cos(angle), -math.sin(angle))
    cables = []
    for cable in carrier.get('cables') or []:
        position = cable.get('position_world_m')
        if not point(position):
            a,b = cable.get('endpoint1_world_m'), cable.get('endpoint2_world_m')
            if not point(a) or not point(b):
                continue
            position = [(a[i]+b[i])*.5 for i in range(3)]
        cables.append((position[0]*forward[0]+position[2]*forward[1], position, cable.get('instance_id')))
    if len(cables) != 4:
        return None  # Do not invent the third wire from incomplete arresting geometry.
    cables.sort(key=lambda item:item[0])  # Aft to forward, independent of exporter array order/name.
    _, origin, cable_id = cables[2]
    distance = NM
    half_width = distance*math.tan(math.radians(ILS_LOC_FULL_SCALE_DEG))
    center = [origin[0]-forward[0]*distance, origin[1], origin[2]-forward[1]*distance]
    low = origin[1]+distance*math.tan(math.radians(glide_deg-ILS_GS_FULL_SCALE_DEG))
    high = origin[1]+distance*math.tan(math.radians(glide_deg+ILS_GS_FULL_SCALE_DEG))
    lower_left = [center[0]-right[0]*half_width, low, center[2]-right[1]*half_width]
    lower_right = [center[0]+right[0]*half_width, low, center[2]+right[1]*half_width]
    upper_left = [lower_left[0],high,lower_left[2]]
    upper_right = [lower_right[0],high,lower_right[2]]
    center[1] = origin[1]+distance*math.tan(math.radians(glide_deg))
    return {'origin':list(origin), 'center':center,
        'corners':[lower_left,lower_right,upper_right,upper_left],
        'wire_id':cable_id, 'wire_ids':[c[2] for c in cables], 'runway_heading_deg':runway_heading,
        'range_m':distance, 'half_width_m':half_width}


def grading_gate_sections(volume, case, rules, sea_offset):
    """Grade boundaries in game X, world Y, game Z, measured along the runway axis."""
    leg, targets = rules[case], rules['targets']
    gates = [('Final start', leg['start_nm'], False), ('Final stop', leg['end_nm'], False)]
    if case == 'Case 3':
        gates += [('Platform start', leg['platform_start_nm'], True),
                  ('Platform stop', leg['platform_end_nm'], True)]
    x, y, z = volume['origin']
    angle = math.radians(volume['runway_heading_deg'])
    forward_x, forward_z = math.sin(angle), math.cos(angle)
    width_slope = math.tan(math.radians(targets['loc_tolerance']))
    glide = targets['glide_target']
    tolerance = targets['glide_tolerance']
    for title, distance_nm, platform in gates:
        aft = distance_nm * NM
        half_width = max(10., aft * width_slope)  # Keep near-wire gates visible.
        if platform:
            target = targets['platform_alt_ft']
            spread = targets['platform_alt_tolerance_ft']
            low = (target - spread) * .3048 - sea_offset
            high = (target + spread) * .3048 - sea_offset
        else:
            low = y + aft * math.tan(math.radians(glide - tolerance))
            high = y + aft * math.tan(math.radians(glide + tolerance))
        center_x, center_z = x - aft * forward_x, z - aft * forward_z
        left = center_x - half_width * forward_z, center_z + half_width * forward_x
        right = center_x + half_width * forward_z, center_z - half_width * forward_x
        yield title, distance_nm, [[left[0], low, left[1]], [right[0], low, right[1]],
                                  [right[0], high, right[1]], [left[0], high, left[1]]]


def final_fade_profile(aft_m, stop_m):
    """Return (fraction along a final-approach ray, opacity) from near to far."""
    if stop_m <= 0:
        return ((0., 1.), (1., 1.))
    clear_m = stop_m / 2
    if aft_m <= clear_m:
        return ()
    start = (clear_m / aft_m, 0.)
    if aft_m <= stop_m:
        return start, (1., (aft_m - clear_m) / clear_m)
    return start, (stop_m / aft_m, 1.), (1., 1.)


def case1_glide_display(volume, stop_m, is_3d):
    """Clip the final beam into slices so lines and walls share the same fade."""
    origin, corners = volume['origin'], volume['corners']
    if not is_3d:
        corners = [[x, origin[1], z] for x, _, z in corners[:2]]
    profile = final_fade_profile(volume['range_m'], stop_m)
    vertices, faces, edges, edge_alpha, wall_alpha = [], [], [], [], []

    def interpolate(tip, fraction):
        return [a + (b - a) * fraction for a, b in zip(origin, tip)]

    def ray(tip):
        for (start, a), (end, b) in zip(profile, profile[1:]):
            edges.extend((interpolate(tip, start), interpolate(tip, end)))
            edge_alpha.extend((a, b))

    for tip in [*corners, volume['center']]:
        ray(tip)
    size = len(corners)
    for fraction, alpha in profile:
        vertices.extend(interpolate(tip, fraction) for tip in corners)
        wall_alpha.extend([alpha] * size)
    for section in range(len(profile) - 1):
        near = section * size
        far = near + size
        for side in range(size if is_3d else 1):
            next_side = (side + 1) % size
            a, b, c, d = near + side, near + next_side, far + next_side, far + side
            if profile[section][0] > 0:
                faces.append((a, b, c))
            faces.append((a, c, d))
    if profile and is_3d:
        faces.extend(((far + 1, far + 3, far + 2), (far + 1, far, far + 3)))
        for i in range(size):
            edges.extend((corners[i], corners[(i + 1) % size]))
            edge_alpha.extend((profile[-1][1],) * 2)
    elif profile:
        edges.extend(corners)
        edge_alpha.extend((profile[-1][1],) * 2)
    return vertices, faces, edges, wall_alpha, edge_alpha
