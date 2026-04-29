#  NanoVNASaver
#
#  A python program to view and export Touchstone data from a NanoVNA
#  Copyright (C) 2026 NanoVNA-Saver Authors
#
#  This program is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.

from PySide6 import QtWidgets


SAFE_FILE_DIALOG_OPTIONS = (
    QtWidgets.QFileDialog.Option.DontUseNativeDialog
    | QtWidgets.QFileDialog.Option.DontUseCustomDirectoryIcons
)


def safe_file_dialog(parent=None) -> QtWidgets.QFileDialog:
    dialog = QtWidgets.QFileDialog(parent)
    dialog.setOptions(dialog.options() | SAFE_FILE_DIALOG_OPTIONS)
    return dialog


def _safe_options(options=None):
    if options is None:
        return SAFE_FILE_DIALOG_OPTIONS
    return options | SAFE_FILE_DIALOG_OPTIONS


def get_open_file_name(*args, **kwargs):
    kwargs["options"] = _safe_options(kwargs.get("options"))
    return QtWidgets.QFileDialog.getOpenFileName(*args, **kwargs)


def get_save_file_name(*args, **kwargs):
    kwargs["options"] = _safe_options(kwargs.get("options"))
    return QtWidgets.QFileDialog.getSaveFileName(*args, **kwargs)


def get_existing_directory(*args, **kwargs):
    kwargs["options"] = _safe_options(kwargs.get("options"))
    return QtWidgets.QFileDialog.getExistingDirectory(*args, **kwargs)
