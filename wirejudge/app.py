"""Wire Judge Live: live receiver and DVR capture."""
import sys
import faulthandler
from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QSurfaceFormat
from PySide6.QtWidgets import QApplication
from wirejudge.paths import ASSETS_DIR


def main(version):
    if sys.stderr is not None:
        faulthandler.enable()
    if sys.platform == 'win32':
        from ctypes import c_wchar_p, windll

        windll.shell32.SetCurrentProcessExplicitAppUserModelID(c_wchar_p('STRAYDOG.WireJudgeLive'))
    fmt = QSurfaceFormat()
    fmt.setDepthBufferSize(24)
    fmt.setSwapInterval(1)
    fmt.setSamples(0)
    QSurfaceFormat.setDefaultFormat(fmt)
    # Pyqtgraph caches shader programs across widgets, including the preview in
    # its own dialog. The programs must belong to the same OpenGL share group.
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    app = QApplication(sys.argv)
    app.setApplicationName('Wire Judge Live')
    app.setOrganizationName('STRAYDOG')
    app.setApplicationVersion(version)
    app.setWindowIcon(QIcon(str(ASSETS_DIR / 'app_icon.png')))
    from wirejudge.ui.main_window import MainWindow
    window = MainWindow()
    window.setWindowTitle(f'{app.applicationName()} v{version} - By: STRAYDOG')
    window.show()
    return app.exec()


if __name__ == '__main__':
    from app import APP_VERSION

    sys.exit(main(APP_VERSION))
