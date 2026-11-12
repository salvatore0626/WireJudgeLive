"""Separate VTOL VR's aircraft actor label from its pilot and airframe."""
import re

AIRFRAMES = ('T-55', 'F-45A', 'F/A-26B', 'AV-42C', 'EF-24G', 'AH-94')
AIRFRAME_NAMES = {name.casefold(): name for name in AIRFRAMES}
ACTOR_LABEL = re.compile(r'([^()]+?)\s+\((.+)\)', re.DOTALL)


def split_actor_label(name, airframe=None):
    """Unwrap only recognized aircraft prefixes, preserving other parentheses."""
    match = ACTOR_LABEL.fullmatch(name.strip()) if isinstance(name, str) else None
    if match is None:
        return name, airframe
    prefix, pilot = match.groups()
    prefix = prefix.strip().casefold()
    known_frame = AIRFRAME_NAMES.get(prefix)
    if pilot.strip() and (known_frame or (isinstance(airframe, str) and prefix == airframe.strip().casefold())):
        return pilot.strip(), airframe or known_frame
    return name, airframe


def pilot_identity(identity, instance_id):
    label = identity.get('actor_name') or identity.get('designation') or str(instance_id)
    return split_actor_label(label, identity.get('player_vehicle'))


def normalize_attempt_identity(attempt):
    pilot, airframe = split_actor_label(attempt['pilot'], attempt.get('airframe'))
    return dict(attempt, pilot=pilot, airframe=airframe)
