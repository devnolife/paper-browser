"""Paper Browser — antarmuka web sederhana untuk menguji berapa artikel jurnal yang bisa didapat, sekaligus
mengelola katalog situs jurnal yang dicari langsung.

Jalankan, lalu buka http://127.0.0.1:8000 di browser:
    .venv\\Scripts\\python.exe web_jurnal.py
"""

import argparse
import json
import logging
import sys
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from jurnal.katalog import Katalog, impor_doaj
from jurnal.pencarian import MESIN
from jurnal.terkait import InputTidakValid, cari_terkait

HALAMAN_UTAMA = Path(__file__).resolve().parent / "web" / "index.html"
LOKAL = ("127.0.0.1", "localhost")
MAKS_ISI = 512 * 1024  # byte isi permintaan POST
MAKS_URL_TAMBAH = 500
# Profil browser persisten hanya bisa dipakai satu Chromium sekaligus, jadi pencarian dijalankan bergantian.
_kunci_pencarian = threading.Lock()
_kunci_impor = threading.Lock()  # impor DOAJ tidak perlu menunggu pencarian, tapi tidak boleh berjalan dua kali
# Pesan kemajuan terakhir dari pencarian/impor yang sedang berjalan; dibaca halaman lewat /api/status, mis. supaya
# pengguna tahu ada jendela verifikasi Brave yang menunggu tombol "Verify" ditekan.
_kunci_kemajuan = threading.Lock()
_kemajuan = {"aktif": False, "impor": False, "pesan": ""}


def _lapor(pesan: str) -> None:
    print(pesan, flush=True)
    with _kunci_kemajuan:
        _kemajuan["pesan"] = pesan.strip()


def _angka(nilai: str | None, bawaan: int, minimum: int, maksimum: int) -> int:
    if not nilai:
        return bawaan
    try:
        return min(max(int(nilai), minimum), maksimum)
    except ValueError:
        raise InputTidakValid(f"Bukan angka: {nilai!r}") from None


class Penangan(BaseHTTPRequestHandler):
    def log_message(self, format, *args) -> None:
        if not self.path.startswith("/api/status"):  # dipanggil halaman tiap detik selama pencarian
            super().log_message(format, *args)

    def _lokal(self) -> bool:
        """Hanya layani akses lokal, supaya situs lain tidak bisa memakai server ini lewat DNS rebinding atau
        permintaan lintas situs dari tab lain."""
        asal = self.headers.get("Origin")
        if self.headers.get("Host", "").rsplit(":", 1)[0] not in LOKAL or (asal and urlparse(asal).hostname not in LOKAL):
            self._kirim_json(403, {"galat": "Hanya untuk akses lokal."})
            return False
        return True

    def do_GET(self) -> None:
        if not self._lokal():
            return
        alamat = urlparse(self.path)
        p = {kunci: nilai[0] for kunci, nilai in parse_qs(alamat.query).items()}
        if alamat.path == "/":
            self._kirim(200, HALAMAN_UTAMA.read_bytes(), "text/html; charset=utf-8")
        elif alamat.path == "/api/cari":
            self._cari(p)
        elif alamat.path == "/api/status":
            with _kunci_kemajuan:
                self._kirim_json(200, dict(_kemajuan))
        elif alamat.path == "/api/katalog":
            self._katalog(p)
        else:
            self._kirim_json(404, {"galat": "Halaman tidak ditemukan."})

    def do_POST(self) -> None:
        # Halaman situs lain tidak bisa mengirim JSON dengan header khusus tanpa izin CORS (yang tidak pernah
        # diberikan server ini), jadi kedua syarat ini mencegah perubahan katalog dari luar halaman ini.
        if not self._lokal():
            return
        jenis = self.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if jenis != "application/json" or self.headers.get("X-Paper-Browser") != "1":
            self._kirim_json(403, {"galat": "Permintaan ditolak."})
            return
        try:
            panjang = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            panjang = -1
        if not 0 <= panjang <= MAKS_ISI:
            if 0 < panjang <= 8 * MAKS_ISI:
                self.rfile.read(panjang)  # dibaca dulu supaya klien menerima jawaban 413, bukan koneksi terputus
            self.close_connection = True
            self._kirim_json(413, {"galat": "Isi permintaan terlalu besar."})
            return
        try:
            data = json.loads(self.rfile.read(panjang) or b"{}")
        except ValueError:
            data = None
        if not isinstance(data, dict):
            self._kirim_json(400, {"galat": "Isi permintaan harus objek JSON."})
            return
        alamat = urlparse(self.path).path
        if alamat == "/api/katalog/tambah":
            self._katalog_tambah(data)
        elif alamat == "/api/katalog/impor-doaj":
            self._impor_doaj()
        else:
            self._kirim_json(404, {"galat": "Halaman tidak ditemukan."})

    def _katalog(self, p: dict[str, str]) -> None:
        try:
            batas = _angka(p.get("batas"), 20, 1, 200)
        except InputTidakValid as e:
            self._kirim_json(400, {"galat": str(e)})
            return
        with Katalog() as k:
            data = {"statistik": k.statistik(), "jurnal": k.daftar(p.get("cari", "").strip() or None, batas)}
        self._kirim_json(200, {**data, "impor_berjalan": _kunci_impor.locked()})

    def _katalog_tambah(self, data: dict) -> None:
        teks, urls = data.get("teks"), data.get("urls")
        if isinstance(teks, str):
            urls = [b.strip() for b in teks.splitlines() if b.strip() and not b.lstrip().startswith("#")]
        if not isinstance(urls, list) or not urls or not all(isinstance(u, str) for u in urls):
            self._kirim_json(400, {"galat": "Isi minimal satu URL jurnal."})
            return
        if len(urls) > MAKS_URL_TAMBAH:
            self._kirim_json(400, {"galat": f"Maksimal {MAKS_URL_TAMBAH} URL sekali tambah."})
            return
        hasil = []
        with Katalog() as k:
            for url in urls:
                beranda, baru = k.tambah(url[:1000], sumber="manual")
                hasil.append({"url": url, "beranda": beranda, "status": "tidak_valid" if not beranda else "baru" if baru else "sudah_ada"})
            statistik = k.statistik()
        self._kirim_json(200, {"hasil": hasil, "statistik": statistik})

    def _impor_doaj(self) -> None:
        if not _kunci_impor.acquire(blocking=False):
            self._kirim_json(409, {"galat": "Impor DOAJ sedang berjalan."})
            return
        with _kunci_kemajuan:
            _kemajuan.update(impor=True, pesan="Memulai impor DOAJ...")
        try:
            with Katalog() as k:
                hasil = impor_doaj(k, lapor=_lapor)
                statistik = k.statistik()
        except Exception as e:
            traceback.print_exc()
            self._kirim_json(502, {"galat": f"Gagal mengimpor DOAJ: {type(e).__name__}: {e}"})
            return
        finally:
            with _kunci_kemajuan:
                _kemajuan.update(impor=False)
            _kunci_impor.release()
        self._kirim_json(200, {"impor": hasil, "statistik": statistik})

    def _cari(self, p: dict[str, str]) -> None:
        masukan = p.get("q", "").strip()
        adalah_url = masukan.lower().startswith(("http://", "https://"))
        try:
            parameter = {
                "kueri": None if adalah_url else masukan,
                "dari_url": masukan if adalah_url else None,
                "situs": p.get("situs", "").strip() or None,
                "mesin": [m.strip() for m in p.get("mesin", ",".join(MESIN)).split(",") if m.strip()],
                "halaman": _angka(p.get("halaman"), 1, 1, 3),
                "maks": _angka(p.get("maks"), 30, 1, 60),
                "mode": p.get("mode", "katalog_dulu"),
                "jurnal_maks": _angka(p.get("jurnal_maks"), 100, 1, 1000),
                "tampilkan": p.get("tampilkan") == "1",
                "browser_cadangan": p.get("cadangan", "1") != "0",
                "segarkan": p.get("segarkan") == "1",
                "lapor": _lapor,
            }
            with _kunci_pencarian:
                with _kunci_kemajuan:
                    _kemajuan.update(aktif=True, pesan="Memulai pencarian...")
                try:
                    laporan = cari_terkait(**parameter)
                finally:
                    with _kunci_kemajuan:
                        _kemajuan.update(aktif=False, pesan="")
        except InputTidakValid as e:
            self._kirim_json(400, {"galat": str(e)})
        except Exception as e:
            traceback.print_exc()
            self._kirim_json(500, {"galat": f"{type(e).__name__}: {e}"})
        else:
            self._kirim_json(200, laporan)

    def _kirim_json(self, status: int, data: dict) -> None:
        self._kirim(status, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _kirim(self, status: int, isi: bytes, jenis: str) -> None:
        try:
            self.send_response(status)
            self.send_header("Content-Type", jenis)
            self.send_header("Content-Length", str(len(isi)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(isi)
        except ConnectionError:  # tab ditutup atau dimuat ulang sebelum pencarian selesai
            pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Antarmuka web untuk menguji pencarian jurnal terkait.")
    parser.add_argument("--port", type=int, default=8000, help="port lokal (default: 8000)")
    parser.add_argument("-v", "--verbose", action="store_true", help="tampilkan log Scrapling")
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")
    logging.getLogger("scrapling").setLevel(logging.INFO if args.verbose else logging.CRITICAL)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Penangan)
    print(f"Buka http://127.0.0.1:{args.port} di browser (Ctrl+C untuk berhenti)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
