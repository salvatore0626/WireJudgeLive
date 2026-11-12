"""Telemetry-time ground label state, retained in DVR and safe across seeks."""
from wirejudge.aircraft.flight_instruments import source_clock


class LandedLabelTracker:
    """Capture-time annotation survives DVR seeks and short RAM windows."""
    def __init__(self):self.session=None;self.last=None;self.since={}
    def update(self,sample):
        session=sample['capture']['connection_id'];now=source_clock(sample)
        if session!=self.session or (self.last is not None and now<self.last):self.since.clear()
        self.session,self.last=session,now
        present=set()
        for aircraft in sample.get('aircraft',[]):
            key=aircraft['instance_id'];present.add(key)
            landed=((aircraft.get('kinematics') or {}).get('flight_info') or {}).get('isLanded')
            if landed is False:self.since.pop(key,None)
            elif landed is True:self.since.setdefault(key,now)
            elapsed=now-self.since[key] if key in self.since else None
            aircraft['display']={'landed_for_s':elapsed,'resting':elapsed is not None and elapsed>7.}
        self.since={key:value for key,value in self.since.items() if key in present}


def settled_aircraft(sample,history):
    result=set();legacy=[]
    for aircraft in sample.get('aircraft',[]):
        landed=((aircraft.get('kinematics') or {}).get('flight_info') or {}).get('isLanded')
        if landed is False:continue
        flag=(aircraft.get('display') or {}).get('resting')
        if flag is True:result.add(aircraft['instance_id'])
        elif flag is None and landed is True:legacy.append(aircraft['instance_id'])
    if legacy:
        # Older recordings have no capture-time annotation. Reconstruct only from
        # available past samples, never wall time or future DVR frames.
        now=source_clock(sample);capture_time=sample['capture']['monotonic_s'];session=sample['capture']['connection_id']
        for key in legacy:
            since=now
            for frame in reversed(history):
                if frame['capture']['connection_id']!=session or frame['capture']['monotonic_s']>capture_time:continue
                a=next((a for a in frame.get('aircraft',[]) if a['instance_id']==key),None)
                if a is None:break
                landed=((a.get('kinematics') or {}).get('flight_info') or {}).get('isLanded')
                if landed is False:break
                if landed is True:since=min(since,source_clock(frame))
            if now-since>7:result.add(key)
    return result
