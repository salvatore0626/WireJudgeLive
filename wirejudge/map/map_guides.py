"""Batched map guide geometry and zoom-dependent detail fading (nautical miles)."""
import math
import numpy as np

NM = 1852.0


def levels(base):
    return sorted(set([float(base)] + [x for x in (5., 10., 50., 100.) if x > base]))


def opacity(span_nm, spacing_nm, coarsest=False):
    if coarsest:
        return 1.0
    t = max(0., min(1., (span_nm / spacing_nm - 12.) / 6.))
    return 1. - t * t * (3. - 2. * t)


def weights(span_nm, steps):
    result = [0.] * len(steps)
    for index, step in enumerate(steps):
        amount = opacity(span_nm, step, index == len(steps)-1)
        if amount > 0:
            result[index] = amount
            if index+1 < len(steps):
                result[index+1] = 1.-amount
            break
    return result


def shared(value, coarse):
    return coarse is not None and abs(value / coarse - round(value / coarse)) < 1e-7


def geometry(mode, step, coarse, center, reach, width, span):
    """Only generate visible guide portions; guides remain anchored at world/reference zero."""
    cx, cy = center
    pieces = []
    if mode == 'Grid':
        left, right, bottom, top = cx-reach, cx+reach, cy-reach, cy+reach
        for i in range(math.floor(left/step), math.ceil(right/step)+1):
            x = i * step
            if not shared(x, coarse):
                pieces.append(np.array([[x, bottom, 0], [x, top, 0]], dtype=np.float32))
        for i in range(math.floor(bottom/step), math.ceil(top/step)+1):
            y = i * step
            if not shared(y, coarse):
                pieces.append(np.array([[left, y, 0], [right, y, 0]], dtype=np.float32))
    else:
        distance = math.hypot(cx, cy)
        direction = math.atan2(cy, cx)
        low = max(1, math.floor((distance-reach)/step))
        high = math.ceil((distance+reach)/step)
        tolerance = span/max(width, 1)*.4
        for i in range(low, high+1):
            radius = i * step
            if shared(radius, coarse):
                continue
            if distance < 1e-9 or radius+distance <= reach:
                start, end = 0., math.tau
            else:
                cosine = (radius*radius+distance*distance-reach*reach)/(2*radius*distance)
                if cosine >= 1:
                    continue
                half = math.acos(max(-1., cosine))
                start, end = direction-half, direction+half
            max_angle = math.sqrt(max(8*tolerance/radius, 1e-12))
            count = max(96 if end-start >= math.tau-1e-6 else 8, min(512, math.ceil((end-start)/max_angle)))
            angles = np.linspace(start, end, count+1)
            points = np.column_stack((np.cos(angles)*radius, np.sin(angles)*radius, np.zeros(count+1)))
            segments = np.stack((points[:-1], points[1:]), axis=1).reshape((-1,3))
            pieces.append(segments.astype(np.float32))
    return np.concatenate(pieces) if pieces else np.empty((0,3), dtype=np.float32)
