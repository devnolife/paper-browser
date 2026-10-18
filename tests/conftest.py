import pytest


class JamPalsu:
    """Pengganti `time.time`/`time.sleep` untuk `Katalog` dan `Kendali`: waktu hanya maju lewat `tidur` atau diubah
    langsung, jadi tes jeda, TTL, dan masa blokir tidak perlu benar-benar menunggu."""

    def __init__(self, sekarang: float = 1_760_000_000.0):
        self.sekarang = sekarang
        self.jeda: list[float] = []

    def __call__(self) -> float:
        return self.sekarang

    def tidur(self, detik: float) -> None:
        self.jeda.append(detik)
        self.sekarang += detik


@pytest.fixture
def jam() -> JamPalsu:
    return JamPalsu()
