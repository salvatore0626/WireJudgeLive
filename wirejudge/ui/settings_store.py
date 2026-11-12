from PySide6.QtCore import QSettings
from pathlib import Path
import json
import sys
from wirejudge.ui.theme import DEFAULT_THEME

GAUGE_DISPLAY_DEFAULTS = {
    'altitude': 'Analog',
    'ias': 'Analog',
    'vertical_speed': 'Digital',
    'heading_track': 'Analog',
}

DEFAULTS = {'flight_palette_overrides':'{}', 'flight_color_overrides':'{}', 'theme': DEFAULT_THEME, 'custom_themes': '{}', 'theme_fonts': '{}', 'font': 'System Default', 'host': '127.0.0.1', 'port': 27850, 'buffer_s': 30,
            'far_hz': 2, 'mid_hz': 5, 'near_hz': 10, 'region_hz': 10,
            'recording_folder': str(Path.home() / 'Documents' / 'WireJudgeLive Recordings'),
            'trails': True, 'verticals': True, 'vectors': True, 'grid': True,
            'carrier_flag': True, 'carrier_cvn': 78, 'deck_crew_enabled': True, 'deck_crew_count': 20,
            'aircraft_label_fields':'["flight","name","ias","altitude","dme","aoa","rtb","warnings"]',
            'icon_scale_2d':100.0,'icon_scale_3d':100.0,
            'glide_color':'#20EF55','approach_color':'#00BC82','marshal_color':'#AD76FF','missed_color':'#FF9B32',
            'guide_style': 'Rings', 'guide_spacing_nm': 5.0, 'procedure_line_width': 2,
            'speed_unit': 'Knots', 'altitude_unit': 'Feet', 'distance_unit':'NM',
            'approach_case': 'Case 1', 'case1_glide_deg': 3.5, 'case1_runway_offset_deg': 10.0, 'velocity_vector_seconds': 3.0,
            **{f'{gauge}_display': mode for gauge, mode in GAUGE_DISPLAY_DEFAULTS.items()}}


class Settings:
    def __init__(self):
        self.store = open_preferences()
        if not self.store.contains('heading_track_display') and self.store.value(
                'instruments/digital_heading_track', False, type=bool):
            self.store.setValue('heading_track_display', 'Digital')
        for field in ('dme', 'aoa', 'rtb'):
            key = f'labels/{field}_initialized'
            if self.store.value(key, False, type=bool):
                continue
            try:fields=json.loads(self.get('aircraft_label_fields'))
            except (ValueError,TypeError):fields=json.loads(DEFAULTS['aircraft_label_fields'])
            if not isinstance(fields,list):fields=json.loads(DEFAULTS['aircraft_label_fields'])
            if field not in fields:fields.append(field)
            self.store.setValue('aircraft_label_fields',json.dumps(fields))
            self.store.setValue(key,True)
        # Update prior shipped defaults while retaining deliberate custom colors.
        old={'glide_color':('#61dd84','#6dc48d'),'approach_color':('#43bb75','#43886c'),'marshal_color':('#a77ae0','#927ab8'),'missed_color':('#ff9f43','#d79a5b')}
        for key,value in old.items():
            if str(self.store.value(key,'')).lower() in value:self.store.setValue(key,DEFAULTS[key])


    def get(self, key):
        default = DEFAULTS[key]
        return self.store.value(key, default, type=type(default))

    def save(self, values):
        for k, v in values.items():
            self.store.setValue(k, v)
        self.store.sync()


def open_preferences():
    if sys.platform == 'win32':
        return QSettings(r'HKEY_CURRENT_USER\Software\WireJudgeLive', QSettings.Format.NativeFormat)
    return QSettings('STRAYDOG', 'WireJudgeLive')
