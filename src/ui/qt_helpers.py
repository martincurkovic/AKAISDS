from PySide6.QtGui import QFontMetrics


def widen_popup_to_fit_items(combo):
    # ugh, so qcombobox's dropdown popup defaults to the same width as the closed combo box
    # this checksthe length of all of the options and sets that to the width...
    metrics = QFontMetrics(combo.font())
    widest = max(
        (metrics.horizontalAdvance(combo.itemText(i)) for i in range(combo.count())),
        default=0,
    )
    combo.view().setMinimumWidth(widest + 40)  # giving extra padding just in case
