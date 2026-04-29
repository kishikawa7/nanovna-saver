import os

_qt_logging_rules = os.environ.get("QT_LOGGING_RULES", "")
_svg_draw_rule = "qt.svg.draw=false"
if _svg_draw_rule not in _qt_logging_rules.split(";"):
    os.environ["QT_LOGGING_RULES"] = (
        f"{_qt_logging_rules};{_svg_draw_rule}"
        if _qt_logging_rules
        else _svg_draw_rule
    )

from .NanoVNASaver import NanoVNASaver

__all__ = ["NanoVNASaver"]
