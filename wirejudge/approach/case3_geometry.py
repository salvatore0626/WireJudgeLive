"""User-defined Case 3 display routes. Coordinates are game X, ASL metres, game Z.

These are reference geometry, not telemetry or an aircraft performance prediction.
DME is slant distance from the native third wire, matching the inspector.
"""
import math
from wirejudge.approach.approach_geometry import case1_volume, final_fade_profile, NM, ILS_GS_FULL_SCALE_DEG, ILS_LOC_FULL_SCALE_DEG

FT = .3048
KNOT = NM / 3600


def turn_radius(knots, load=1.4):
    return (knots*KNOT)**2 / (9.80665*math.sqrt(load*load-1))


def case3_geometry(carrier, sea_offset, glide=3.5, offset=10., final_stop_m=0.):
    if sea_offset is None or not math.isfinite(sea_offset):return None
    volume=case1_volume(carrier,glide,offset)
    if volume is None:return None
    origin=volume['origin'];deck=origin[1]+sea_offset
    heading=volume['runway_heading_deg'];angle=math.radians(heading)
    forward=(math.sin(angle),math.cos(angle));right=(math.cos(angle),-math.sin(angle))

    def at(aft,lateral,asl):
        return [origin[0]-forward[0]*aft+right[0]*lateral,asl,
                origin[2]-forward[1]*aft+right[1]*lateral]

    def fix(dme,asl,deviation=0):
        radius=math.sqrt(max(0,(dme*NM)**2-(asl-deck)**2))
        a=math.radians(deviation)
        return at(radius*math.cos(a),radius*math.sin(a),asl)

    vertices=[];faces=[];edges=[];routes=[];missed=[];glide_routes=[];marshal_routes=[]
    # Slant-DME near/far boundaries and +/-2.5-degree localizer arcs at both heights.
    steps=8
    for dme in (3,10):
        for altitude in (1000*FT,1400*FT):
            vertices.extend(fix(dme,altitude,-ILS_LOC_FULL_SCALE_DEG+2*ILS_LOC_FULL_SCALE_DEG*i/steps) for i in range(steps+1))
    stride=steps+1
    for i in range(steps):
        for a,b,c,d in ((i,i+1,stride+i+1,stride+i),
                        (2*stride+i,3*stride+i,3*stride+i+1,2*stride+i+1),
                        (i,2*stride+i,2*stride+i+1,i+1),
                        (stride+i,stride+i+1,3*stride+i+1,3*stride+i)):
            faces.extend(((a,b,c),(a,c,d)))
    for i in (0,steps):
        a,b,c,d=i,stride+i,3*stride+i,2*stride+i
        faces.extend(((a,b,c),(a,c,d)))
    for start in (0,stride,2*stride,3*stride):
        for i in range(steps):edges.extend((vertices[start+i],vertices[start+i+1]))
    for i in (0,steps):
        for a,b in ((i,stride+i),(2*stride+i,3*stride+i),(i,2*stride+i),(stride+i,3*stride+i)):
            edges.extend((vertices[a],vertices[b]))

    iaf=fix(21,6000*FT);ten=fix(10,1200*FT);six=fix(6,1200*FT);three=fix(3,1200*FT)
    # Dashed nominal descent cue, deliberately not a filled corridor beyond 10 DME.
    def dashed(a,b,destination,count=24):
        for i in range(0,count,2):
            destination.extend(([a[j]+(b[j]-a[j])*i/count for j in range(3)],
                                [a[j]+(b[j]-a[j])*(i+1)/count for j in range(3)]))
    dashed(iaf,ten,routes)
    routes.extend((ten,three))
    apex = at(0, 0, deck)
    glide_alpha = []

    def glide_ray(tip, aft, inbound=False):
        profile = final_fade_profile(aft, final_stop_m)
        for (near, a), (far, b) in zip(profile, profile[1:]):
            fractions, alpha = ((far, near), (b, a)) if inbound else ((near, far), (a, b))
            glide_routes.extend([x + (y - x) * fraction for x, y in zip(apex, tip)]
                                for fraction in fractions)
            glide_alpha.extend(alpha)
        return profile[-1][1] if profile else 0.

    g=math.radians(glide)
    center_aft = 3 * NM * math.cos(g)
    glide_ray(at(center_aft, 0, deck + 3 * NM * math.sin(g)), center_aft, inbound=True)
    final_limits=[]
    final_alpha=[]
    for gs in (glide-ILS_GS_FULL_SCALE_DEG,glide+ILS_GS_FULL_SCALE_DEG):
        a=math.radians(gs)
        for loc in (-ILS_LOC_FULL_SCALE_DEG,ILS_LOC_FULL_SCALE_DEG):
            b=math.radians(loc)
            aft = 3 * NM * math.cos(a) * math.cos(b)
            p = at(aft, 3 * NM * math.cos(a) * math.sin(b), deck + 3 * NM * math.sin(a))
            final_limits.append(p)
            final_alpha.append(glide_ray(p, aft))
    for i, j in ((0,1),(0,2),(1,3),(2,3)):
        if final_alpha[i] or final_alpha[j]:
            glide_routes.extend((final_limits[i], final_limits[j]))
            glide_alpha.extend((final_alpha[i], final_alpha[j]))
    # Visible cross-sections at speed-change/intercept stations.
    for dme in (3,6,10):routes.extend((fix(dme,1200*FT,-ILS_LOC_FULL_SCALE_DEG),fix(dme,1200*FT,ILS_LOC_FULL_SCALE_DEG)))

    # Inbound leg ends at IAF. Left racetrack, 60-second straight legs at 300 kt.
    radius=turn_radius(300);leg=300*KNOT*60
    aft=math.sqrt((21*NM)**2-(6000*FT-deck)**2)
    hold=[]
    for i in range(33):
        a=math.pi*i/32;hold.append(at(aft-radius*math.sin(a),-radius+radius*math.cos(a),6000*FT))
    hold.append(at(aft+leg,-2*radius,6000*FT))
    for i in range(1,33):
        a=math.pi+math.pi*i/32;hold.append(at(aft+leg-radius*math.sin(a),-radius+radius*math.cos(a),6000*FT))
    hold.append(iaf)
    for a,b in zip(hold,hold[1:]):marshal_routes.extend((a,b))

    def arrow(a,b,destination,length=230):
        dx,dz=b[0]-a[0],b[2]-a[2];norm=math.hypot(dx,dz)
        if norm<1e-9:return
        dx,dz=dx/norm,dz/norm
        destination.extend((b,[b[0]-dx*length-dz*length*.45,b[1],b[2]-dz*length+dx*length*.45],
                            b,[b[0]-dx*length+dz*length*.45,b[1],b[2]-dz*length-dx*length*.45]))
    arrow(at(aft+leg*.65,0,6000*FT),at(aft+leg*.45,0,6000*FT),marshal_routes)
    arrow(at(aft+leg*.35,-2*radius,6000*FT),at(aft+leg*.55,-2*radius,6000*FT),marshal_routes)
    # IAF 3D octahedron, with a dashed sea-level stem.
    size=150
    diamond=[[iaf[0]+size,iaf[1],iaf[2]],[iaf[0],iaf[1],iaf[2]+size],
             [iaf[0]-size,iaf[1],iaf[2]],[iaf[0],iaf[1],iaf[2]-size]]
    for i,p in enumerate(diamond):
        marshal_routes.extend((p,diamond[(i+1)%4],p,[iaf[0],iaf[1]+size,iaf[2]],p,[iaf[0],iaf[1]-size,iaf[2]]))
    dashed([iaf[0],0,iaf[2]],iaf,marshal_routes,20)

    # Reach 1,200 ft ASL after 2 NM of horizontal travel from wire 3.
    # Rate at 300 kt depends on the native deck ASL altitude.
    target_alt=max(deck,1200*FT)
    level_distance=2*NM
    climb_slope=(target_alt-deck)/level_distance
    climb_fpm=climb_slope*(300*KNOT)*60/FT
    four=4*NM/math.sqrt(1+climb_slope**2)
    # If already level before 4 DME, use the level altitude's slant-DME boundary.
    if level_distance<four:four=math.sqrt(max(0,(4*NM)**2-(target_alt-deck)**2))
    def climb_height(distance):return min(target_alt,deck+distance*climb_slope)
    start=at(0,0,deck);turn=at(-four,0,climb_height(four))
    missed_path=[start]
    if 0<level_distance<four:missed_path.append(at(-level_distance,0,target_alt))
    missed_path.append(turn)
    mr=turn_radius(300)
    turn_angles=[math.pi*i/32 for i in range(1,33)]
    if four<level_distance<four+math.pi*mr:turn_angles.append((level_distance-four)/mr)
    for a in sorted(turn_angles):
        missed_path.append(at(-four-mr*math.sin(a),-mr+mr*math.cos(a),climb_height(four+mr*a)))
    turn_end_distance=four+math.pi*mr
    # Include the exact level-off point on the reciprocal continuation.
    if turn_end_distance<level_distance<turn_end_distance+8*NM:
        missed_path.append(at(-four+level_distance-turn_end_distance,-2*mr,target_alt))
    outbound=at(-four+8*NM,-2*mr,climb_height(turn_end_distance+8*NM))
    missed_path.append(outbound)
    for a,b in zip(missed_path,missed_path[1:]):missed.extend((a,b))
    arrow(missed_path[-2],outbound,missed)
    arrow(start,at(-four*.6,0,climb_height(four*.6)),missed)
    labels=[(iaf,'21 DME - 6,000ft ASL\n300kt 1min legs'),
            (ten,'10 DME - 1200 ft - 250kt'),(six,'6 DME - 1200 ft - 200kt'),
            (three,'Intercept Glide - 8 AOA'),(turn,'300kt - 1200 ft'),
            (outbound,f'{(heading+180)%360:03.0f} Outbound')]
    plan_vertices=[fix(dme,1200*FT,-ILS_LOC_FULL_SCALE_DEG+2*ILS_LOC_FULL_SCALE_DEG*i/steps) for dme in (3,10) for i in range(steps+1)]
    plan_faces=[];plan_edges=[]
    for i in range(steps):
        plan_faces.extend(((i,i+1,stride+i+1),(i,stride+i+1,stride+i)))
        plan_edges.extend((plan_vertices[i],plan_vertices[i+1],plan_vertices[stride+i],plan_vertices[stride+i+1]))
    for i in (0,steps):plan_edges.extend((plan_vertices[i],plan_vertices[stride+i]))
    return dict(vertices=vertices,faces=faces,edges=edges,plan_vertices=plan_vertices,
                plan_faces=plan_faces,plan_edges=plan_edges,routes=routes,missed=missed,labels=labels,
                glide_routes=glide_routes,glide_alpha=glide_alpha,marshal_routes=marshal_routes,
                iaf=iaf,hold=hold,hold_radius_m=radius,hold_leg_m=leg,runway_heading_deg=heading,
                missed_turn=turn,missed_outbound=outbound,missed_path=missed_path,
                missed_climb_slope=climb_slope,missed_level_distance_m=level_distance,missed_climb_fpm=climb_fpm)
