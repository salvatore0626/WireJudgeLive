"""Distance-weighted landing grades. No Qt, disk access, or wall-clock weighting."""
import copy
import math
import numpy as np
from wirejudge.approach.approach_detection import landing_course_position, measurement, number
from wirejudge.approach.approach_geometry import case1_volume, NM

BINS = 4096
COMPONENTS = ('Position', 'Speed', 'Localizer', 'Glide', 'AoA', 'Wire')
DEFAULT_RULES = {
    'Case 1': {'start_nm': .75, 'end_nm': .08, 'Localizer': 1000., 'Glide': 1000., 'AoA': 1000., 'Wire': 1000.},
    'Case 3': {'start_nm': 2.25, 'end_nm': .08, 'platform_start_nm': 8.5, 'platform_end_nm': 3.25,
               'Position': 1000., 'Speed': 1000., 'Localizer': 1000., 'Glide': 1000., 'AoA': 1000., 'Wire': 1000.},
    'targets': {'glide_target':3.5, 'glide_tolerance': .7, 'loc_tolerance': 2.5, 'aoa_target': 8., 'aoa_tolerance': 5.,
                'platform_alt_ft': 1200., 'platform_alt_tolerance_ft': 300.,
                'speed_outer_kt': 250., 'speed_inner_kt': 200., 'speed_change_nm': 6.25, 'speed_deadzone_nm': 1., 'speed_tolerance_kt': 50.},
    # Fractions permit independent wire maximums for each case.
    'wires': {'Bolter': 0., 'Wire 1': .25, 'Wire 2': .75, 'Wire 3': 1., 'Wire 4': .5}}


def validate_rules(rules):
    r = copy.deepcopy(DEFAULT_RULES)
    for section in r:
        r[section].update(rules.get(section, {}))
        for key,value in r[section].items():
            if not number(value) or value < 0:
                raise ValueError(f'{section}: {key} must be a finite, nonnegative number.')
    for case in ('Case 1','Case 3'):
        c=r[case]
        if not 0 <= c['end_nm'] < c['start_nm'] <= 30:
            raise ValueError(f'{case}: final start must be greater than end, within 0–30 NM.')
    c=r['Case 3']
    if not c['start_nm'] <= c['platform_end_nm'] < c['platform_start_nm'] <= 30:
        raise ValueError('Case 3: platform must precede final, within 0–30 NM.')
    for key in ('glide_tolerance','loc_tolerance','aoa_tolerance','platform_alt_tolerance_ft','speed_tolerance_kt'):
        if r['targets'][key] <= 0:raise ValueError(f'{key} must be greater than zero.')
    if any(v > 1 for v in r['wires'].values()):raise ValueError('Wire awards must be between 0 and 100%.')
    return r


def upgrade_rules(rules):
    """Upgrade former factory values while retaining customized scoring settings."""
    rules = copy.deepcopy(rules)
    targets = rules.get('targets', {})
    if 'speed_deadzone_nm' not in targets:
        previous_window = {
            key: rules.get('Case 3', {}).get(key, default)
            for key, default in (('platform_start_nm', 8.), ('platform_end_nm', 3.))
        }
        old_defaults = {
            'Case 3': {'platform_start_nm': 8., 'platform_end_nm': 3.,
                       'Position': 750., 'Speed': 750., 'AoA': 500.},
            'targets': {'platform_alt_tolerance_ft': 200., 'speed_change_nm': 6.},
        }
        for section, values in old_defaults.items():
            for key, old_value in values.items():
                if rules.get(section, {}).get(key) == old_value:
                    rules[section][key] = DEFAULT_RULES[section][key]
        case = rules.get('Case 3', {})
        far = case.get('platform_start_nm', DEFAULT_RULES['Case 3']['platform_start_nm'])
        near = case.get('platform_end_nm', DEFAULT_RULES['Case 3']['platform_end_nm'])
        if not case.get('start_nm', DEFAULT_RULES['Case 3']['start_nm']) <= near < far:
            rules.setdefault('Case 3', {}).update(previous_window)
    return validate_rules(rules)


def compact_sample(sample, aircraft, ship, glide=3.5, offset=10., volume=None):
    """Small regradable record in native units, including source-clock gap checks."""
    volume=volume or case1_volume(ship,glide,offset)
    m=measurement(aircraft,volume,glide) if volume else None
    if not m:return None
    k=aircraft.get('kinematics') or {};f=k.get('flight_info') or {};source=sample.get('source') or {}
    clock=next((source.get(key) for key in ('mission_time_s','real_time_s') if number(source.get(key))), sample['capture']['monotonic_s'])
    velocity = k.get('velocity_world_mps')
    groundspeed = None
    if isinstance(velocity, (list, tuple)) and len(velocity) == 3 and all(number(v) for v in velocity):
        groundspeed = math.hypot(velocity[0], velocity[2])
    hook = (aircraft.get('systems') or {}).get('hook_point_world_m')
    hook_position = landing_course_position(hook, volume) if (
        isinstance(hook, list) and len(hook) == 3 and all(number(v) for v in hook)) else None
    # GS is stored as an absolute angle so changing target geometry can regrade.
    return [sample['capture']['monotonic_s'],clock,m['aft']/NM,
            math.sqrt(m['aft']**2+m['left']**2+m['height']**2)/NM,
            (m['gs']+glide) if m['gs'] is not None else None,m['loc'],m['aoa'],
            f.get('altitudeASL') if number(f.get('altitudeASL')) else None,
            groundspeed,
            f.get('indicatedAirspeed') if number(f.get('indicatedAirspeed')) else None,
            [coordinate / NM for coordinate in hook_position] if hook_position is not None else None]


def fill_aoa(samples):
    """Interpolate missing AoA between nearby, ordered source timestamps."""
    angles = np.array([sample[6] for sample in samples], dtype=float)
    if np.isfinite(angles).all():
        return angles
    known = np.flatnonzero(np.isfinite(angles))
    clock = np.array([sample[1] for sample in samples])
    for start, end in zip(known, known[1:]):
        if end <= start+1 or clock[end]-clock[start] > 20:
            continue
        interval = clock[start:end+1]
        if np.all(np.diff(interval) > 0):
            angles[start+1:end] = np.interp(clock[start+1:end], interval[[0, -1]], angles[[start, end]])
    return angles


def final_position(sample):
    """Return aft, left and height in NM plus whether the hook supplied them."""
    hook = sample[10] if len(sample) > 10 else None
    if hook is not None:
        return (*hook, True)
    aft = sample[2]
    return (aft,
            aft * math.tan(math.radians(sample[5])) if number(sample[5]) else math.nan,
            aft * math.tan(math.radians(sample[4])) if number(sample[4]) else math.nan,
            False)


def score_attempt(attempt, samples, rules=None):
    r=validate_rules(rules or DEFAULT_RULES);case=attempt.get('case','Case 1');c=r[case];targets=r['targets']
    start=attempt['start_s'];end=attempt.get('end_s');last=end if end is not None else attempt['last_s']
    samples=[p for p in samples if start <= p[0] <= last]
    aoa = fill_aoa(samples)
    final=end is not None
    results={};total_progress=0.;total_earned=0.;total_lost=0.;maximum=0.;missing=False
    final_sources = set()
    # Fill only the first inbound crossing of each station, never earn twice on a go-around.
    legs=[('final',c['start_nm'],c['end_nm'],('Localizer','Glide','AoA'))]
    if case=='Case 3':legs.insert(0,('platform',c['platform_start_nm'],c['platform_end_nm'],('Position','Speed')))
    for name,far,near,components in legs:
        count = BINS if name == 'final' else max(1, round((far-near)*512))
        distances = far-(np.arange(count)+.5)*(far-near)/count
        values = {key:np.full(count,np.nan) for key in components}
        claimed = np.zeros(count,dtype=bool)
        graded = {key:np.ones(count,dtype=bool) for key in components}
        speed_center = targets['speed_change_nm']
        half_band = targets['speed_deadzone_nm']/2
        if 'Speed' in graded:
            graded['Speed'] = (distances <= speed_center-half_band) | (distances >= speed_center+half_band)
        glide_slope = math.tan(math.radians(targets['glide_target']))
        up = math.tan(math.radians(targets['glide_target']+targets['glide_tolerance']))-glide_slope
        down = glide_slope-math.tan(math.radians(targets['glide_target']-targets['glide_tolerance']))
        loc_width = math.tan(math.radians(targets['loc_tolerance']))
        closest=far
        for i,(a,b) in enumerate(zip(samples,samples[1:])):
            da,db=a[2],b[2]
            if not number(da) or not number(db):continue
            closest=min(closest,db)
            elapsed = b[1]-a[1]
            if db>=da or not 0 < elapsed <= 20 or (da-db)*NM/elapsed > 350:
                continue
            first=max(0,int(math.ceil((far-da)/(far-near)*count-.5)))
            stop=min(count,int(math.ceil((far-db)/(far-near)*count-.5)))
            if stop<=first:continue
            indices=np.arange(first,stop);indices=indices[~claimed[indices]]
            claimed[indices]=True
            fraction=(da-distances[indices])/(da-db)
            def interpolated(index):
                return a[index]+fraction*(b[index]-a[index]) if number(a[index]) and number(b[index]) else np.full(len(indices),np.nan)
            def interpolate_offset(index):
                if not (number(a[index]) and number(b[index])):
                    return np.full(len(indices), np.nan)
                left = da*math.tan(math.radians(a[index]))
                right = db*math.tan(math.radians(b[index]))
                return left+fraction*(right-left)

            x = distances[indices]
            if name=='final':
                a_aft, a_left, a_height, a_hook = final_position(a)
                b_aft, b_left, b_height, b_hook = final_position(b)
                hook_aft = a_aft + fraction * (b_aft - a_aft)
                left = a_left + fraction * (b_left - a_left)
                height = a_height + fraction * (b_height - a_height)
                reference_aft = np.where(hook_aft > 0, hook_aft, np.nan)
                if np.any(np.isfinite(reference_aft) & (np.isfinite(left) | np.isfinite(height))):
                    final_sources.update((a_hook, b_hook))
                values['Localizer'][indices] = np.clip(1-abs(left)/(loc_width*reference_aft),0,1)
                height_error = height-glide_slope*reference_aft
                glide_width = np.where(height_error >= 0, up, down)*reference_aft
                values['Glide'][indices] = np.clip(1-abs(height_error)/glide_width,0,1)
                alpha = aoa[i]+fraction*(aoa[i+1]-aoa[i])
                values['AoA'][indices]=np.clip(1-abs(alpha-targets['aoa_target'])/targets['aoa_tolerance'],0,1)
            else:
                alt=interpolated(7)/.3048
                loc_credit = np.clip(1-abs(interpolate_offset(5))/(loc_width*x),0,1)
                position=(loc_credit+
                          np.clip(1-abs(alt-targets['platform_alt_ft'])/targets['platform_alt_tolerance_ft'],0,1))/2
                speed=interpolated(8)*3600/NM
                desired=np.where(distances[indices]>=speed_center,targets['speed_outer_kt'],targets['speed_inner_kt'])
                values['Position'][indices]=position
                values['Speed'][indices]=np.clip(1-abs(speed-desired)/targets['speed_tolerance_kt'],0,1)
        closest=min(closest,min((p[2] for p in samples if number(p[2])),default=far))
        progressed=np.ones(count,dtype=bool) if final else distances>=closest
        for key in components:
            budget = c.get(key,0.)
            active = graded[key]
            judged = progressed & active
            valid = np.isfinite(values[key]) & judged
            denominator = int(active.sum())
            earned = float(np.nansum(values[key][judged]))*budget/denominator if denominator else 0.
            used = int(judged.sum())*budget/denominator if denominator else (budget if final else 0.)
            covered = int(valid.sum())/denominator if denominator else 0.
            results[key]={'earned':earned,'lost':used-earned,'maximum':budget,'coverage':covered,'progress':used/budget if budget else 1.}
            if budget and ((not denominator and final) or int(judged.sum())-int(valid.sum()) > .001*denominator):missing=True
            total_earned+=earned;total_lost+=used-earned;maximum+=budget;total_progress+=used
    budget=c['Wire'];outcome=attempt.get('outcome','Active')
    wire=attempt.get('wire');catch=attempt.get('wire_catch_s')
    if wire and (catch is None or start<=catch<=last):outcome=f'Wire {wire}'
    resolved=outcome in r['wires']
    wire_earned=budget*r['wires'].get(outcome,0.)
    wire_used=budget if resolved or final else 0.
    results['Wire']={'earned':wire_earned,'lost':wire_used-wire_earned,'maximum':budget,'coverage':1. if resolved else 0.,'progress':wire_used/budget if budget else 1.}
    maximum+=budget;total_earned+=wire_earned;total_lost+=wire_used-wire_earned;total_progress+=wire_used
    provisional=missing or bool(attempt.get('missing_bounds')) or (final and not resolved)
    reference = {frozenset({True}): 'Hook', frozenset({False}): 'Aircraft fallback',
                 frozenset({True, False}): 'Hook + aircraft fallback'}.get(frozenset(final_sources))
    return {'final_reference':reference,'components':results,'earned':total_earned,'deductive':maximum-total_lost,
            'maximum':maximum,'progress':total_progress/maximum if maximum else 1.,
            'provisional':provisional,'missing':missing,'complete':final,'wire_pending':not resolved,
            'phase':('Complete' if resolved else outcome) if final else ('Wire resolved' if resolved else
                ('Platform' if case=='Case 3' and samples and samples[-1][2]>c['platform_end_nm'] else 'Final'))}
