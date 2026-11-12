"""Aircraft display menu that stays open while checkable options are edited."""
from PySide6.QtCore import Qt,QPoint
from PySide6.QtWidgets import QMenu,QToolButton
from wirejudge.ui.theme import PALETTE


class AircraftMenu(QMenu):
    def __init__(self,*args):
        super().__init__(*args)
        self.setAttribute(Qt.WidgetAttribute.WA_NoMouseReplay)
        self.palette_key=None;self.apply_theme()
    def apply_theme(self):
        key=(PALETTE['disabled_text'],PALETTE['border'])
        if key==self.palette_key:return
        self.palette_key=key
        self.setStyleSheet(f'QMenu::item:disabled {{color:{key[0]};}} QMenu::separator {{height:3px;background:{key[1]};margin:5px 8px;}}')
    def mouseReleaseEvent(self,event):
        action=self.actionAt(event.position().toPoint())
        if event.button()==Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            if action and action.isEnabled() and action.isCheckable():action.trigger()
            event.accept();return
        super().mouseReleaseEvent(event)
    def keyPressEvent(self,event):
        action=self.activeAction()
        if event.key() in (Qt.Key.Key_Space,Qt.Key.Key_Return,Qt.Key.Key_Enter) and action and action.isEnabled() and action.isCheckable():
            action.trigger();event.accept();return
        super().keyPressEvent(event)


class AircraftMenuButton(QToolButton):
    def setMenu(self,menu):
        super().setMenu(menu)
        menu.aboutToHide.connect(lambda:self.setDown(False))
    def toggle_menu(self):
        menu=self.menu()
        if menu.isVisible():menu.hide()
        else:self.setDown(True);menu.popup(self.mapToGlobal(QPoint(0,self.height())))
    def mousePressEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton:self.toggle_menu();event.accept()
        else:super().mousePressEvent(event)
    def mouseReleaseEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton:event.accept()
        else:super().mouseReleaseEvent(event)
    def keyPressEvent(self,event):
        if event.key() in (Qt.Key.Key_Space,Qt.Key.Key_Return,Qt.Key.Key_Enter,Qt.Key.Key_Down):self.toggle_menu();event.accept()
        else:super().keyPressEvent(event)
    def keyReleaseEvent(self,event):
        if event.key() in (Qt.Key.Key_Space,Qt.Key.Key_Return,Qt.Key.Key_Enter,Qt.Key.Key_Down):event.accept()
        else:super().keyReleaseEvent(event)
