"""Flight-grouped aircraft list; native callsigns remain unchanged in telemetry."""
import re
from wirejudge.ui.theme import color as theme_color
from wirejudge.aircraft.pilot_identity import split_actor_label
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem, QHeaderView

NAMES = ('Alpha Bravo Charlie Delta Echo Foxtrot Golf Hotel India Juliet Kilo Lima '
         'Mike November Oscar Papa Quebec Romeo Sierra Tango Uniform Victor Whiskey X-ray Yankee Zulu').split()
ALIASES = {name.casefold():name for name in NAMES}
ALIASES.update({chr(97+i):name for i,name in enumerate(NAMES)})
ALIASES.update(alfa='Alpha',juliett='Juliet',xray='X-ray')


def flight_identity(aircraft):
    identity = aircraft.get('identity') or {}
    designation = str(identity.get('designation') or '').strip()
    match = re.fullmatch(r'([A-Za-z]+(?:-[A-Za-z]+)?)\s*(\d+)\s*-\s*(\d+)',designation)
    actor = identity.get('actor_name') or str(aircraft['instance_id'])
    airframe = identity.get('player_vehicle') or ''
    pilot, airframe = split_actor_label(actor, airframe)
    if match:
        prefix,section,number = match.groups()
        name = ALIASES.get(prefix.casefold(),prefix.title())
        short = f'{chr(65+NAMES.index(name)) if name in NAMES else prefix.upper()}{int(section)}-{int(number)}'
        return name+' Flight', (int(section),int(number),str(pilot).casefold(),str(aircraft['instance_id'])), ' '.join(v for v in (short,str(pilot),airframe) if v)
    return 'Ungrouped', (0,0,str(pilot).casefold(),str(aircraft['instance_id'])), ' '.join(v for v in (designation,str(pilot),airframe) if v)


class AircraftList(QTreeWidget):
    def __init__(self):
        super().__init__()
        self.setHeaderHidden(True)
        self.setExpandsOnDoubleClick(False)
        self.header().setSectionResizeMode(0,QHeaderView.ResizeMode.Stretch)
        self.aircraft_items = {}
        self.flight_items = {}
        self.membership = None

    def clear(self):
        super().clear()
        self.aircraft_items.clear(); self.flight_items.clear(); self.membership=None

    def update_aircraft(self, sample, color_for):
        records=[]
        for a in sample['aircraft']:
            group,order,title=flight_identity(a)
            records.append((group,order,title,a))
        records.sort(key=lambda r:(r[0]=='Ungrouped',r[0].casefold(),r[1]))
        structure=tuple((group,a['instance_id']) for group,_,_,a in records)
        self.blockSignals(True)
        try:
            if structure!=self.membership:
                # Structural work only when membership/order changes, not every sample.
                selected=self.currentItem()
                selected_id=selected.data(0,Qt.ItemDataRole.UserRole) if selected else None
                expanded={name:item.isExpanded() for name,item in self.flight_items.items()}
                self.clear()
                for group,_,_,a in records:
                    if group not in self.flight_items:
                        parent=QTreeWidgetItem(self,[group])
                        parent.setFlags(parent.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                        parent.setExpanded(expanded.get(group,True))
                        font=parent.font(0);font.setBold(True);parent.setFont(0,font)
                        self.flight_items[group]=parent
                    item=QTreeWidgetItem(self.flight_items[group])
                    item.setData(0,Qt.ItemDataRole.UserRole,a['instance_id'])
                    self.aircraft_items[a['instance_id']]=item
                self.membership=structure
                if selected_id in self.aircraft_items:
                    self.setCurrentItem(self.aircraft_items[selected_id])
            for _,_,title,a in records:
                item=self.aircraft_items[a['instance_id']]
                identity=a.get('identity') or {}
                item.setText(0,title)
                item.setToolTip(0,f"{identity.get('designation') or 'Callsign unavailable'}\n{identity.get('actor_name') or title}")
                color=QColor.fromRgbF(*color_for(a,sample))
                item.setForeground(0,color)
                item.parent().setForeground(0,color if item.parent().text(0)!='Ungrouped' else QColor(theme_color('muted')))
        finally:
            self.blockSignals(False)
        return {a['instance_id'] for _,_,_,a in records}

    def select_aircraft(self, instance):
        item=self.aircraft_items.get(instance)
        if item is not None:
            item.parent().setExpanded(True)
            self.setCurrentItem(item)
            self.scrollToItem(item)
