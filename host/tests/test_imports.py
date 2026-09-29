import importlib

import pytest


@pytest.mark.parametrize("module", ["deskdeck.app", "deskdeck.wiretest"])
def test_entry_points_import(module):
    importlib.import_module(module)
