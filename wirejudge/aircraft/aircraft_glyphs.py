"""Triangular aircraft glyphs with an upright tail and native/relative velocity vectors."""
from functools import lru_cache
import math
from wirejudge.paths import ASSETS_DIR
import re
from xml.etree import ElementTree
import numpy as np

# Body coordinates: X right, Y up, Z nose. A flat triangle plus one dorsal fin.
ARROW = np.array([[0,0,.65],[-.4,0,-.45],[.4,0,-.45],
                  [0,0,-.43],[0,0,-.12],[0,.4,-.34]],dtype=float)
FACES = np.array([[0,1,2],[3,4,5]],dtype=np.uint32)
OUTLINE_EDGES=((0,1),(1,2),(2,0),(3,4),(4,5),(5,3))
SIZE_SCALE=.5


AIRFRAME_SPAN_M = {'av42c': 14., 'fa26b': 18., 'f45a': 11.,
                   'ah94': 15., 't55': 10., 'ef24g': 21.}
DETAIL_MIN_PIXELS = 24.
AIRCRAFT_ART = ASSETS_DIR / 'aircraft'


def airframe_key(name):
    key = re.sub(r'[^a-z0-9]', '', str(name or '').lower())
    return key if key in AIRFRAME_SPAN_M else None


@lru_cache(maxsize=4 * len(AIRFRAME_SPAN_M))
def wireframe_edges(key, gear_down=False, hook_down=False):
    try:
        root = ElementTree.parse(AIRCRAFT_ART / f'{key}.svg').getroot()
    except (OSError, ElementTree.ParseError):
        return None
    edges = []
    deployed_edges = []
    deployed = {'gear': gear_down, 'hook': hook_down}
    for element in root.iter():
        shape = element.tag.rsplit('}', 1)[-1]
        if shape not in ('polyline', 'polygon'):
            continue
        condition = element.get('data-when')
        if condition and not deployed.get(condition):
            continue
        points = [tuple(map(float, pair.split(','))) for pair in element.attrib['points'].split()]
        # Optional per-vertex elevation in the same units as the SVG plan view.
        heights = element.get('data-heights')
        elevations = list(map(float, heights.split())) if heights else [0.] * len(points)
        points = [(x - 50, 52 - y, z) for (x, y), z in zip(points, elevations, strict=True)]
        if shape == 'polygon':
            points.append(points[0])
        target = deployed_edges if condition else edges
        for a, b in zip(points, points[1:]):
            target.extend((a, b))
    if not edges:
        return None
    points = np.asarray(edges)
    width = float(np.ptp(points[:, 0]))
    if width <= 0:
        return None
    return np.asarray(edges + deployed_edges) * (AIRFRAME_SPAN_M[key] / width)


def detailed_glyph(key, directions, scale, metres_per_pixel, *, gear_down=False, hook_down=False):
    span = AIRFRAME_SPAN_M.get(key)
    if span is None or metres_per_pixel <= 0:
        return None
    if span * scale / metres_per_pixel < DETAIL_MIN_PIXELS:
        return None
    edges = wireframe_edges(key, gear_down, hook_down)
    if edges is None:
        return None
    right = (directions[2] - directions[1]) / .8
    forward = (directions[0] - (directions[1] + directions[2]) / 2) / 1.1
    up = np.cross(right, forward)
    return (edges @ np.stack((right, forward, up))) * scale


def finite_vector(value, length=3):
    return isinstance(value,(list,tuple,np.ndarray)) and len(value)==length and all(
        isinstance(v,(int,float,np.number)) and math.isfinite(v) for v in value)


def world_to_map_vectors(vectors, reference_heading_deg=0):
    values=np.asarray(vectors,dtype=float)
    h=math.radians(reference_heading_deg)
    x,y,z=values[...,0],values[...,1],values[...,2]
    return np.stack((math.cos(h)*x-math.sin(h)*z,
                     math.sin(h)*x+math.cos(h)*z,y),axis=-1)


def arrow_directions(quaternion, reference_heading_deg=0):
    if not finite_vector(quaternion,4):
        return None
    q=np.asarray(quaternion,dtype=float)
    norm=np.linalg.norm(q)
    if norm<1e-12:
        return None
    x,y,z,w=q/norm
    rotation=np.array([[1-2*(y*y+z*z),2*(x*y-z*w),2*(x*z+y*w)],
                       [2*(x*y+z*w),1-2*(x*x+z*z),2*(y*z-x*w)],
                       [2*(x*z-y*w),2*(y*z+x*w),1-2*(x*x+y*y)]])
    return world_to_map_vectors(ARROW@rotation.T,reference_heading_deg)


def velocity_vector(position, velocity, reference=None):
    if not finite_vector(velocity):
        return None
    result=np.array(velocity,dtype=float)
    heading=0
    if reference is not None:
        cp,cv,omega=reference.get('position_world_m'),reference.get('velocity_world_mps'),reference.get('angular_velocity_world_radps')
        heading=(reference.get('derived') or {}).get('heading_deg') or 0.
        if finite_vector(cv):
            result -= np.asarray(cv)
        if all(finite_vector(v) for v in (position,cp,omega)):
            result -= np.cross(omega,np.asarray(position)-np.asarray(cp))
    return world_to_map_vectors(result,heading)
