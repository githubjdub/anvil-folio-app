"""Import shared client helpers with Anvil's package-relative import layout."""
import importlib
from pathlib import Path
import sys
import types


PACKAGE = 'folio_client_for_tests'


def client_module(name):
    if PACKAGE not in sys.modules:
        package = types.ModuleType(PACKAGE)
        package.__path__ = [str(Path(__file__).resolve().parents[1] / 'client_code')]
        sys.modules[PACKAGE] = package
    return importlib.import_module(PACKAGE + '.' + name)
