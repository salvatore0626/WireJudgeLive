"""Responsive settings cards; existing controls survive column changes."""
from PySide6.QtWidgets import QWidget,QHBoxLayout,QVBoxLayout,QSizePolicy


class SettingsColumns(QWidget):
    def __init__(self,cards):
        super().__init__()
        self.cards=cards
        self.column_count=0
        self.columns=[]
        self.row=QHBoxLayout(self)
        self.row.setContentsMargins(0,0,0,0)
        self.row.setSpacing(18)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Preferred)
        self.reflow(2)

    def resizeEvent(self,event):
        super().resizeEvent(event)
        count=3 if self.width()>=1320 else (2 if self.width()>=850 else 1)
        if count!=self.column_count:self.reflow(count)

    def reflow(self,count):
        # Reparent rather than rebuild: edits, focus and signals remain intact.
        for card in self.cards:card.setParent(self)
        while self.row.count():
            item=self.row.takeAt(0)
            if item.widget():item.widget().deleteLater()
        self.column_count=count
        self.columns=[]
        heights=[0]*count
        for _ in range(count):
            column=QWidget(self);layout=QVBoxLayout(column)
            layout.setContentsMargins(0,0,0,0);layout.setSpacing(8)
            self.row.addWidget(column,1)
            self.columns.append(layout)
        for card in self.cards:
            index=min(range(count),key=lambda i:heights[i])
            self.columns[index].addWidget(card)
            heights[index]+=card.sizeHint().height()+8
            card.show()
        for layout in self.columns:layout.addStretch()
        self.updateGeometry()
