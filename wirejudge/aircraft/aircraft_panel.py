"""Resizable aircraft sidebar that moves into a normal secondary window."""
from PySide6.QtCore import Qt, QTimer, QRect
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QDockWidget, QWidget, QVBoxLayout, QHBoxLayout,
    QSplitter, QToolButton, QPushButton, QMainWindow)


class AircraftWindow(QMainWindow):
    def __init__(self, panel):
        super().__init__(None,Qt.WindowType.Window)
        self.panel=panel
        self.setWindowTitle('Aircraft — Wire Judge Live')
        self.setMinimumSize(245,220)
        self.setStyleSheet(panel.owner.styleSheet())

    def closeEvent(self,event):
        if self.panel.owner.closing:
            super().closeEvent(event)
        else:
            event.ignore()
            self.panel.dock_panel()


class AircraftPanel(QDockWidget):
    def __init__(self, host, owner, tree, inspector):
        super().__init__('Aircraft',host)
        self.host,self.owner,self.store=host,owner,owner.settings.store
        self.setObjectName('aircraft_panel')
        self.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea)
        self.setFeatures(QDockWidget.DockWidgetFeature.NoDockWidgetFeatures)
        self.setMinimumWidth(245)
        self.expanded_sizes=[320,320]
        self.docked_width=310
        self.floating_geometry=None
        self.floating_window=None
        self.detached=False
        title=QWidget()
        row=QHBoxLayout(title);row.setContentsMargins(6,3,6,3)
        row.addStretch(1)
        self.pop_button=QPushButton('Pop out')
        self.pop_button.setToolTip('Open in a normal window for another monitor. Close that window to return the panel here.')
        self.pop_button.clicked.connect(self.toggle_floating)
        row.addWidget(self.pop_button)
        self.setTitleBarWidget(title)
        body=QWidget();layout=QVBoxLayout(body);layout.setContentsMargins(0,0,6,0)
        self.splitter=QSplitter(Qt.Orientation.Vertical)
        self.splitter.setHandleWidth(7)
        self.splitter.setChildrenCollapsible(False)
        self.list_section=QWidget()
        section=QVBoxLayout(self.list_section);section.setContentsMargins(0,0,0,0)
        self.collapse_button=QToolButton()
        self.collapse_button.setText('AIRCRAFT')
        self.collapse_button.setCheckable(True);self.collapse_button.setChecked(True)
        self.collapse_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.collapse_button.setArrowType(Qt.ArrowType.DownArrow)
        self.collapse_button.setToolTip('Collapse or expand the aircraft list')
        self.collapse_button.toggled.connect(self.set_list_expanded)
        section.addWidget(self.collapse_button)
        self.list_body=QWidget()
        inner=QVBoxLayout(self.list_body);inner.setContentsMargins(0,0,0,0)
        tree.setMinimumHeight(60)
        inner.addWidget(tree,1)
        section.addWidget(self.list_body,1)
        inspector.setMinimumHeight(100)
        self.splitter.addWidget(self.list_section);self.splitter.addWidget(inspector)
        self.splitter.setStretchFactor(0,1);self.splitter.setStretchFactor(1,1)
        layout.addWidget(self.splitter)
        self.setWidget(body)
        host.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea,self)
        QTimer.singleShot(0,self.restore_layout)

    def set_list_expanded(self, expanded):
        if not expanded:
            sizes=self.splitter.sizes()
            if all(sizes):self.expanded_sizes=sizes
        self.list_body.setVisible(expanded)
        self.collapse_button.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        self.list_section.setMaximumHeight(16777215 if expanded else self.collapse_button.sizeHint().height())
        if expanded:
            self.splitter.setSizes(self.expanded_sizes)

    def toggle_floating(self):
        if self.detached:
            self.dock_panel()
        else:
            self.docked_width=self.width()
            if self.floating_window is None:
                self.floating_window=AircraftWindow(self)
                self.floating_window.resize(max(320,self.width()),max(450,self.height()))
            body=self.widget()
            body.setParent(None)
            self.setWidget(None)
            self.floating_window.setCentralWidget(body)
            self.detached=True
            super().hide()
            if self.floating_geometry:self.floating_window.restoreGeometry(self.floating_geometry)
            self.ensure_on_screen()
            self.floating_window.show()
            body.show()
            self.floating_window.raise_()

    def dock_panel(self):
        if not self.detached:return
        window=self.floating_window
        self.floating_geometry=window.saveGeometry()
        body=window.takeCentralWidget()
        self.setWidget(body)
        self.detached=False
        window.hide()
        self.show();body.show()
        self.host.resizeDocks([self],[self.docked_width],Qt.Orientation.Horizontal)

    def shutdown(self):
        if self.floating_window is not None:self.floating_window.hide()
        self.hide()

    def ensure_on_screen(self):
        window=self.floating_window if self.detached else self
        rect=window.frameGeometry()
        title=QRect(rect.x(),rect.y(),rect.width(),40)
        screens=QGuiApplication.screens()
        if any(title.intersected(s.availableGeometry()).width()>=80 and
               title.intersected(s.availableGeometry()).height()>=20 for s in screens):
            return
        screen=self.owner.screen() or QGuiApplication.primaryScreen()
        if screen is None:return
        available=screen.availableGeometry()
        window.resize(min(max(window.width(),300),available.width()),min(max(window.height(),400),available.height()))
        rect=window.frameGeometry();rect.moveCenter(available.center());window.move(rect.topLeft())

    def restore_layout(self):
        try:
            sizes=[max(1,int(v)) for v in self.store.value('aircraft_panel/sizes',[320,320])]
            if len(sizes)==2:self.expanded_sizes=sizes
            self.docked_width=max(245,int(self.store.value('aircraft_panel/width',310)))
        except (TypeError,ValueError):
            pass
        self.splitter.setSizes(self.expanded_sizes)
        expanded=self.store.value('aircraft_panel/list_expanded',True,type=bool)
        self.collapse_button.setChecked(expanded)
        self.host.resizeDocks([self],[self.docked_width],Qt.Orientation.Horizontal)
        self.floating_geometry=self.store.value('aircraft_panel/geometry')
        if self.store.value('aircraft_panel/floating',False,type=bool):
            self.toggle_floating()

    def save_layout(self):
        if self.collapse_button.isChecked():
            sizes=self.splitter.sizes()
            if all(sizes):self.expanded_sizes=sizes
        floating=self.detached
        if floating:self.floating_geometry=self.floating_window.saveGeometry()
        else:self.docked_width=self.width()
        values={'sizes':self.expanded_sizes,'width':self.docked_width,
                'list_expanded':self.collapse_button.isChecked(),'floating':floating}
        if self.floating_geometry:values['geometry']=self.floating_geometry
        for key,value in values.items():self.store.setValue('aircraft_panel/'+key,value)

    def closeEvent(self,event):
        if self.owner.closing:
            super().closeEvent(event)
        else:
            event.ignore()
            self.dock_panel()
            self.show()
