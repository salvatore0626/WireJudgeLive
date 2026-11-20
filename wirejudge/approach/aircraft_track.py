"""Match successive observations in a shared world coordinate frame."""
from dataclasses import dataclass
import math

from wirejudge.approach.approach_geometry import point


MIN_DISPLACEMENT_M = 20.0
MIN_TRAVEL_TIME_S = 3.0
TRAVEL_MARGIN = 1.20


@dataclass(frozen=True, slots=True)
class AircraftTrack:
    airframe: str | None
    position: tuple[float, float] | None
    position_time: float
    groundspeed: float | None


def track_observation(previous, airframe, kinematics, translation, stamp):
    """Return the latest usable state and whether it starts another aircraft life."""
    airframe = airframe.strip() if isinstance(airframe, str) else None
    position = kinematics.get('position_world_m')
    horizontal = None
    if point(position):
        horizontal = (position[0] + translation[0], position[2] + translation[2])
    velocity = kinematics.get('velocity_world_mps')
    speed = math.hypot(velocity[0], velocity[2]) if point(velocity) else None
    if previous is None:
        return AircraftTrack(airframe, horizontal, stamp, speed), False

    changed_frame = (airframe and previous.airframe
                     and airframe.casefold() != previous.airframe.casefold())
    jump = False
    if horizontal is not None and previous.position is not None and previous.groundspeed is not None:
        elapsed = max(MIN_TRAVEL_TIME_S, stamp - previous.position_time)
        allowed = max(MIN_DISPLACEMENT_M, previous.groundspeed * elapsed * TRAVEL_MARGIN)
        jump = math.dist(previous.position, horizontal) > allowed
    if changed_frame or jump:
        return AircraftTrack(airframe or previous.airframe, horizontal, stamp, speed), True
    if horizontal is None:
        horizontal, stamp = previous.position, previous.position_time
    return AircraftTrack(airframe or previous.airframe, horizontal, stamp,
                         speed if speed is not None else previous.groundspeed), False
