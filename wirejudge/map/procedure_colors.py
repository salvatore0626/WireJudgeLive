"""Shared configurable palette for procedure geometry and labels."""
import numpy as np
from PySide6.QtGui import QColor

DEFAULT_COLORS={'glide':'#20EF55','approach':'#00BC82','marshal':'#AD76FF','missed':'#FF9B32'}

THEME_COLORS={
    'Banana':dict(glide='#95E66D',approach='#C2DF58',marshal='#F4D861',missed='#FFD493'),
    'Chartroom':dict(glide='#27633F',approach='#1C635D',marshal='#644381',missed='#8B431C'),
    'Phosphor':dict(glide='#80E89D',approach='#40CAA0',marshal='#9DB6E9',missed='#E3C272'),
    'Sandstorm':dict(glide='#124C30',approach='#16463F',marshal='#46265F',missed='#633015'),
    'Ultraviolet':dict(glide='#73DC9D',approach='#5CCDC3',marshal='#CE9EF9',missed='#FFAD77'),
    'Ember Contrast':dict(glide='#59D57D',approach='#2BB58A',marshal='#B176E8',missed='#F3A44C'),
    'Porcelain':dict(glide='#238B45',approach='#16806A',marshal='#7954B3',missed='#B66615'),
    'Abyss':dict(glide='#55D98B',approach='#25AF87',marshal='#B184F5',missed='#FFAD66'),
}


def defaults(name):
    return THEME_COLORS.get(name, DEFAULT_COLORS)


def is_preset(role, value):
    return value.lower() in {p[role].lower() for p in [DEFAULT_COLORS,*THEME_COLORS.values()]}


def rgba(value,alpha=1.):
    color=QColor(value)
    if not color.isValid():color=QColor('#ffffff')
    return color.redF(),color.greenF(),color.blueF(),alpha


def vertex_colors(value, opacity, fade):
    colors = np.empty((len(fade), 4), dtype=np.float32)
    colors[:] = rgba(value, opacity)
    colors[:, 3] *= fade
    return colors
