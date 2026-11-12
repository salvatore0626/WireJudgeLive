"""Presentation-only conversions. Capture and DVR always retain native SI values."""
import math
import re

SPEED_FACTORS = {'Knots': 3600 / 1852, 'KPH': 3.6, 'MPH': 3600 / 1609.344,
                 'Bananas per second': 1 / .2}
DISTANCE_FACTORS={'NM':1/1852.,'KM':1/1000.,'Miles':1/1609.344,'Banana Trees':1/7.5}
DISTANCE_SYMBOLS={'NM':'NM','KM':'km','Miles':'mi','Banana Trees':'Banana Trees'}
ALTITUDE_FACTORS = {'Meters': 1., 'Feet': 1 / .3048, 'Bananas': 1 / .2}
SPEED_SYMBOLS = {'Knots': 'kt', 'KPH': 'km/h', 'MPH': 'mph', 'Bananas per second': 'BPS'}
ALTITUDE_SYMBOLS = {'Meters': 'm', 'Feet': 'ft', 'Bananas': 'bananas'}


def convert(value, factor):
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        return None
    return value * factor


class DisplayUnits:
    def __init__(self, speed='Knots', altitude='Feet', distance='NM'):
        if speed in ('BPH','BPS'):speed='Bananas per second'
        self.speed_name = speed if speed in SPEED_FACTORS else 'Knots'
        self.altitude_name = altitude if altitude in ALTITUDE_FACTORS else 'Feet'
        self.distance_name=distance if distance in DISTANCE_FACTORS else 'NM'

    @property
    def speed_symbol(self):
        return SPEED_SYMBOLS[self.speed_name]

    @property
    def altitude_symbol(self):
        return ALTITUDE_SYMBOLS[self.altitude_name]

    @property
    def distance_symbol(self):return DISTANCE_SYMBOLS[self.distance_name]

    def distance(self,metres):return convert(metres,DISTANCE_FACTORS[self.distance_name])

    def distance_nm(self,nm):return self.distance(nm*1852) if nm is not None else None

    def nm_from_distance(self,value):return value/(1852*DISTANCE_FACTORS[self.distance_name])

    def speed(self, metres_per_second):
        return convert(metres_per_second, SPEED_FACTORS[self.speed_name])

    def altitude(self, metres):
        return convert(metres, ALTITUDE_FACTORS[self.altitude_name])

    def speed_vector(self, vector):
        if not isinstance(vector, list) or len(vector) != 3:
            return None
        values = [self.speed(v) for v in vector]
        return values if all(v is not None for v in values) else None


    def procedure_text(self, text):
        """Convert presentation references; chart geometry stays in SI/NM."""
        def replace(match):
            unit=match[4]
            def formatted(raw):
                value=float(raw.replace(',',''))
                if unit=='kt':value=self.speed(value*1852/3600)
                elif unit=='ft':value=self.altitude(value*.3048)
                else:value=self.distance_nm(value)
                return f'{value:,.0f}' if unit in ('kt','ft') else f'{value:,.2f}'.rstrip('0').rstrip('.')
            number=formatted(match[1])
            if match[3] is not None:number+=match[2]+formatted(match[3])
            symbol={'kt':self.speed_symbol,
                    'ft':self.altitude_symbol,'NM':self.distance_symbol,'DME':'DME'}[unit]
            return number+' '+symbol
        return re.sub(r'([\d,]+(?:\.\d+)?)(?:\s*([–→-])\s*([\d,]+(?:\.\d+)?))?\s*(ft|kt|NM|DME)\b',replace,text)
