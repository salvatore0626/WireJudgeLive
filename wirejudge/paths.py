"""Bundled assets and platform-specific leaderboard storage locations."""
import os
from pathlib import Path
import sys

from PySide6.QtCore import QStandardPaths

ASSETS_DIR = Path(__file__).resolve().parent / 'data'
LEADERBOARD_FILE = 'leaderboards.sqlite'


def leaderboard_path(configured=None):
    qt_default = Path(QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.AppDataLocation)) / LEADERBOARD_FILE
    if sys.platform != 'win32':
        return Path(configured) if configured else qt_default

    profile_appdata = Path.home() / 'AppData'
    roaming = Path(os.environ.get('APPDATA') or profile_appdata / 'Roaming')
    if {'packages', 'localcache'} <= {part.lower() for part in roaming.parts}:
        roaming = profile_appdata / 'Roaming'
    return roaming / 'Wire Judge Live' / LEADERBOARD_FILE
