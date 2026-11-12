"""Session-stable flight colors shared by every view."""
from wirejudge.aircraft.aircraft_list import flight_identity
from wirejudge.ui import theme

FLIGHT_PALETTE=theme.BASE_FLIGHT_PALETTE


class FlightColors:
    def __init__(self):
        self.colors = {}
        self.memberships = {}
        self.indices = {}
        self.theme_name = theme.CURRENT_THEME
        self.overrides = {}

    def set_overrides(self, values):
        self.overrides = dict(values)
        self.colors.clear()

    @staticmethod
    def key(source, aircraft):
        session=(source.get('process_id'),source.get('epoch'))
        flight=flight_identity(aircraft)[0]
        return session+(('aircraft',aircraft.get('instance_id')) if flight=='Ungrouped' else ('flight',flight))

    def get(self, source, aircraft):
        track=(source.get('process_id'),source.get('epoch'),aircraft.get('instance_id'))
        key=self.key(source,aircraft)
        if key[2]=='flight':
            self.memberships[track]=key
        else:
            key=self.memberships.get(track,key)
        if self.theme_name != theme.CURRENT_THEME:
            self.colors.clear()
            self.theme_name = theme.CURRENT_THEME
        if key not in self.indices:
            self.indices[key] = len(self.indices)
        if key not in self.colors:
            self.colors[key]=self._color(key,self.indices[key])
        return self.colors[key]

    def preview(self, slot):
        palette=theme.flight_palette()
        value=self.overrides.get(str(slot%6),palette[slot%6])
        rgb=tuple(int(value.lstrip('#')[i:i+2],16)/255 for i in (0,2,4))
        return (*rgb,1.)

    def _color(self,key,index):
        return self.preview(index%6)
