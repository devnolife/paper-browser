"""Katalog "tempat publish" jurnal: alamat beranda jurnal OJS beserta instalasinya.

Dipakai untuk mencari artikel langsung lewat fitur cari bawaan tiap jurnal, tanpa mesin pencari. Katalog diisi
otomatis dari hasil pencarian (plus jurnal lain di instalasi yang sama), dari impor daftar jurnal DOAJ, dan manual.
Disimpan di SQLite; satu objek `Katalog` hanya dipakai di satu thread.
"""

import csv
import io
import json
import math
import re
import sqlite3
import time
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

from jurnal.padanan import kelompok_konsep, penanda_bidang
from jurnal.relevansi import kata_penting

LOKASI = Path(__file__).resolve().parent.parent / ".data" / "katalog.sqlite3"
URL_CSV_DOAJ = "https://doaj.org/csv"  # dump resmi semua jurnal DOAJ; API pencariannya dibatasi 1.000 rekaman
BLOKIR_DETIK = 24 * 3600
TTL_CACHE = 24 * 3600  # detik halaman hasil pencarian OJS disimpan
GAGAL_MAKS = 3  # jurnal/instalasi yang gagal dihubungi berturut-turut sebanyak ini dilewati
_MAKS_KATA_KUNCI = 60

_SKEMA = """
CREATE TABLE IF NOT EXISTS instalasi (
    dasar TEXT PRIMARY KEY,
    cari_situs INTEGER,
    status TEXT NOT NULL DEFAULT 'baru',
    blokir_sampai REAL NOT NULL DEFAULT 0,
    gagal_berturut INTEGER NOT NULL DEFAULT 0,
    dicek_tetangga REAL NOT NULL DEFAULT 0,
    diperbarui REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS jurnal (
    id INTEGER PRIMARY KEY,
    beranda TEXT NOT NULL UNIQUE,
    dasar TEXT NOT NULL,
    nama TEXT,
    kata_kunci TEXT NOT NULL DEFAULT '',
    token TEXT NOT NULL DEFAULT '',
    sumber TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'baru',
    gagal_berturut INTEGER NOT NULL DEFAULT 0,
    jumlah_temuan INTEGER NOT NULL DEFAULT 0,
    ditambah REAL NOT NULL,
    terakhir_dicari REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS jurnal_dasar ON jurnal (dasar);
CREATE TABLE IF NOT EXISTS riwayat (
    jurnal_id INTEGER NOT NULL,
    kata TEXT NOT NULL,
    temuan INTEGER NOT NULL,
    PRIMARY KEY (jurnal_id, kata)
);
CREATE TABLE IF NOT EXISTS cache_ojs (
    url TEXT PRIMARY KEY,
    dibuat REAL NOT NULL,
    hasil TEXT NOT NULL
);
"""

# OJS dengan index.php: .../index.php/<jurnal>/... ; segmen "index" berarti tingkat situs, bukan jurnal.
_OJS_INDEX = re.compile(r"^(?P<dasar>https?://[^?#]*?/index\.php)/(?P<jurnal>[^/?#]+)", re.I)
# Rute halaman OJS yang bisa menempel di belakang alamat jurnal (URL ringkas tanpa index.php).
_RUTE_OJS = re.compile(
    r"/(?:article|issue|about|announcement|search|login|user|information|index|navigationMenu|libraryFiles"
    r"|catalog|submissions|editorialTeam|gateway|oai|help|statistics)(?:/.*)?$",
    re.I,
)
_BAHASA = re.compile(r"(?<=[^/])/[a-z]{2}(?:_[A-Z]{2})?$")


def beranda_jurnal(url: str) -> str | None:
    """Alamat beranda jurnal dari URL apa pun di situsnya (beranda, artikel, galley, /about, ...).

    None bila URL tidak valid atau menunjuk tingkat situs OJS (`.../index.php/index`).
    """
    url = (url or "").strip()
    if not url:
        return None
    if not re.match(r"https?://", url, re.I):
        url = "https://" + url
    alamat = urlparse(url)
    if not re.fullmatch(r"[\w-]+(?:\.[\w-]+)+(?::\d{1,5})?", alamat.netloc):
        return None  # bukan nama host (mis. teks biasa, "https://", atau host tanpa domain)
    url = f"{alamat.scheme.lower()}://{alamat.netloc.lower()}{alamat.path}".rstrip("/")
    if cocok := _OJS_INDEX.match(url):
        return None if cocok["jurnal"].lower() == "index" else f"{cocok['dasar']}/{cocok['jurnal']}"
    url = _RUTE_OJS.sub("", url)
    if urlparse(url).path.count("/") >= 2:
        url = _BAHASA.sub("", url)  # mis. .../jistr/en -> .../jistr
    return url.rstrip("/")


def dasar_instalasi(beranda: str) -> str:
    """Alamat instalasi OJS tempat jurnal itu berada: `.../index.php`, atau bagian sebelum segmen jurnal."""
    if "/index.php/" in beranda:
        return beranda.split("/index.php/", 1)[0] + "/index.php"
    alamat = urlparse(beranda)
    jalur = alamat.path.rstrip("/")
    induk = jalur.rsplit("/", 1)[0] if jalur.count("/") >= 1 and jalur else ""
    return f"{alamat.scheme}://{alamat.netloc}{induk}"


def _token(nama: str | None, kata_kunci: list[str]) -> str:
    return " ".join(kata_penting(" ".join([nama or "", *kata_kunci])))


class Katalog:
    """`jam` bisa diganti (mis. jam palsu) untuk pengujian."""

    def __init__(self, lokasi: str | Path = LOKASI, jam=time.time):
        if str(lokasi) != ":memory:":
            Path(lokasi).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(lokasi), timeout=30, isolation_level=None)
        if str(lokasi) != ":memory:":
            self._db.execute("PRAGMA journal_mode=WAL")  # baca tetap jalan saat impor sedang menulis
        self._db.executescript(_SKEMA)
        if str(lokasi) != ":memory:":
            self._db.execute("PRAGMA synchronous=NORMAL")  # aman untuk WAL; ratusan penulisan kecil per pencarian
        self._jam = jam

    def close(self) -> None:
        self._db.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def tambah(self, url: str, nama: str | None = None, kata_kunci=(), sumber: str = "manual") -> tuple[str | None, bool]:
        """Tambahkan atau perbarui jurnal. Hasil: (beranda ternormalisasi, apakah jurnal baru)."""
        beranda = beranda_jurnal(url)
        if not beranda:
            return None, False
        dasar = dasar_instalasi(beranda)
        sekarang = self._jam()
        nama = " ".join((nama or "").split()) or None
        kata_baru = [k.strip() for k in kata_kunci if k and k.strip()]
        self._db.execute("INSERT OR IGNORE INTO instalasi (dasar, diperbarui) VALUES (?, ?)", (dasar, sekarang))
        baris = self._db.execute("SELECT id, nama, kata_kunci FROM jurnal WHERE beranda = ?", (beranda,)).fetchone()
        if baris:
            nama_akhir = baris[1] or nama
            kata = list(dict.fromkeys([k for k in baris[2].split(" ; ") if k] + kata_baru))[:_MAKS_KATA_KUNCI]
            self._db.execute(
                "UPDATE jurnal SET nama = ?, kata_kunci = ?, token = ? WHERE id = ?",
                (nama_akhir, " ; ".join(kata), _token(nama_akhir, kata), baris[0]),
            )
            return beranda, False
        kata = list(dict.fromkeys(kata_baru))[:_MAKS_KATA_KUNCI]
        self._db.execute(
            "INSERT INTO jurnal (beranda, dasar, nama, kata_kunci, token, sumber, ditambah) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (beranda, dasar, nama, " ; ".join(kata), _token(nama, kata), sumber, sekarang),
        )
        return beranda, True

    def tambah_banyak(self, data, sumber: str) -> dict:
        """`data`: iterabel (url, nama, kata_kunci). Satu transaksi supaya impor ribuan jurnal tetap cepat."""
        hasil = {"baru": 0, "diperbarui": 0, "dilewati": 0}
        self._db.execute("BEGIN IMMEDIATE")
        try:
            for url, nama, kata_kunci in data:
                beranda, baru = self.tambah(url, nama, kata_kunci, sumber)
                hasil["dilewati" if not beranda else "baru" if baru else "diperbarui"] += 1
        except BaseException:
            self._db.execute("ROLLBACK")
            raise
        self._db.execute("COMMIT")
        return hasil

    def jumlah_jurnal(self) -> int:
        return self._db.execute("SELECT COUNT(*) FROM jurnal").fetchone()[0]

    def jurnal(self, beranda: str) -> dict | None:
        baris = self._db.execute(
            "SELECT beranda, dasar, nama, sumber, status, jumlah_temuan FROM jurnal WHERE beranda = ?", (beranda,)
        ).fetchone()
        return dict(zip(("beranda", "dasar", "nama", "sumber", "status", "jumlah_temuan"), baris)) if baris else None

    def instalasi(self, dasar: str) -> dict | None:
        baris = self._db.execute(
            "SELECT dasar, cari_situs, status, blokir_sampai, dicek_tetangga FROM instalasi WHERE dasar = ?", (dasar,)
        ).fetchone()
        return dict(zip(("dasar", "cari_situs", "status", "blokir_sampai", "dicek_tetangga"), baris)) if baris else None

    def pilih(self, token_kueri: set[str], n: int = 100) -> list[dict]:
        """Jurnal paling sesuai untuk kueri: cocok konsep (dengan padanan), cocok bidang, dan riwayat temuan.

        Jurnal yang bukan OJS, gagal berulang, atau instalasinya sedang diblokir dilewati.
        """
        kelompok = kelompok_konsep(token_kueri)
        penanda = penanda_bidang(token_kueri)
        riwayat = defaultdict(int)
        if token_kueri:
            tanda = ",".join("?" * len(token_kueri))
            for jid, jumlah in self._db.execute(
                f"SELECT jurnal_id, SUM(temuan) FROM riwayat WHERE kata IN ({tanda}) GROUP BY jurnal_id",
                tuple(token_kueri),
            ):
                riwayat[jid] = jumlah
        sekarang, baris = self._jam(), []
        for jid, beranda, dasar, nama, token, temuan, cari_situs in self._db.execute(
            "SELECT j.id, j.beranda, j.dasar, j.nama, j.token, j.jumlah_temuan, i.cari_situs FROM jurnal j "
            "JOIN instalasi i ON i.dasar = j.dasar "
            "WHERE j.status != 'bukan_ojs' AND j.gagal_berturut < ? AND i.status != 'bukan_ojs' "
            "AND i.gagal_berturut < ? AND i.blokir_sampai <= ?",
            (GAGAL_MAKS, GAGAL_MAKS, sekarang),
        ):
            kata = set(token.split())
            baris.append((jid, beranda, dasar, nama, temuan, cari_situs, [bool(k & kata) for k in kelompok], bool(penanda & kata)))
        # Bobot kelangkaan (IDF): konsep yang jarang muncul di katalog (mis. "greedy") lebih menentukan daripada yang umum.
        df = [sum(b[6][i] for b in baris) for i in range(len(kelompok))]
        bobot = [1 + math.log((len(baris) + 1) / (d + 1)) for d in df]
        calon = []
        for jid, beranda, dasar, nama, temuan, cari_situs, cocok, cocok_bidang in baris:
            relevan = 2.0 * sum(w for w, c in zip(bobot, cocok) if c) + (1.5 if cocok_bidang else 0.0) + 2.0 * min(riwayat[jid], 3)
            if relevan > 0:  # jumlah temuan lama hanya penentu urutan, bukan alasan memilih jurnal yang tak berkaitan
                nilai = relevan + 0.3 * math.log1p(temuan)
                calon.append((nilai, temuan, -jid, {"beranda": beranda, "dasar": dasar, "nama": nama, "cari_situs": cari_situs}))
        calon.sort(key=lambda c: c[:3], reverse=True)
        return [c[3] for c in calon[:n]]

    def catat_temuan(self, temuan_per_beranda: dict[str, int], token_kueri: set[str]) -> None:
        """Catat jurnal yang memberi hasil untuk kata-kata kueri ini, supaya pencarian serupa memilihnya lagi."""
        sekarang = self._jam()
        for beranda, jumlah in temuan_per_beranda.items():
            baris = self._db.execute("SELECT id FROM jurnal WHERE beranda = ?", (beranda,)).fetchone()
            if not baris or jumlah <= 0:
                continue
            self._db.execute(
                "UPDATE jurnal SET jumlah_temuan = jumlah_temuan + ?, status = 'ok', gagal_berturut = 0, "
                "terakhir_dicari = ? WHERE id = ?",
                (jumlah, sekarang, baris[0]),
            )
            for kata in token_kueri:
                self._db.execute(
                    "INSERT INTO riwayat VALUES (?, ?, ?) "
                    "ON CONFLICT (jurnal_id, kata) DO UPDATE SET temuan = temuan + excluded.temuan",
                    (baris[0], kata, jumlah),
                )

    def tandai_jurnal(self, beranda: str, status: str) -> None:
        """`ok` (berhasil dicari), `bukan_ojs`, atau `gagal` (gagal berturut-turut 3x membuatnya dilewati)."""
        if status == "gagal":
            self._db.execute("UPDATE jurnal SET gagal_berturut = gagal_berturut + 1 WHERE beranda = ?", (beranda,))
        else:
            self._db.execute(
                "UPDATE jurnal SET status = ?, gagal_berturut = 0, terakhir_dicari = ? WHERE beranda = ?",
                (status, self._jam(), beranda),
            )

    def tandai_instalasi(self, dasar: str, *, status: str | None = None, cari_situs: bool | None = None) -> None:
        """`status`: `ok`, `bukan_ojs`, `diblokir` (dilewati selama BLOKIR_DETIK), atau `gagal` (tak terhubung;
        gagal berturut-turut GAGAL_MAKS kali membuatnya dilewati)."""
        sekarang = self._jam()
        self._db.execute("INSERT OR IGNORE INTO instalasi (dasar, diperbarui) VALUES (?, ?)", (dasar, sekarang))
        if status == "gagal":
            self._db.execute("UPDATE instalasi SET gagal_berturut = gagal_berturut + 1 WHERE dasar = ?", (dasar,))
        elif status:
            blokir = sekarang + BLOKIR_DETIK if status == "diblokir" else 0
            self._db.execute(
                "UPDATE instalasi SET status = ?, blokir_sampai = ?, gagal_berturut = 0, diperbarui = ? WHERE dasar = ?",
                (status, blokir, sekarang, dasar),
            )
        if cari_situs is not None:
            self._db.execute("UPDATE instalasi SET cari_situs = ? WHERE dasar = ?", (int(cari_situs), dasar))

    def ambil_cache(self, url: str, ttl: float = TTL_CACHE) -> list[dict] | None:
        baris = self._db.execute("SELECT dibuat, hasil FROM cache_ojs WHERE url = ?", (url,)).fetchone()
        return json.loads(baris[1]) if baris and self._jam() - baris[0] < ttl else None

    def simpan_cache(self, url: str, hasil: list[dict]) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO cache_ojs VALUES (?, ?, ?)", (url, self._jam(), json.dumps(hasil, ensure_ascii=False))
        )

    def jurnal_di_host(self, host: str) -> list[dict]:
        """Jurnal di katalog yang berada di host ini (untuk mode fokus situs)."""
        host = host.lower().removeprefix("www.")
        hasil = []
        for beranda, dasar, nama, cari_situs in self._db.execute(
            "SELECT j.beranda, j.dasar, j.nama, i.cari_situs FROM jurnal j JOIN instalasi i ON i.dasar = j.dasar"
        ):
            if urlparse(beranda).netloc.lower().removeprefix("www.") == host:
                hasil.append({"beranda": beranda, "dasar": dasar, "nama": nama, "cari_situs": cari_situs})
        return hasil

    def instalasi_untuk_tetangga(self, batas: int) -> list[str]:
        """Instalasi yang terbukti relevan (pernah memberi hasil atau ditambah otomatis/manual) dan belum pernah
        dicek daftar jurnalnya."""
        return [
            dasar
            for (dasar,) in self._db.execute(
                "SELECT i.dasar FROM instalasi i WHERE i.dicek_tetangga = 0 AND i.status != 'bukan_ojs' "
                "AND i.gagal_berturut < ? AND i.blokir_sampai <= ? AND EXISTS (SELECT 1 FROM jurnal j WHERE j.dasar = i.dasar "
                "AND (j.jumlah_temuan > 0 OR j.sumber IN ('otomatis', 'manual'))) "
                "ORDER BY i.diperbarui DESC LIMIT ?",
                (GAGAL_MAKS, self._jam(), batas),
            )
        ]

    def tandai_tetangga_dicek(self, dasar: str) -> None:
        self._db.execute("UPDATE instalasi SET dicek_tetangga = ? WHERE dasar = ?", (self._jam(), dasar))

    def statistik(self) -> dict:
        def hitung(sql, *arg):
            return {k: n for k, n in self._db.execute(sql, arg)}

        return {
            "jurnal": self._db.execute("SELECT COUNT(*) FROM jurnal").fetchone()[0],
            "instalasi": self._db.execute("SELECT COUNT(*) FROM instalasi").fetchone()[0],
            "per_sumber": hitung("SELECT sumber, COUNT(*) FROM jurnal GROUP BY sumber"),
            "per_status": hitung("SELECT status, COUNT(*) FROM jurnal GROUP BY status"),
            "pernah_memberi_hasil": self._db.execute("SELECT COUNT(*) FROM jurnal WHERE jumlah_temuan > 0").fetchone()[0],
            "instalasi_diblokir": self._db.execute(
                "SELECT COUNT(*) FROM instalasi WHERE blokir_sampai > ?", (self._jam(),)
            ).fetchone()[0],
        }

    def daftar(self, cari: str | None = None, batas: int = 50) -> list[dict]:
        sql = "SELECT nama, beranda, sumber, status, jumlah_temuan FROM jurnal"
        arg: tuple = ()
        if cari:
            sql += " WHERE nama LIKE ? OR beranda LIKE ? OR kata_kunci LIKE ?"
            arg = (f"%{cari}%",) * 3
        sql += " ORDER BY jumlah_temuan DESC, ditambah DESC LIMIT ?"
        kolom = ("nama", "beranda", "sumber", "status", "jumlah_temuan")
        return [dict(zip(kolom, baris)) for baris in self._db.execute(sql, (*arg, batas))]


def baca_csv_doaj(teks: str, negara: str = "Indonesia") -> list[tuple[str, str, list[str]]]:
    """Baris (url, nama, kata kunci) jurnal dari dump CSV DOAJ untuk satu negara penerbit."""
    data = []
    for baris in csv.DictReader(io.StringIO(teks)):
        if (baris.get("Country of publisher") or "").strip() != negara:
            continue
        kata = [k.strip() for k in (baris.get("Keywords") or "").split(",")]
        # "Subjects" berformat "Medicine: Pharmacy and materia medica | Education: Theory and practice of education".
        kata += [s.strip() for s in re.split(r"[|:]", baris.get("Subjects") or "")]
        if alternatif := (baris.get("Alternative title") or "").strip():
            kata.append(alternatif)
        data.append(((baris.get("Journal URL") or "").strip(), (baris.get("Journal title") or "").strip(), kata))
    return data


def impor_doaj(katalog: Katalog, lapor=lambda pesan: None, teks_csv: str | None = None) -> dict:
    """Impor jurnal Indonesia dari dump CSV resmi DOAJ (sekali unduh ±25 MB). Aman diulang untuk pembaruan."""
    if teks_csv is None:
        from curl_cffi import requests as curl

        lapor("Mengunduh daftar jurnal DOAJ (±25 MB)...")
        jawaban = curl.get(URL_CSV_DOAJ, impersonate="chrome", timeout=300, allow_redirects=True)
        if jawaban.status_code != 200:
            raise RuntimeError(f"Gagal mengunduh CSV DOAJ: HTTP {jawaban.status_code}")
        teks_csv = jawaban.content.decode("utf-8-sig")
    data = baca_csv_doaj(teks_csv)
    lapor(f"Menyimpan {len(data)} jurnal Indonesia ke katalog...")
    return {"jurnal_indonesia": len(data), **katalog.tambah_banyak(data, "doaj")}
