"""Carrier deck traced in SVG coordinates, with a simplified hull for the 3D map."""
from functools import lru_cache
import math
from wirejudge.paths import ASSETS_DIR
import re
from statistics import median
from xml.etree import ElementTree

from wirejudge.approach.approach_geometry import point


ASSET = ASSETS_DIR / 'models' / 'carrier-top-view-white-transparent.svg'
# Match the old map glyph's 320 m overall length until the SVG has a surveyed scale.
METRES_PER_PIXEL = 320 / (1147 - 84)
ORIGIN_X, ORIGIN_Y = 369, (84 + 1147) / 2
HULL_DEPTH_M = 18.
# Parked wheel contacts in the replay sit about 15 cm below the arresting wires.
WIRE_DECK_CLEARANCE_M = .15


def local_point(x, y):
    return (x - ORIGIN_X) * METRES_PER_PIXEL, (ORIGIN_Y - y) * METRES_PER_PIXEL


@lru_cache(maxsize=1)
def deck_geometry():
    segments = []
    outline = []
    landing_area = []
    centerline = None
    for path in ElementTree.parse(ASSET).getroot().iter('{http://www.w3.org/2000/svg}path'):
        # The bundled trace consists of straight paths with absolute M/L/Z commands.
        for subpath in path.attrib['d'].split('M')[1:]:
            coordinates = [float(value) for value in re.findall(r'-?\d+(?:\.\d+)?', subpath)]
            vertices = [local_point(x, y) for x, y in zip(coordinates[::2], coordinates[1::2])]
            identifier = path.get('id')
            if identifier == 'deck-outline':
                outline = vertices
            if identifier == 'landing-area':
                landing_area = vertices
            if identifier == 'landing-centerline':
                a, b = vertices
                centerline = a, b
                length = math.dist(a, b)
                on, off = (float(n) * METRES_PER_PIXEL for n in path.get('stroke-dasharray').split())
                def at(distance):
                    return tuple(x + (y - x) * distance / length for x, y in zip(a, b))
                for start in range(math.ceil(length / (on + off))):
                    distance = start * (on + off)
                    segments.append((at(distance), at(min(distance + on, length))))
                continue
            if subpath.rstrip().endswith('Z'):
                vertices = vertices + vertices[:1]
            segments.extend(zip(vertices, vertices[1:]))
    return segments, outline, centerline, landing_area


@lru_cache(maxsize=1)
def catapult_tracks():
    tracks = []
    for path in ElementTree.parse(ASSET).getroot().iter('{http://www.w3.org/2000/svg}path'):
        if path.get('id') not in ('bow-deck-guides', 'port-deck-guides'):
            continue
        for subpath in path.attrib['d'].split('M')[1:]:
            x1, y1, x2, y2 = (float(v) for v in re.findall(r'-?\d+(?:\.\d+)?', subpath))
            tracks.append((local_point(x1, y1), local_point(x2, y2)))
    return tuple(tracks)


def cross(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


@lru_cache(maxsize=1)
def deck_triangles():
    """Triangulate the concave SVG outline without filling its notches."""
    _, outline, _, _ = deck_geometry()
    vertices = list(range(len(outline)))
    area = sum(a[0] * b[1] - a[1] * b[0] for a, b in zip(outline, outline[1:] + outline[:1]))
    if area < 0:
        vertices.reverse()
    triangles = []
    while len(vertices) > 3:
        for slot, current in enumerate(vertices):
            before, after = vertices[slot - 1], vertices[(slot + 1) % len(vertices)]
            triangle = before, current, after
            a, b, c = (outline[index] for index in triangle)
            if cross(a, b, c) <= 0:
                continue
            if any(cross(a, b, outline[i]) >= 0 and cross(b, c, outline[i]) >= 0
                   and cross(c, a, outline[i]) >= 0 for i in vertices if i not in triangle):
                continue
            triangles.append(triangle)
            vertices.pop(slot)
            break
        else:
            raise ValueError('Unable to triangulate carrier deck outline')
    triangles.append(tuple(vertices))
    return triangles


def island_edges(project, deck_z):
    """Draw the starboard island in the same local frame as the deck trace."""
    # Center of the island footprint in the overhead screenshot.
    right, forward = local_point(453, 887)
    # Height above deck, width, and length in metres.
    tiers = ((.1, 13, 21), (7, 13, 21), (11, 18, 25), (15, 18, 25),
             (18, 13, 19), (21, 13, 19), (22, 5, 7), (33, 2, 3))
    previous = None
    for height, width, length in tiers:
        ring = [project((right + x * width / 2, forward + y * length / 2), deck_z + height)
                for x, y in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
        yield from zip(ring, ring[1:] + ring[:1])
        if previous is not None:
            yield from zip(previous, ring)
        previous = ring
    for height, span in ((27, 13), (32, 10)):
        yield (project((right - span / 2, forward), deck_z + height),
               project((right + span / 2, forward), deck_z + height))


def deck_projection(ship, origin, angle, transform):
    _, _, (bow, stern), _ = deck_geometry()
    c, s = math.cos(angle), math.sin(angle)
    offsets, elevations = [], []
    for cable in ship.get('cables') or []:
        a, b = cable.get('endpoint1_world_m'), cable.get('endpoint2_world_m')
        center = [(x + y) / 2 for x, y in zip(a, b)] if point(a) and point(b) else cable.get('position_world_m')
        if not point(center):
            continue
        x, y, z = transform(center)
        dx, dy = x - origin[0], y - origin[1]
        right, forward = c * dx - s * dy, s * dx + c * dy
        traced_right = bow[0] + (stern[0] - bow[0]) * (forward - bow[1]) / (stern[1] - bow[1])
        offsets.append(right - traced_right)
        elevations.append(z)
    right_offset = median(offsets) if offsets else 0.
    deck_z = median(elevations) - WIRE_DECK_CLEARANCE_M if elevations else origin[2]

    def project(vertex, elevation):
        right, forward = vertex
        right += right_offset
        return origin[0] + c * right + s * forward, origin[1] - s * right + c * forward, elevation

    return project, deck_z, bool(elevations)


def landing_area_world(ship, heading):
    """Return the traced landing area in (world X, world Z, elevation) order."""
    position = ship.get('position_world_m')
    if heading is None or not point(position):
        return ()
    def horizontal(p):
        return p[0], p[2], p[1]
    project, deck_z, surveyed = deck_projection(ship, horizontal(position), math.radians(heading), horizontal)
    return tuple(project(vertex, deck_z) for vertex in deck_geometry()[3]) if surveyed else ()


def deck_triangles_world(ship, heading):
    """Return the full deck mask in (world X, world Z, elevation) order."""
    position = ship.get('position_world_m')
    if heading is None or not point(position):
        return ()

    def horizontal(p):
        return p[0], p[2], p[1]

    project, deck_z, _ = deck_projection(ship, horizontal(position), math.radians(heading), horizontal)
    vertices = [project(vertex, deck_z) for vertex in deck_geometry()[1]]
    return [tuple(vertices[index] for index in face) for face in deck_triangles()]


def carrier_geometry(ship, origin, angle, transform, is_3d, *, sea_level=None):
    """Return deck lines, 3D structure lines and depth-mask triangles in map coordinates."""
    segments, outline, _, _ = deck_geometry()
    project, deck_z, _ = deck_projection(ship, origin, angle, transform)
    deck = [(project(a, deck_z), project(b, deck_z)) for a, b in segments]
    upper = [project(vertex, deck_z) for vertex in outline]
    mask = [tuple(upper[index] for index in face) for face in deck_triangles()]
    if not is_3d:
        return deck, [], mask
    hull_bottom = sea_level if sea_level is not None and sea_level < deck_z else deck_z - HULL_DEPTH_M
    lower = [project((right * .64, forward * .93), hull_bottom) for right, forward in outline]
    hull = list(zip(lower, lower[1:] + lower[:1]))
    hull.extend((upper[index], lower[index]) for index in (0, 1, 3, 8, 11, 13, 16, 24))
    hull.extend(island_edges(project, deck_z))
    return deck, hull, mask

def deck_height_at(position, triangles):
    """Find the height of a deck directly underneath a nearby aircraft."""
    x, y, z = position
    for triangle in triangles:
        height = triangle[0][2]
        if abs(z - height) > 8.:
            continue
        sides = [(b[0]-a[0])*(y-a[1]) - (b[1]-a[1])*(x-a[0])
                 for a, b in zip(triangle, (*triangle[1:], triangle[0]))]
        if min(sides) >= 0 or max(sides) <= 0:
            return height
    return None


DIGIT_SEGMENTS = ('abcdef', 'bc', 'abdeg', 'abcdg', 'bcfg',
                  'acdfg', 'acdefg', 'abc', 'abcdefg', 'abcdfg')
DIGIT_STROKES = dict(zip('abcdefg', (
    ((0, 1), (1, 1)), ((1, 1), (1, .5)), ((1, .5), (1, 0)),
    ((0, 0), (1, 0)), ((0, .5), (0, 0)), ((0, 1), (0, .5)), ((0, .5), (1, .5)),
)))


def tower_details(project, deck_z, wind):
    """Yield colored line segments for the 3D island details."""
    right, forward = local_point(453, 887)

    def at(x, y, z):
        return project((right + x, forward + y), deck_z + z)

    east, north = wind
    speed = math.hypot(east, north)
    dx, dy = (east / speed, north / speed) if speed > .01 else (0., -1.)
    stretch = min(1., speed / 7.7)
    yield at(0, 0, 33), at(0, 0, 39), 'carrier'
    rings = []
    reach, height, previous_bend = .55, 39., 0.
    for index in range(6):
        t = index / 5
        radius = 1.3 - .9 * t
        # Stronger wind holds more of the sock level before the tail starts to bend.
        slack = 1 - stretch
        bend = math.pi / 2 * max(0., t - stretch) / math.sqrt(slack) if slack else 0.
        if index:
            tangent = (bend + previous_bend) / 2
            reach += 1.4 * math.cos(tangent)
            height -= 1.4 * math.sin(tangent)
        previous_bend = bend
        ring = [at(dx * (reach + radius * math.sin(a) * math.sin(bend)) - dy * radius * math.cos(a),
                   dy * (reach + radius * math.sin(a) * math.sin(bend)) + dx * radius * math.cos(a),
                   height + radius * math.sin(a) * math.cos(bend))
                for a in (math.tau * j / 8 for j in range(8))]
        for a, b in zip(ring, ring[1:] + ring[:1]):
            yield a, b, 'accent'
        if rings:
            for a, b in zip(rings[-1], ring):
                yield a, b, 'accent' if index % 2 else 'text'
        rings.append(ring)

def tower_flag(project, deck_z, wind, seconds, banana=False):
    """Draw a traveling ripple along the flag, anchored to the pole at u=0."""
    right, forward = local_point(453, 887)
    east, north = wind
    speed = math.hypot(east, north)
    dx, dy = (east / speed, north / speed) if speed > .01 else (0., -1.)
    stretch = min(1., speed / 7.7)
    phase = seconds * math.tau * 1.5

    def at(x, y, z):
        return project((right + x, forward - 6 + y), deck_z + z)

    yield at(0, 0, 22), at(0, 0, 36), 'carrier'

    def flag_point(u, v):
        extent = u * (1.5 + 5 * stretch)
        wave = phase - math.tau * 1.25 * u + .75 * v
        amplitude = u * stretch
        ripple = .9 * amplitude * math.sin(wave)
        height = 35.5 - v * 3.4 - u * (1 - stretch) * 3.5
        return at(dx * extent - dy * ripple, dy * extent + dx * ripple,
                  height + .45 * amplitude * math.cos(wave))

    def stripe(start, end, v, role):
        steps = max(1, math.ceil((end - start) * 10))
        points = [flag_point(start + (end - start) * i / steps, v) for i in range(steps + 1)]
        for a, b in zip(points, points[1:]):
            yield a, b, role

    if banana:
        for v in (0., .08, .16, .84, .92, 1.):
            yield from stripe(0., 1., v, 'slow')
        for u in (0., 1.):
            yield flag_point(u, 0.), flag_point(u, 1.), 'slow'

        # Curved fruit silhouette and stem share the cloth's wind deformation.
        outline = ((.24, .27), (.28, .40), (.38, .51), (.50, .55), (.62, .53),
                   (.72, .44), (.77, .33), (.82, .34), (.80, .49), (.72, .63),
                   (.62, .73), (.50, .77), (.39, .73), (.30, .63), (.24, .48),
                   (.21, .33), (.24, .27))
        for a, b in zip(outline, outline[1:]):
            yield flag_point(*a), flag_point(*b), 'accent'
        for v in (.59, .64, .69):
            yield from stripe(.34 + (v - .59), .72 - (v - .59), v, 'accent')
        yield flag_point(.79, .35), flag_point(.83, .23), 'accent'
        return

    for i in range(13):
        v = (i + .5) / 13
        yield from stripe(.42 if i < 7 else 0., 1., v, '#DC3448' if i % 2 == 0 else '#F3F3F3')
    for i in range(8):
        yield from stripe(0., .42, i / 13, '#526CBC')
    for row in range(3):
        for col in range(4):
            u, v = .05 + col * .10, .08 + row * .16
            yield flag_point(u, v), flag_point(u + .022, v), '#FFFFFF'


def tower_numbers(project, deck_z, number):
    """Build the two number faces with their outward normals for camera culling."""
    right, forward = local_point(453, 887)
    digits = str(number)
    offset = (len(digits) * 3.8 - 1.1) / 2
    for side in (-1, 1):
        face = right + side * 6.55
        center = project((face, forward), deck_z + 4)
        outside = project((face + side, forward), deck_z + 4)
        normal = (outside[0] - center[0], outside[1] - center[1])
        vertices = []
        for i, digit in enumerate(digits):
            for stroke in DIGIT_SEGMENTS[int(digit)]:
                for x, y in DIGIT_STROKES[stroke]:
                    vertices.append(project((face, forward + side * (i * 3.8 + x * 2.7 - offset)),
                                            deck_z + 2 + y * 4))
        yield center, normal, vertices
