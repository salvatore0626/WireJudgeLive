"""Selectable high-contrast UI palettes shared by widgets and painters."""
import re

PALETTE={'map':'#10151B','panel':'#192129','border':'#2B3843',
         'text':'#E1E8ED','muted':'#93A4B1','accent':'#70B7C9','warning':'#FF626A'}

STYLE_TEMPLATE = '''
QWidget { background: #192129; color: #E1E8ED; font-family: "Segoe UI", "Inter", sans-serif; font-size: 13px; }
QMainWindow, QTabWidget::pane { background: #10151B; }
QFrame#topbar { background: #192129; border-bottom: 1px solid #2B3843; }
QLabel#brandLive { font-size:20px; font-weight:700; color:#FF626A; }
QFrame#mapControls { border:1px solid #2B3843; border-radius:6px; }
QLabel#missionClock { color:#70B7C9; background:#10151B; border:1px solid #2B3843; border-radius:6px; padding:5px 10px; font-family:"Consolas", "Menlo", monospace; font-size:18px; font-weight:700; }
QLabel#brand { font-size: 20px; font-weight: 700; letter-spacing: 0px; color: #E1E8ED; }
QLabel#aircraftTitle { color: #70B7C9; font-size: 16px; font-weight: 700; }
QLabel#eyebrow { color: #70B7C9; font-size: 11px; font-weight: 600; }
QLabel#muted { color: #93A4B1; }
QLabel#emptyTitle { font-size: 18px; color: #E1E8ED; font-weight: 600; }
QLabel#status { color: #D79A5B; padding: 7px 14px; background: #30291F; border-radius: 5px; }
QPushButton, QToolButton, QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit { background: #202B34; border: 1px solid #2B3843; border-radius: 5px; padding: 7px 10px; }
QPushButton:hover, QToolButton:hover { background: #293A45; border-color: #70B7C9; }
QPushButton:checked, QToolButton:checked { color: #A9D6E1; background: #243C47; border-color: #70B7C9; }
QPushButton:disabled, QToolButton:disabled, QComboBox:disabled, QDoubleSpinBox:disabled { color: #62717D; border-color: #26313A; background: #182027; }
QTabBar::tab { padding: 14px 24px; color: #93A4B1; border-bottom: 2px solid transparent; }
QTabBar::tab:selected { color: #A9D6E1; border-bottom: 2px solid #70B7C9; background: #202D36; }
QTabBar::tab:disabled { color: #62717D; }
QTabWidget::pane { border: none; border-top: 1px solid #2B3843; }
QGroupBox { border: 1px solid #2B3843; border-radius: 7px; margin-top: 18px; padding: 18px 12px 12px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 6px; color: #93A4B1; }
QTreeWidget, QTableWidget { background: #10151B; border: 1px solid #2B3843; border-radius: 4px; alternate-background-color: #192129; selection-background-color: #293E4C; selection-color: #E1E8ED; }
QHeaderView::section { background: #202B34; color: #93A4B1; border: none; border-bottom: 1px solid #2B3843; padding: 9px; }
QSplitter::handle { background: #2B3843; width: 3px; }
QStatusBar { color: #93A4B1; border-top: 1px solid #2B3843; }
QSlider::groove:horizontal { height: 5px; background: #2B3843; border-radius: 2px; }
QSlider::handle:horizontal { background: #70B7C9; width: 12px; margin: -4px 0; border-radius: 5px; }
QScrollArea { border: none; }
QScrollBar:vertical { background: #10151B; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background: #3A4A57; min-height: 24px; border-radius: 4px; }
QScrollBar::handle:vertical:hover { background: #70B7C9; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }
QCheckBox { spacing: 8px; }
QMenu { background: #192129; border: 1px solid #2B3843; padding: 4px; }
QMenu::item { padding: 6px 20px; }
QMenu::item:selected { background: #293E4C; color: #E1E8ED; }
QToolTip { background: #202B34; color: #E1E8ED; border: 1px solid #70B7C9; padding: 6px; }
'''


# Immutable preset definitions; PALETTE is updated in place for painters.
THEMES = {
    'Midnight Cyan': dict(map='#070E17', panel='#102233', border='#28516A',
        text='#F1F7FC', muted='#A5BFD0', accent='#36D9F3', warning='#FF535F',
        control='#153248', hover='#20485E', selected='#17465B'),
    'Deep Navy': dict(map='#060A14', panel='#142344', border='#354F80',
        text='#F2F5FF', muted='#AABDE2', accent='#5D99FF', warning='#FF535F',
        control='#1B3158', hover='#294779', selected='#254A80'),
    'Carbon Amber': dict(map='#0B0B0D', panel='#202025', border='#505057',
        text='#FAF6EE', muted='#C0BAB0', accent='#FFBD59', warning='#FF535F',
        control='#303035', hover='#454039', selected='#51422C'),
}
THEMES.update({
    'Chartroom': dict(map='#EBE0C5', panel='#F8EED8', border='#877656',
        text='#182638', muted='#443E32', accent='#7F2C26', warning='#A42E41',
        control='#E7D9B9', hover='#DBCAA6', selected='#EBCDAE',
        slow='#176542', on='#7A5400', selection='#26364A', needle='#26364A',
        inactive='#A69B82', disabled_text='#655F52', disabled_border='#C2B69B',
        disabled_bg='#E4DBC6', guide='#605441', guide_alpha=.60),
    'Phosphor': dict(map='#05100B', panel='#102218', border='#315641',
        text='#DCF6E5', muted='#A3C5AE', accent='#71F5AF', warning='#FF6972',
        control='#19362A', hover='#234936', selected='#27543B', guide='#4C775D'),
    'Sandstorm': dict(map='#9E8767', panel='#342A21', border='#75614B',
        text='#FFF0D8', muted='#DEC8A9', accent='#72E0D0', warning='#FF717A',
        control='#493A2C', hover='#5C4A38', selected='#28504A',
        guide='#46382D', guide_alpha=.6, selection='#231B15'),
    'Ultraviolet': dict(map='#130B21', panel='#251537', border='#58416E',
        text='#F5E9FF', muted='#C9B3DC', accent='#C4A0FF', warning='#FF6A85',
        control='#35204A', hover='#4C3267', selected='#50316D', guide='#705682'),
    'Banana': dict(map='#121B0C', panel='#233319', border='#627A39',
        text='#FFF7CC', muted='#D1DE91', accent='#FFE351', warning='#FF626C',
        control='#334724', hover='#475B2D', selected='#555522', guide='#819A48',
        slow='#94E46F', on='#FFD350'),
    'Ember Contrast': dict(map='#0D0D11', panel='#242127', border='#51464D',
        text='#FFF6EB', muted='#CBBDB5', accent='#FFC064', warning='#FF5968',
        control='#332D32', hover='#494047', selected='#55432D'),
    'Porcelain': dict(map='#C8D0D7', panel='#DEE3E7', border='#A7B3BF',
        text='#172635', muted='#526575', accent='#006F87', warning='#C72E45',
        control='#CFD7DE', hover='#BFCBD5', selected='#B8D3DD',
        slow='#18834C', on='#916900', selection='#172635', needle='#172635',
        inactive='#A7B3BF', disabled_text='#687783', disabled_border='#BAC4CE', disabled_bg='#D3DAE1',
        guide='#435465', guide_alpha=.65),
    'Abyss': dict(map='#020407', panel='#0A111A', border='#253547',
        text='#EDF7FF', muted='#A0B5C9', accent='#6EE7FA', warning='#FF5968',
        control='#111E2B', hover='#1A2C3C', selected='#153540'),
})
# Recessed displays, carrier strokes, and graph traces are independently tunable.
DETAILS = {
    'Chartroom': ('#EFE4CA', '#314259', '#155B81', '#704485', '#246341', '#874515'),
    'Phosphor': ('#09190F', '#B4EAD0', '#79D6E3', '#C3B0F4', '#91F4AA', '#EAD387'),
    'Sandstorm': ('#281F18', '#291F17', '#76DEEE', '#D8B3F8', '#9EE2AC', '#FFD089'),
    'Ultraviolet': ('#1C102A', '#E1CAF9', '#8ED9FD', '#DEA8F5', '#83E6BB', '#FFC581'),
    'Banana': ('#182511', '#EEE5A0', '#C9E969', '#F4E3A0', '#82DB68', '#FFCA45'),
    'Midnight Cyan': ('#0B1927', '#D0ECF7', '#64D8F3', '#C8A4FC', '#59DDB2', '#FFC27E'),
    'Deep Navy': ('#0D1730', '#D9E4FF', '#79AEFF', '#D5A7FB', '#6BE6BC', '#F7BC70'),
    'Carbon Amber': ('#16191F', '#E7DDD1', '#72C9EF', '#C7A5ED', '#74D5AC', '#FFC175'),
    'Ember Contrast': ('#1A161D', '#F3DED4', '#76D4EF', '#D4A6E9', '#72DCAF', '#FFB472'),
    'Porcelain': ('#E9EEF2', '#344A5B', '#176E9C', '#7048AA', '#147554', '#93520D'),
    'Abyss': ('#070D15', '#CBEBF4', '#70D9FA', '#BCA4FF', '#6DE0AF', '#FFD086'),
}
for name, preset in THEMES.items():
    roles = ('recessed', 'carrier', 'chart_glide', 'chart_localizer', 'chart_aoa', 'chart_ias')
    preset.update(zip(roles, DETAILS[name]))
    light = name in ('Porcelain', 'Chartroom')
    preset.update(
        raised=preset['control'], divider=preset['border'], focus=preset['accent'],
        chart_background=preset['recessed'] if name == 'Sandstorm' else preset['map'],
        chart_limit=preset['muted'], chart_gate=preset['muted'], chart_cursor=preset['accent'],
        radar=preset['accent'],
        sky='#326684' if light else '#174B70',
        ground='#765238' if light else '#63452E', horizon='#F1F7FC',
        gear_up=preset['muted'], warning_dim='#EDBBC4' if light else '#672C32',
        score_idle=preset['muted'], score_blue='#165DAC' if light else '#63B3FF',
        score_green='#156748' if light else '#62DF9A',
        score_orange='#894600' if light else '#FFB452',
        score_red='#A42336' if light else '#FF666B',
        spark_trail='#9B5100' if light else '#FFAE38',
        spark_head='#875400' if light else '#FFE59C',
        wire_1='#B72B41' if light else '#FF595E', wire_2='#8B6900' if light else '#FFD84D',
        wire_3='#16774E' if light else '#62DF9A', wire_4='#A65008' if light else '#FF9D45',
    )
    for role, value in dict(slow='#20EF55', on='#FFE36D', selection='#FFFFFF',
            needle='#FFFFFF', inactive='#35434F', disabled_text='#62717D',
            disabled_border='#26313A', disabled_bg='#182027', guide='#4D5C69', guide_alpha=.42).items():
        preset.setdefault(role, value)

# Carbon variants share the graphite surfaces and instrument colors.
for name, accent, hover, selected in (
    ('Carbon Cobalt', '#72A8FF', '#343D50', '#263D60'),
    ('Carbon Blaze', '#FF954F', '#4B3832', '#573721'),
    ('Carbon Barbie', '#FF86C8', '#4C3847', '#552D47'),
):
    THEMES[name] = dict(THEMES['Carbon Amber'], accent=accent, radar=accent, hover=hover,
                        selected=selected, focus=accent, chart_cursor=accent)

# Approved six-color palette, shared by all dark themes.
BASE_FLIGHT_PALETTE = ('#1883F0', '#ED6F18', '#963BE8', '#109E66', '#E2297B', '#E6B517')
# Match the hues on Porcelain, darkening only where needed for contrast.
FLIGHT_PALETTES = {
    'Porcelain': ('#1574D4', '#BB5713', '#963BE8', '#0D8455', '#D82776', '#8E700E'),
    'Chartroom': ('#155DA8', '#9C4213', '#7032A5', '#0F6546', '#9E205C', '#755800'),
    'Sandstorm': ('#064579', '#6D2300', '#50216E', '#064D39', '#76113E', '#FFF398'),
}


CUSTOM_THEMES = {}
BLANK_COLORS = dict(map='#17191C', panel='#24272A', control='#333639',
                    text='#F3F3F3', muted='#BCC0C4', accent='#D1D6DA',
                    border='#62686E', carrier='#DFE3E6')
BASIC_COLORS = dict(map='Map background', panel='Panel background', control='Control surface',
                    text='Text', muted='Muted text', accent='Accent', border='Borders and grid',
                    carrier='Carrier')
ADVANCED_COLORS = dict(chart_glide='Glideslope trace', chart_localizer='Localizer trace',
                       chart_aoa='AoA trace', chart_ias='Airspeed trace',
                       sky='Attitude sky', ground='Attitude ground', needle='Gauge needle',
                       radar='Radar sweep',
                       procedure_glide='Glideslope markings', procedure_approach='Approach markings',
                       procedure_marshal='Marshal markings', procedure_missed='Missed approach markings',
                       **{f'flight_{i}': f'Flight color {i+1}' for i in range(6)})


def valid_custom_theme(name, definition):
    if (not isinstance(name, str) or not name.strip() or len(name) > 60
            or name.casefold() in {n.casefold() for n in (*THEMES, 'Blank')}
            or not isinstance(definition, dict)):
        return False
    base = definition.get('base')
    colors = definition.get('colors')
    return (isinstance(base, str) and (base in THEMES or base == 'Blank')
            and definition.get('font', 'Theme default') in (*FONT_OPTIONS, 'Theme default')
            and isinstance(colors, dict)
            and all(role in BASIC_COLORS or role in ADVANCED_COLORS or role == 'guide_alpha' for role in colors)
            and all(type(value) in (int, float) and 0 <= value <= 1 if role == 'guide_alpha'
                    else isinstance(value, str) and re.fullmatch(r'#[0-9a-fA-F]{6}', value)
                    for role, value in colors.items()))


def blend(background, foreground, weight):
    channels = [round(int(background[i:i+2], 16) * (1-weight) + int(foreground[i:i+2], 16) * weight)
                for i in (1, 3, 5)]
    return '#' + ''.join(f'{channel:02x}' for channel in channels)


def custom_palette(base, overrides):
    palette = dict(THEMES.get(base, THEMES[DEFAULT_THEME]))
    colors = {**BLANK_COLORS, **overrides} if base == 'Blank' else overrides
    palette.update({role: value for role, value in colors.items() if role in palette})
    if 'map' in colors:
        palette['chart_background'] = palette['map']
    if 'map' in colors or 'panel' in colors:
        palette['recessed'] = blend(palette['map'], palette['panel'], .35)
    if 'control' in colors or 'accent' in colors:
        palette.update(raised=palette['control'],
                       hover=blend(palette['control'], palette['accent'], .14),
                       selected=blend(palette['control'], palette['accent'], .28))
    if 'accent' in colors:
        palette.update(focus=palette['accent'], chart_cursor=palette['accent'])
    if 'border' in colors:
        palette.update(divider=palette['border'], guide=palette['border'])
    if 'muted' in colors:
        palette.update(chart_limit=palette['muted'], chart_gate=palette['muted'],
                       gear_up=palette['muted'], score_idle=palette['muted'])
    return palette


def theme_names():
    return (*THEMES, *CUSTOM_THEMES)


def flight_palette(name=None):
    name = name or CURRENT_THEME
    custom = CUSTOM_THEMES.get(name, {})
    base = custom.get('base', name)
    defaults = FLIGHT_PALETTES.get(base, BASE_FLIGHT_PALETTE)
    return tuple(custom.get('colors', {}).get(f'flight_{i}', value) for i, value in enumerate(defaults))


DEFAULT_THEME = 'Phosphor'
LEGACY = {'#10151B':'map', '#192129':'panel', '#2B3843':'border',
          '#E1E8ED':'text', '#93A4B1':'muted', '#70B7C9':'accent',
          '#A9D6E1':'accent', '#FF626A':'warning', '#202B34':'control',
          '#293A45':'hover', '#243C47':'selected', '#202D36':'selected',
          '#293E4C':'selected', '#3A4A57':'border', '#627785':'muted', '#20EF55':'slow', '#FFE36D':'on', '#E4CD7A':'on',
          '#35434F':'inactive', '#62717D':'disabled_text', '#26313A':'disabled_border', '#182027':'disabled_bg'}
CURRENT_THEME = DEFAULT_THEME


def set_theme(name):
    global CURRENT_THEME
    CURRENT_THEME = name if name in theme_names() else DEFAULT_THEME
    PALETTE.clear()
    custom = CUSTOM_THEMES.get(CURRENT_THEME)
    PALETTE.update(custom_palette(custom['base'], custom['colors']) if custom else THEMES[CURRENT_THEME])


def color(value, palette=None):
    palette = PALETTE if palette is None else palette
    return palette.get(LEGACY.get(value.upper(), value), value)


FONT_OPTIONS = {
    'System Default': '"Segoe UI", "Inter", sans-serif',
    'Sans Serif': '"Arial", "Liberation Sans", sans-serif',
    'Serif': '"Georgia", "Liberation Serif", serif',
    'Monospace': '"Consolas", "Menlo", "Liberation Mono", monospace',
    'Military · Flight Deck': '"Bahnschrift", "DIN Alternate", "DejaVu Sans Condensed", sans-serif',
    'Military · Radar Terminal': '"OCR A Extended", "Lucida Console", "DejaVu Sans Mono", monospace',
}
THEME_FONTS = {
    'Chartroom': 'Military · Radar Terminal', 'Porcelain': 'Sans Serif',
    'Phosphor': 'Military · Radar Terminal', 'Abyss': 'Monospace',
    'Midnight Cyan': 'Military · Radar Terminal', 'Deep Navy': 'Military · Flight Deck',
    'Sandstorm': 'Military · Flight Deck', 'Carbon Amber': 'Military · Flight Deck',
    'Carbon Cobalt': 'Military · Flight Deck', 'Carbon Blaze': 'Military · Flight Deck',
    'Carbon Barbie': 'Sans Serif', 'Ember Contrast': 'Military · Flight Deck',
    'Ultraviolet': 'Monospace', 'Banana': 'Sans Serif',
}
CURRENT_FONT = 'System Default'


def default_font(name):
    custom = CUSTOM_THEMES.get(name, {})
    font = custom.get('font', 'Theme default')
    return THEME_FONTS.get(custom.get('base', name), 'System Default') if font == 'Theme default' else font



def set_font(name):
    global CURRENT_FONT
    CURRENT_FONT = name if name in FONT_OPTIONS else 'System Default'


def stylesheet(palette=None, font=None):
    palette = PALETTE if palette is None else palette
    font = CURRENT_FONT if font is None else font
    replay_highlight = blend(palette['accent'], palette['text'], .4)
    result = STYLE_TEMPLATE.replace('"Segoe UI", "Inter", sans-serif', FONT_OPTIONS[font])
    for old, role in LEGACY.items():
        result = result.replace(old, palette[role])
    return result + f'''
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QPushButton:focus, QToolButton:focus {{ border-color: {palette['focus']}; }}
QScrollBar:horizontal {{ background: {palette['recessed']}; height: 10px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {palette['border']}; min-width: 24px; border-radius: 4px; }}
QScrollBar::handle:horizontal:hover {{ background: {palette['accent']}; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: none; }}
QHeaderView::section {{ background: {palette['raised']}; border-bottom-color: {palette['divider']}; }}
QTreeWidget#replayList {{ selection-color: {replay_highlight}; }}
QTreeWidget#replayList::item:selected {{ color: {replay_highlight}; background: {palette['selected']}; font-weight: 600; }}
'''


def refresh_widgets(window, previous):
    """Recolor existing plots and cached dial faces once per theme change."""
    import pyqtgraph as pg
    from PySide6.QtGui import QColor, QPen, QBrush
    from PySide6.QtWidgets import QWidget
    from wirejudge.aircraft.gauge_card import GaugeCard
    old_roles = {previous[key].lower(): key for key in dict.fromkeys(LEGACY.values()) if key in previous}
    def converted(value):
        role = old_roles.get(QColor(value).name().lower())
        return QColor(PALETTE[role]) if role else None
    window.setStyleSheet(stylesheet())
    if hasattr(window,'mode'):window.mode.apply_theme()
    window.replays_button.apply_theme()
    if hasattr(window,'refresh_control_icons'):window.refresh_control_icons()
    if hasattr(window,'map_controls'):window.map_controls.adjustSize()
    floating = window.aircraft_panel.floating_window
    if floating:
        floating.setStyleSheet(stylesheet())
    for overlay in window.map.text_overlays:
        overlay.refresh_label_font()
    window.map.setBackgroundColor(PALETTE['map'])
    window.map.apply_carrier_theme()
    window.map.guide_alphas.clear()
    window.map.refresh_grid()
    window.map.update_compass()
    window.map.update()
    if hasattr(window,'approach_displays'):window.approach_displays.apply_theme()
    if hasattr(window,'stack_tool'):window.stack_tool.apply_theme()
    if hasattr(window,'roster_tool'):window.roster_tool.apply_theme()
    if hasattr(window,'deck_crew_tool'):window.deck_crew_tool.apply_theme()
    if hasattr(window,'conditions_tool'):window.conditions_tool.apply_theme()
    if hasattr(window,'scoring_tool'):window.scoring_tool.apply_theme()
    if hasattr(window,'lso_hints'):window.lso_hints.apply_theme()
    plots = window.findChildren(pg.PlotWidget)
    if floating:
        plots += floating.findChildren(pg.PlotWidget)
    for plot in plots:
        plot.setBackground(PALETTE['chart_background'])
        title = plot.plotItem.titleLabel
        if title.text:
            plot.setTitle(title.text, color=PALETTE['muted'])
        for axis in ('left', 'bottom', 'right', 'top'):
            plot.getAxis(axis).setPen(PALETTE['border'])
            plot.getAxis(axis).setTextPen(PALETTE['muted'])
        for item in plot.plotItem.items:
            opts = getattr(item, 'opts', {})
            for key, setter in (('pen', 'setPen'), ('symbolPen', 'setSymbolPen'),
                                ('symbolBrush', 'setSymbolBrush'), ('brush', 'setBrush')):
                value = opts.get(key)
                if value is None or not hasattr(item, setter):
                    continue
                if isinstance(value, (QPen, QBrush)):
                    new_color = converted(value.color())
                    if new_color:
                        new_value = type(value)(value); new_value.setColor(new_color)
                        getattr(item, setter)(new_value)
            if isinstance(item, pg.InfiniteLine):
                new_color = converted(item.pen.color())
                if new_color:
                    pen = QPen(item.pen); pen.setColor(new_color); item.setPen(pen)
    if hasattr(window, 'approach_view'):
        window.approach_view.apply_theme()
    # Detached instruments are not children of the main window.
    widgets = window.findChildren(QWidget)
    if floating:
        widgets += floating.findChildren(QWidget)
    for widget in widgets:
        if isinstance(widget, GaugeCard):
            widget.dial_face = None; widget.dial_face_key = None
        widget.update()


set_theme(DEFAULT_THEME)
STYLE = stylesheet()
