import importlib
import pkgutil

import pytest

import jurnal


@pytest.mark.parametrize("modul", sorted(m.name for m in pkgutil.iter_modules(jurnal.__path__)))
def test_modul_bisa_diimpor(modul):
    """Menangkap nama yang hilang dari dependensi yang dipin, mis. setelah memperbarui scrapling atau curl_cffi."""
    importlib.import_module(f"jurnal.{modul}")
