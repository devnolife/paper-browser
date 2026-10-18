"""Kendali permintaan ke mesin pencari: cache hasil, jeda antar-permintaan, batas per jam, dan masa istirahat.

Tujuannya bersikap sopan agar IP tidak ditandai sebagai bot (seperti captcha Brave): kueri yang sama tidak
diulang, permintaan diberi jeda, dan mesin yang meminta verifikasi diistirahatkan dulu. Status disimpan di
SQLite supaya berlaku lintas proses (CLI dan server web).
"""

import json
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

LOKASI = Path(__file__).resolve().parent.parent / ".data" / "kendali.sqlite3"
TTL_CACHE = 24 * 3600  # detik hasil pencarian disimpan
JEDA = {"brave": 15, "bing": 5}  # detik minimum antar-permintaan ke mesin yang sama, termasuk antar-halaman
BATAS_PER_JAM = {"brave": 30, "bing": 60}
ISTIRAHAT_CAPTCHA = 30 * 60  # detik mesin diistirahatkan setelah captcha yang tidak diverifikasi

_SKEMA = """
CREATE TABLE IF NOT EXISTS cache (
    mesin TEXT NOT NULL, kueri TEXT NOT NULL, halaman INTEGER NOT NULL,
    dibuat REAL NOT NULL, hasil TEXT NOT NULL,
    PRIMARY KEY (mesin, kueri, halaman)
);
CREATE TABLE IF NOT EXISTS permintaan (mesin TEXT NOT NULL, waktu REAL NOT NULL);
CREATE INDEX IF NOT EXISTS permintaan_mesin_waktu ON permintaan (mesin, waktu);
CREATE TABLE IF NOT EXISTS istirahat (mesin TEXT PRIMARY KEY, sampai REAL NOT NULL, alasan TEXT NOT NULL);
"""


def jam_rapi(waktu: float) -> str:
    return datetime.fromtimestamp(waktu).strftime("%H:%M")


def iso(waktu: float) -> str:
    return datetime.fromtimestamp(waktu).isoformat(timespec="seconds")


class MesinIstirahat(RuntimeError):
    """Mesin sedang diistirahatkan: setelah captcha yang tidak diverifikasi, atau batas per jamnya habis."""

    def __init__(self, mesin: str, sampai: float, alasan: str):
        super().__init__(f"istirahat sampai {jam_rapi(sampai)} ({alasan})")
        self.mesin, self.sampai, self.alasan = mesin, sampai, alasan


def _kunci_kueri(kueri: str) -> str:
    return " ".join(kueri.lower().split())


class Kendali:
    """`jam` dan `tidur` bisa diganti (mis. jam palsu) untuk pengujian."""

    def __init__(self, lokasi: str | Path = LOKASI, jam=time.time, tidur=time.sleep):
        if str(lokasi) != ":memory:":
            Path(lokasi).parent.mkdir(parents=True, exist_ok=True)
        # isolation_level=None: transaksi diatur sendiri lewat BEGIN IMMEDIATE di `_transaksi`.
        self._db = sqlite3.connect(str(lokasi), timeout=30, isolation_level=None)
        self._db.executescript(_SKEMA)
        self._jam, self._tidur = jam, tidur

    def close(self) -> None:
        self._db.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    @contextmanager
    def _transaksi(self):
        # BEGIN IMMEDIATE langsung mengunci penulisan, jadi dua proses tidak bisa memesan giliran yang sama.
        self._db.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self._db.execute("ROLLBACK")
            raise
        self._db.execute("COMMIT")

    def ambil_cache(self, mesin: str, kueri: str, halaman: int, ttl: float = TTL_CACHE) -> list[dict] | None:
        baris = self._db.execute(
            "SELECT dibuat, hasil FROM cache WHERE mesin = ? AND kueri = ? AND halaman = ?",
            (mesin, _kunci_kueri(kueri), halaman),
        ).fetchone()
        if baris and self._jam() - baris[0] < ttl:
            return json.loads(baris[1])
        return None

    def simpan_cache(self, mesin: str, kueri: str, halaman: int, hasil: list[dict]) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO cache VALUES (?, ?, ?, ?, ?)",
            (mesin, _kunci_kueri(kueri), halaman, self._jam(), json.dumps(hasil, ensure_ascii=False)),
        )

    def istirahat_sampai(self, mesin: str) -> tuple[float, str] | None:
        baris = self._db.execute(
            "SELECT sampai, alasan FROM istirahat WHERE mesin = ? AND sampai > ?", (mesin, self._jam())
        ).fetchone()
        return (baris[0], baris[1]) if baris else None

    def istirahatkan(self, mesin: str, alasan: str, detik: float = ISTIRAHAT_CAPTCHA) -> float:
        sampai = self._jam() + detik
        self._db.execute("INSERT OR REPLACE INTO istirahat VALUES (?, ?, ?)", (mesin, sampai, alasan))
        return sampai

    def pulihkan(self, mesin: str) -> None:
        self._db.execute("DELETE FROM istirahat WHERE mesin = ?", (mesin,))

    def _cek(self, mesin: str, sekarang: float) -> list[float]:
        """Angkat MesinIstirahat bila mesin tak boleh dipakai sekarang; hasilnya waktu permintaan dalam sejam terakhir."""
        if istirahat := self.istirahat_sampai(mesin):
            raise MesinIstirahat(mesin, *istirahat)
        waktu = [
            w
            for (w,) in self._db.execute(
                "SELECT waktu FROM permintaan WHERE mesin = ? AND waktu > ? ORDER BY waktu", (mesin, sekarang - 3600)
            )
        ]
        batas = BATAS_PER_JAM.get(mesin)
        if batas and len(waktu) >= batas:
            # Slot berikutnya terbuka saat permintaan ke-`batas` dari belakang keluar dari jendela sejam.
            raise MesinIstirahat(mesin, waktu[-batas] + 3600, f"batas {batas} permintaan per jam tercapai")
        return waktu

    def cek_boleh(self, mesin: str) -> None:
        """Seperti `tunggu_giliran` tanpa mencatat apa pun; dipakai sebelum menyalakan browser."""
        self._cek(mesin, self._jam())

    def catat_permintaan(self, mesin: str) -> None:
        """Catat permintaan tanpa menunggu jeda (mis. memuat ulang halaman captcha untuk diverifikasi pengguna)."""
        with self._transaksi():
            self._db.execute("INSERT INTO permintaan VALUES (?, ?)", (mesin, self._jam()))

    def tunggu_giliran(self, mesin: str, lapor=None) -> None:
        """Tunggu sampai jeda minimum sejak permintaan terakhir ke `mesin` terpenuhi, lalu catat permintaan ini.

        Mengangkat MesinIstirahat bila mesin sedang istirahat atau batas per jamnya habis.
        """
        with self._transaksi():
            sekarang = self._jam()
            waktu = self._cek(mesin, sekarang)
            giliran = max(sekarang, waktu[-1] + JEDA.get(mesin, 0)) if waktu else sekarang
            self._db.execute("INSERT INTO permintaan VALUES (?, ?)", (mesin, giliran))
            self._db.execute("DELETE FROM permintaan WHERE waktu < ?", (sekarang - 2 * 3600,))
        if (sisa := giliran - sekarang) > 0:
            if lapor and sisa >= 1:
                lapor(f"  {mesin}: jeda {sisa:.0f} dtk sebelum permintaan berikutnya...")
            self._tidur(sisa)
