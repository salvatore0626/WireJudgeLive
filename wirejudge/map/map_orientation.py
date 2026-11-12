"""World compass axes expressed in the map's optional carrier-relative frame."""
import math


def compass_axes(reference_heading_deg=0.):
    angle=math.radians(reference_heading_deg)
    north=(-math.sin(angle),math.cos(angle),0.)
    east=(math.cos(angle),math.sin(angle),0.)
    return north,east
