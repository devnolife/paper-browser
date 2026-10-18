"""Alur pencarian judul jurnal terkait: katalog situs jurnal dan/atau mesin pencari -> kandidat -> verifikasi
artikel -> skor -> saring -> cek PDF -> catat jurnal ke katalog.

Dipakai bersama oleh CLI (`cari_jurnal.py`) dan antarmuka web (`web_jurnal.py`).
"""

import re
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

from scrapling.fetchers import Fetcher, StealthySession
from scrapling.parser import Selector

from jurnal.katalog import Katalog
from jurnal.kendali import Kendali, MesinIstirahat, iso, jam_rapi
from jurnal.metadata import adalah_artikel, ambil_metadata
from jurnal.ojs import cari_di_katalog, temukan_tetangga
from jurnal.pdf import analisis_tombol_pdf, cek_pdf, cek_pdf_di_halaman, hasil_cek
from jurnal.pencarian import MESIN, CaptchaError, HasilCari, cari_bing, cari_brave, normalisasi_url
from jurnal.relevansi import berkaitan, kata_penting, skor

_AKAR = Path(__file__).resolve().parent.parent
# Profil browser tetap (cookie, termasuk bukti verifikasi) supaya tidak dianggap pengunjung baru tiap kali.
PROFIL_BROWSER = _AKAR / ".profil-browser"
# Tiap mesin pencari punya profil sendiri: jendela verifikasinya bisa dibuka tanpa bentrok dengan sesi lain, dan
# cookie hasil verifikasi pengguna tersimpan untuk pencarian berikutnya.
PROFIL_MESIN = {"bing": _AKAR / ".profil-bing", "brave": _AKAR / ".profil-brave"}
NAMA_MESIN = {"bing": "Bing", "brave": "Brave"}
TUNGGU_VERIFIKASI = 120  # detik bagi pengguna untuk menyelesaikan verifikasi sendiri di jendela browser
# Kandidat (terutama dari katalog) tersebar di banyak situs yang sebagian lambat (5-15 dtk), jadi dibuka cukup paralel
# supaya satu situs lambat tidak menahan yang lain; tetapi situs jurnal kecil menjawab HTTP 500 bila satu host diserbu
# (mis. mode fokus situs: semua kandidat di satu host), jadi per host dibatasi.
PARALEL_BUKA = 16
PER_HOST = 2
# Kegagalan HTTP yang biasanya hilang bila halaman dibuka browser sungguhan: tantangan JavaScript non-interaktif
# (mis. Cloudflare "Just a moment...") dan rantai sertifikat SSL tak lengkap yang dilengkapi sendiri oleh Chromium.
BISA_DICOBA_BROWSER = {"HTTP 403", "HTTP 429", "HTTP 503", "CertificateVerifyError"}
# Tantangan Cloudflare pertama kali bisa makan >15 dtk; sesudahnya cookie tersimpan di profil dan jauh lebih cepat.
_TIMEOUT_BROWSER = 20_000
_META_ARTIKEL = 'meta[name="citation_title"]'
# Captcha interaktif (DataDome, reCAPTCHA, hCaptcha) tidak lolos sendiri dan sengaja tidak dikerjakan, jadi tak perlu ditunggu.
_CAPTCHA_INTERAKTIF = 'iframe[src*="captcha-delivery.com"], iframe[src*="recaptcha"], iframe[src*="hcaptcha.com"]'


class InputTidakValid(ValueError):
    """Parameter pencarian tidak valid atau artikel sumber tidak bisa dibaca."""


class _SesiMalas:
    """Sesi browser yang baru dinyalakan saat pertama kali dipakai, supaya pencarian yang seluruhnya terlayani
    cache dan HTTP tidak perlu meluncurkan Chromium."""

    def __init__(self, **opsi):
        self._opsi, self._sesi = opsi, None

    def fetch(self, url: str, **kwargs):
        if self._sesi is None:
            self._sesi = StealthySession(**self._opsi)
            self._sesi.start()
        return self._sesi.fetch(url, **kwargs)

    def close(self) -> None:
        if self._sesi is not None:
            self._sesi.close()
            self._sesi = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def _baca(page, lewat: str) -> dict:
    return {**ambil_metadata(page), "analisis_pdf": analisis_tombol_pdf(page), "dibuka_lewat": lewat}


def _peta_per_host(fungsi, butir: list, url_dari=lambda b: b) -> list:
    """Seperti `pool.map`: paralel antar situs, tetapi paling banyak PER_HOST permintaan bersamaan ke satu situs."""
    batas = {h: threading.Semaphore(PER_HOST) for h in {urlparse(url_dari(b)).netloc for b in butir}}

    def jalankan(b):
        with batas[urlparse(url_dari(b)).netloc]:
            return fungsi(b)

    with ThreadPoolExecutor(max_workers=PARALEL_BUKA) as pool:
        return list(pool.map(jalankan, butir))


def buka(url: str) -> tuple[dict | None, str | None]:
    """Buka kandidat lewat HTTP. Hasil: (data jika halaman artikel, alasan jika gagal dibuka)."""
    for percobaan in (1, 2):
        try:
            page = Fetcher.get(url, impersonate="chrome", stealthy_headers=True, timeout=20, retries=2)
        except Exception as e:
            return None, type(e).__name__
        if page.status not in (500, 502, 504) or percobaan == 2:
            break
        time.sleep(1.5)  # situs kecil yang sedang sibuk biasanya pulih sebentar kemudian
    if page.status != 200:
        return None, f"HTTP {page.status}"
    return (_baca(page, "http") if adalah_artikel(page) else None), None


def _tunggu_artikel(page) -> bool:
    """Tunggu halaman artikel muncul di browser; tantangan JavaScript (mis. "Just a moment...") berganti sendiri
    ke artikel setelah lolos. Berhenti lebih awal bila yang muncul captcha interaktif."""
    batas = time.monotonic() + _TIMEOUT_BROWSER / 1000
    while time.monotonic() < batas:
        try:
            if page.query_selector(_META_ARTIKEL):
                return True
            if page.query_selector(_CAPTCHA_INTERAKTIF):
                return False
        except Exception:
            pass  # konteks halaman berganti saat tantangan berpindah ke artikel; cek lagi
        page.wait_for_timeout(300)
    return False


def buka_lewat_browser(sesi, url: str) -> tuple[dict | None, str | None]:
    """Seperti `buka`, tapi lewat browser; tautan PDF-nya sekalian dicek dari halaman yang sama.

    Captcha interaktif tidak dikerjakan, jadi halaman seperti itu tetap gagal.
    """
    cek = {}

    def aksi(page):
        if not _tunggu_artikel(page):
            return
        if url_pdf := analisis_tombol_pdf(Selector(page.content(), url=page.url))["url_unduh"]:
            cek.update(cek_pdf_di_halaman(page, url_pdf), url_unduh=url_pdf)

    try:
        page = sesi.fetch(url, page_action=aksi, timeout=_TIMEOUT_BROWSER, network_idle=False)
    except Exception as e:
        return None, type(e).__name__
    if page.status != 200:
        return None, f"HTTP {page.status}"
    if not adalah_artikel(page):
        return None, None
    data = _baca(page, "browser")
    data["analisis_pdf"].update(cek)
    return data, None


def cek_pdf_lewat_browser(sesi, url_artikel: str, url_pdf: str) -> dict:
    """Cek PDF dari dalam halaman artikel di browser, untuk situs yang menolak unduhan otomatis lewat HTTP."""
    hasil = {}

    def aksi(page):
        if _tunggu_artikel(page):
            hasil.update(cek_pdf_di_halaman(page, url_pdf))

    try:
        sesi.fetch(url_artikel, page_action=aksi, timeout=_TIMEOUT_BROWSER, network_idle=False)
    except Exception as e:
        return hasil_cek("gagal", type(e).__name__, lewat="browser")
    return hasil or hasil_cek("gagal", "halaman artikel tidak termuat di browser", lewat="browser")


def _cek_semua_pdf(sesi, daftar: list[dict], browser_cadangan: bool, lapor) -> None:
    """Pastikan tautan unduh PDF tiap artikel benar-benar berkas PDF; hasilnya mengisi `analisis_pdf` di tempat."""
    belum = [a for a in daftar if a["analisis_pdf"]["status"] == "belum_dicek"]
    if not belum:
        return
    lapor(f"Mengecek {len(belum)} tautan PDF...")
    cek = _peta_per_host(
        lambda a: cek_pdf(a["analisis_pdf"]["url_unduh"], referer=a["url"]), belum, lambda a: a["analisis_pdf"]["url_unduh"]
    )
    for a, hasil in zip(belum, cek):
        a["analisis_pdf"].update(hasil)
    ditolak = [a for a in belum if a["analisis_pdf"]["alasan"] in BISA_DICOBA_BROWSER] if browser_cadangan else []
    if ditolak:
        lapor(f"Mengecek ulang {len(ditolak)} tautan PDF lewat browser...")
        for a in ditolak:
            a["analisis_pdf"].update(cek_pdf_lewat_browser(sesi, a["url"], a["analisis_pdf"]["url_unduh"]))


def kunci_judul(judul: str) -> str:
    return " ".join(re.findall(r"[^\W_]+", judul.lower()))


def _jalankan_mesin(
    nama: str, kueri_cari: str, halaman: int, tampilkan: bool, interaktif: bool, kendali: Kendali, lapor
) -> tuple[list, bool]:
    """Cari di satu mesin. Hasil: (temuan, apakah pengguna memverifikasi tantangan di jendela browser).

    Biasanya headless. Bila mesin meminta verifikasi dan `interaktif`, jendelanya dibuka terlihat (profil yang sama)
    supaya pengguna sendiri menyelesaikannya; tantangan tidak pernah dikerjakan otomatis. Tanpa verifikasi, mesin
    diistirahatkan supaya tidak terus ditembak dan jendela tidak muncul berulang-ulang.
    """
    cari = {"bing": cari_bing, "brave": cari_brave}[nama]
    opsi = {"disable_resources": True, "timeout": 30_000, "retries": 1, "user_data_dir": str(PROFIL_MESIN[nama])}
    if tampilkan:
        kendali.pulihkan(nama)  # pengguna sudah siap memverifikasi sekarang, masa istirahat diabaikan
    kendali.cek_boleh(nama)
    if not tampilkan:
        try:
            with StealthySession(headless=True, **opsi) as sesi:
                temuan = cari(sesi, kueri_cari, halaman, sebelum_permintaan=lambda i: kendali.tunggu_giliran(nama, lapor))
            return temuan, False
        except CaptchaError:
            if not interaktif:
                kendali.istirahatkan(nama, "captcha tidak diverifikasi")
                raise
        batas = datetime.now() + timedelta(seconds=TUNGGU_VERIFIKASI)
        lapor(
            f"  {nama}: meminta verifikasi. Jendela {NAMA_MESIN[nama]} dibuka; selesaikan sendiri "
            f'(klik "Verify" atau kotak centangnya) sebelum {batas:%H:%M:%S}. Cek taskbar bila jendelanya tidak terlihat.'
        )

    def sebelum(i: int) -> None:
        # Memuat ulang halaman tantangan untuk diverifikasi tidak perlu menunggu jeda; halaman berikutnya tetap berjeda.
        if i == 0 and not tampilkan:
            kendali.catat_permintaan(nama)
        else:
            kendali.tunggu_giliran(nama, lapor)

    info = {}
    try:
        with StealthySession(headless=False, **opsi) as sesi:
            temuan = cari(sesi, kueri_cari, halaman, TUNGGU_VERIFIKASI, sebelum, info)
    except CaptchaError:
        kendali.istirahatkan(nama, "captcha tidak diverifikasi")
        raise
    kendali.pulihkan(nama)
    return temuan, (not tampilkan) or info.get("captcha_terlihat", False)


def _host_cocok(url: str, situs: str) -> bool:
    host = urlparse(url).netloc.lower().split(":")[0].removeprefix("www.")
    return host == situs or host.endswith("." + situs)


def _saring_hasil(temuan: list, kueri_cari: str) -> tuple[list, str | None]:
    """Buang hasil yang jelas tidak nyambung. Mesin pencari kadang membatasi pencarian otomatis secara halus dengan
    hasil acak (mis. daftar kuliner untuk kueri algoritma), terutama untuk kueri `site:`.

    Hasil: (temuan yang layak dipakai, alasan bila seluruh hasil dianggap tidak relevan).
    """
    if not temuan:
        return temuan, None
    if cocok := re.search(r"(?:^|\s)site:(\S+)", kueri_cari):
        situs = cocok[1].split("/")[0].lower().removeprefix("www.")
        di_situs = [h for h in temuan if _host_cocok(h.url, situs)]
        return (di_situs, None) if di_situs else ([], f"tidak ada hasil dari {situs}")
    token = set(kata_penting(kueri_cari))
    if token and not any(token & set(kata_penting(f"{h.judul} {h.cuplikan} {h.url}")) for h in temuan):
        return [], "tidak ada yang memuat kata kueri"
    return temuan, None


def _cari_di_mesin(mesin, kueri_cari: str, halaman: int, *, tampilkan, interaktif, segarkan, kendali, lapor) -> tuple[list, dict]:
    hasil_cari, status_mesin = [], {}
    for nama in [m for m in MESIN if m in mesin]:
        st = {"status": "ok", "hasil": 0, "pesan": None, "dari_cache": False, "diverifikasi": False, "istirahat_sampai": None}
        temuan = [] if segarkan else kendali.ambil_cache(nama, kueri_cari, halaman)
        if temuan:
            temuan = [HasilCari(**h) for h in temuan]
            st.update(hasil=len(temuan), dari_cache=True)
            lapor(f"  {nama}: {len(temuan)} hasil (dari cache)")
        else:
            try:
                temuan, diverifikasi = _jalankan_mesin(nama, kueri_cari, halaman, tampilkan, interaktif, kendali, lapor)
            except MesinIstirahat as e:
                temuan = []
                st.update(status="istirahat", pesan=str(e), istirahat_sampai=iso(e.sampai))
            except CaptchaError:
                temuan = []
                sampai = (kendali.istirahat_sampai(nama) or (None,))[0]
                st.update(
                    status="captcha",
                    pesan="captcha tidak diverifikasi" + (f"; istirahat sampai {jam_rapi(sampai)}" if sampai else ""),
                    istirahat_sampai=iso(sampai) if sampai else None,
                )
            except Exception as e:  # mesin pencari bisa berubah atau memblokir kapan saja
                temuan = []
                st.update(status="gagal", pesan=f"{type(e).__name__}: {e}")
            else:
                temuan, alasan = _saring_hasil(temuan, kueri_cari)
                if alasan:
                    st.update(
                        status="tidak_relevan",
                        pesan=f"hasil tidak relevan ({alasan}); kemungkinan dibatasi mesin pencari, tidak disimpan di cache",
                    )
                else:
                    st.update(hasil=len(temuan), diverifikasi=diverifikasi)
                    if temuan:
                        kendali.simpan_cache(nama, kueri_cari, halaman, [asdict(h) for h in temuan])
                    else:
                        st["pesan"] = "0 hasil (mungkin diblokir sementara)"
            if st["status"] == "ok" and not st["pesan"]:
                lapor(f"  {nama}: {st['hasil']} hasil" + (" setelah verifikasi" if st["diverifikasi"] else ""))
            else:
                lapor(f"  {nama}: {st['pesan']}")
        status_mesin[nama] = st
        hasil_cari += temuan
    return hasil_cari, status_mesin


def _pilih_kandidat(hasil_cari: list, url_sumber: str | None, maks: int) -> dict[str, list[str]]:
    """URL artikel unik -> mesin yang menemukannya, dibatasi `maks`.

    Diurutkan per posisi agar hasil tiap mesin berselang-seling dan sama-sama terwakili saat dibatasi.
    """
    kandidat: dict[str, list[str]] = {}
    for h in sorted(hasil_cari, key=lambda h: h.posisi):
        url = normalisasi_url(h.url)
        if url and url != url_sumber:
            mesin_url = kandidat.setdefault(url, [])
            if h.mesin not in mesin_url:
                mesin_url.append(h.mesin)
    return dict(list(kandidat.items())[:maks])


def _kunci_situs(url: str) -> str:
    return re.sub(r"^https?://(www\.)?", "", url.lower()).rstrip("/")


def daftar_jurnal(artikel: list[dict]) -> list[dict]:
    """Jurnal unik dari daftar artikel, lengkap dengan link website-nya dan artikel yang ditemukan di sana."""
    jurnal: dict[str, dict] = {}
    for a in artikel:
        situs, nama = a.get("situs_jurnal"), a.get("jurnal")
        if not situs and not nama:
            continue
        # Situs tanpa path (mis. agregator seperti Neliti) bisa memuat banyak jurnal, jadi namanya ikut jadi pembeda.
        kunci = _kunci_situs(situs or "")
        if not (situs and urlparse(situs).path.strip("/")):
            kunci += "|" + (nama or "").lower()
        j = jurnal.setdefault(
            kunci, {"nama": nama or urlparse(situs).netloc, "situs": situs, "jumlah_artikel": 0, "artikel": []}
        )
        j["jumlah_artikel"] += 1
        j["artikel"].append({"judul": a["judul"], "url": a["url"]})
    # Artikel dari agregator (website jurnalnya tak diketahui) digabung ke jurnal bernama sama yang website-nya diketahui.
    dengan_situs = {j["nama"].lower(): j for j in jurnal.values() if j["situs"]}
    for kunci, j in list(jurnal.items()):
        if not j["situs"] and (tujuan := dengan_situs.get(j["nama"].lower())):
            tujuan["jumlah_artikel"] += j["jumlah_artikel"]
            tujuan["artikel"] += j["artikel"]
            del jurnal[kunci]
    return sorted(jurnal.values(), key=lambda j: (-j["jumlah_artikel"], j["nama"].lower()))


MODE = ("katalog_dulu", "katalog_saja", "gabung", "mesin")
AMBANG_RELEVAN = 10  # katalog_dulu: Bing/Brave baru dipakai bila artikel relevan dari katalog kurang dari ini
TETANGGA_PER_PENCARIAN = 5  # instalasi OJS yang dicek daftar jurnalnya per pencarian (berjalan bersama cek PDF)


def _cari_katalog(katalog: Katalog, token_urut: list[str], *, situs, jurnal_maks, cukup, segarkan, lapor) -> tuple[list, dict]:
    """Cari langsung di situs-situs jurnal katalog. Galat apa pun tidak menggagalkan pencarian; mesin pencari masih
    bisa dipakai."""
    if not situs and not katalog.jumlah_jurnal():
        pesan = "katalog masih kosong; impor daftar jurnal DOAJ atau tambahkan URL jurnal"
        lapor(f"  katalog: {pesan}")
        return [], {"status": "kosong", "pesan": pesan, "hasil": 0}
    try:
        hasil, stat = cari_di_katalog(
            katalog, token_urut, jurnal_maks=jurnal_maks, situs=situs, cukup=cukup, segarkan=segarkan, lapor=lapor
        )
    except Exception as e:  # situs jurnal atau berkas katalog bermasalah: lanjut dengan mesin pencari
        lapor(f"  katalog: gagal ({type(e).__name__}: {e})")
        return [], {"status": "gagal", "pesan": f"{type(e).__name__}: {e}", "hasil": 0}
    stat.update(status="ok", pesan=None)
    lapor(
        f"  katalog: {len(hasil)} hasil dari {stat['situs_memberi_hasil']} situs jurnal "
        f"({stat['permintaan']} permintaan, {stat['dari_cache']} dari cache, {stat['durasi_detik']} dtk)"
    )
    return hasil, stat


def _entri_katalog(stat: dict) -> dict:
    """Status katalog dalam format yang sama dengan status mesin pencari."""
    pesan = stat.get("pesan")
    if stat["status"] == "ok":
        pesan = f"{stat['hasil']} hasil dari {stat['situs_memberi_hasil']} situs jurnal"
        if stat["diblokir"] or stat["gagal"]:
            pesan += f" ({stat['diblokir']} menolak, {stat['gagal']} tak terjangkau)"
    return {
        "status": stat["status"], "hasil": stat.get("hasil", 0), "pesan": pesan,
        "dari_cache": bool(stat.get("dari_cache")) and not stat.get("permintaan"),
        "diverifikasi": False, "istirahat_sampai": None,
    }


def _entri_dilewati(alasan: str) -> dict:
    return {"status": "dilewati", "hasil": 0, "pesan": alasan, "dari_cache": False, "diverifikasi": False, "istirahat_sampai": None}


def _nilai(kandidat: dict[str, list[str]], dibuka: dict, token_kueri: set[str], terlihat: set) -> tuple[list, list, list, list]:
    """Artikel dari kandidat yang sudah dibuka. Hasil: (semua artikel, unik, ditampilkan, gagal dibuka)."""
    artikel, gagal = [], []
    for url, oleh in kandidat.items():
        data, galat = dibuka[url]
        if galat:
            gagal.append({"url": url, "alasan": galat})
        elif data:
            artikel.append({"skor": skor(token_kueri, data), **data, "ditemukan_oleh": list(oleh)})
    # sort() stabil, jadi saat skor sama urutan hasil pencarian tetap dipakai.
    artikel.sort(key=lambda a: -a["skor"])
    # Satu artikel bisa muncul lewat beberapa URL (redirect, agregator seperti Neliti):
    # simpan versi berskor tertinggi dan buang artikel sumber.
    unik, terlihat = [], set(terlihat)
    for a in artikel:
        kunci = {normalisasi_url(a["url"]) or a["url"], kunci_judul(a["judul"])}
        if kunci.isdisjoint(terlihat):
            unik.append(a)
        terlihat |= kunci
    # Tanpa minimal dua konsep kueri (dengan padanannya) di judul/kata kunci/abstrak, artikel dianggap tidak berkaitan.
    tampil = [a for a in unik if berkaitan(token_kueri, a)] if token_kueri else unik
    return artikel, unik, tampil, gagal


def _pelajari(katalog: Katalog, tampil: list[dict], token_kueri: set[str]) -> int:
    """Catat jurnal tempat artikel relevan ditemukan (lewat sumber mana pun) beserta kata kunci artikelnya, supaya
    pencarian serupa berikutnya memilih jurnal itu. Hasil: jumlah jurnal yang baru masuk katalog."""
    temuan, baru = defaultdict(int), 0
    for a in tampil:
        if not a.get("situs_jurnal"):
            continue  # agregator: website jurnalnya tak diketahui
        beranda, adalah_baru = katalog.tambah(a["situs_jurnal"], a.get("jurnal"), a.get("kata_kunci") or (), "otomatis")
        if beranda:
            temuan[beranda] += 1
            baru += adalah_baru
    katalog.catat_temuan(dict(temuan), token_kueri)
    return baru


def _simpan_tetangga(katalog: Katalog, tugas) -> dict:
    """Tulis hasil penemuan jurnal se-instalasi (dijalankan di latar) ke katalog."""
    hasil = {"instalasi_dicek": 0, "jurnal_baru": 0}
    try:
        temuan = tugas.result() if tugas else {}
    except Exception:
        return hasil  # dicoba lagi pada pencarian berikutnya
    for dasar, daftar in temuan.items():
        hasil["jurnal_baru"] += katalog.tambah_banyak(((b, nama, ()) for b, nama in daftar), "tetangga")["baru"]
        katalog.tandai_tetangga_dicek(dasar)
        hasil["instalasi_dicek"] += 1
    return hasil


def cari_terkait(
    kueri: str | None = None,
    dari_url: str | None = None,
    situs: str | None = None,
    mesin: list[str] | tuple[str, ...] = tuple(MESIN),
    halaman: int = 1,
    maks: int = 30,
    tampilkan: bool = False,
    browser_cadangan: bool = True,
    interaktif: bool = True,
    segarkan: bool = False,
    mode: str = "katalog_dulu",
    jurnal_maks: int = 100,
    lapor=lambda pesan: None,
    katalog: Katalog | None = None,
) -> dict:
    """Cari artikel jurnal yang berkaitan dengan `kueri` atau dengan artikel di `dari_url` (pilih salah satu).

    `mode`: `katalog_dulu` (cari langsung lewat fitur cari situs-situs jurnal di katalog; Bing/Brave hanya bila
    artikel relevan kurang dari AMBANG_RELEVAN), `katalog_saja`, `gabung` (keduanya), atau `mesin` (Bing/Brave
    saja). `jurnal_maks`: jumlah jurnal katalog paling sesuai yang ditanya. `situs` memfokuskan pencarian ke satu
    situs: katalog mencari langsung di situs itu, mesin pencari memakai `site:`.

    Setiap artikel ikut dianalisis: ada tidaknya tombol unduh PDF, dan apakah tautannya benar-benar PDF; hasilnya
    juga dikelompokkan per jurnal beserta link website-nya, dan jurnalnya dicatat di katalog untuk pencarian
    berikutnya. `browser_cadangan` membuka ulang lewat browser halaman/PDF yang gagal karena proteksi bot atau SSL.
    `interaktif` mengizinkan jendela verifikasi mesin pencari dibuka untuk pengguna; `segarkan` mengabaikan cache
    hasil pencarian. `lapor` menerima pesan kemajuan. `katalog` bisa diisi objek Katalog lain (mis. untuk uji).
    """
    kueri, dari_url = (kueri or "").strip() or None, (dari_url or "").strip() or None
    if bool(kueri) == bool(dari_url):
        raise InputTidakValid("Isi salah satu: kueri atau URL artikel.")
    if dari_url and not re.match(r"https?://", dari_url, re.I):
        raise InputTidakValid("URL artikel harus diawali http:// atau https://")
    if not mesin or any(m not in MESIN for m in mesin):
        raise InputTidakValid(f"Mesin hanya boleh berisi: {', '.join(MESIN)}")
    if mode not in MODE:
        raise InputTidakValid(f"Mode hanya boleh: {', '.join(MODE)}")
    if halaman < 1 or maks < 1:
        raise InputTidakValid("Halaman dan maks minimal 1.")
    if not 1 <= jurnal_maks <= 1000:
        raise InputTidakValid("Jumlah jurnal katalog yang ditanya harus 1-1000.")
    # Operator site: memakai host (opsional dengan path), bukan URL lengkap: "https://x.ac.id/" -> "x.ac.id".
    situs = re.sub(r"^https?://", "", (situs or "").strip(), flags=re.I).rstrip("/") or None

    mulai = time.perf_counter()
    durasi_fase, jam = {}, [mulai]

    def catat(fase: str) -> None:
        jam.append(time.perf_counter())
        durasi_fase[fase] = round(durasi_fase.get(fase, 0) + jam[-1] - jam[-2], 1)

    with Kendali() as kendali, (nullcontext(katalog) if katalog else Katalog()) as kat, _SesiMalas(
        headless=not tampilkan, disable_resources=True, timeout=30_000, retries=2, user_data_dir=str(PROFIL_BROWSER)
    ) as sesi:
        sumber, url_sumber, terlihat = None, None, set()
        if dari_url:
            sumber, galat = buka(dari_url)
            if galat in BISA_DICOBA_BROWSER and browser_cadangan:
                sumber, galat = buka_lewat_browser(sesi, dari_url)
            if not sumber:
                alasan = galat or "bukan halaman artikel jurnal"
                raise InputTidakValid(f"Tidak bisa membaca metadata artikel sumber ({alasan}): {dari_url}")
            url_sumber = normalisasi_url(sumber["url"])
            terlihat = {url_sumber, kunci_judul(sumber["judul"])}
            kueri = " ".join(kata_penting(sumber["judul"]))
            token_kueri = set(kata_penting(sumber["judul"] + " " + " ".join(sumber["kata_kunci"])))
            # Kata kunci penulis biasanya paling khas, jadi didahulukan untuk kotak cari situs jurnal.
            token_urut = list(dict.fromkeys(kata_penting(" ".join(sumber["kata_kunci"])) + kata_penting(sumber["judul"])))
            lapor(f"Artikel sumber: {sumber['judul']}")
        else:
            token_kueri, token_urut = set(kata_penting(kueri)), kata_penting(kueri)

        if situs:
            kueri_cari = f"{kueri} site:{situs}"
        elif re.search(r"\b(jurnal|journal)\b", kueri, re.I):
            kueri_cari = kueri
        else:
            kueri_cari = f"{kueri} jurnal"

        kandidat: dict[str, list[str]] = {}  # URL artikel -> sumber yang menemukannya, urut dibuka
        dibuka: dict[str, tuple] = {}
        ulang: list[str] = []
        hasil_pencarian, status_mesin, stat_katalog = 0, {}, None

        def tahap(temuan: list) -> None:
            """Buka kandidat baru dari hasil pencarian; untuk URL yang sudah dibuka, penemunya cukup digabungkan."""
            baru = []
            for url, oleh in _pilih_kandidat(temuan, url_sumber, maks).items():
                if url in kandidat:
                    kandidat[url] += [m for m in oleh if m not in kandidat[url]]
                else:
                    kandidat[url] = oleh
                    baru.append(url)
            if baru:
                lapor(f"Membuka {len(baru)} kandidat...")
                dibuka.update(zip(baru, _peta_per_host(buka, baru)))
            catat("buka_kandidat")
            perlu_browser = [u for u in baru if dibuka[u][1] in BISA_DICOBA_BROWSER] if browser_cadangan else []
            if perlu_browser:
                lapor(f"Membuka ulang {len(perlu_browser)} halaman lewat browser...")
                for u in perlu_browser:
                    dibuka[u] = buka_lewat_browser(sesi, u)
                ulang.extend(perlu_browser)
            catat("buka_ulang_browser")

        def cari_mesin() -> list:
            nonlocal status_mesin, hasil_pencarian
            lapor(f"Mencari: {kueri_cari}")
            temuan, status_mesin = _cari_di_mesin(
                mesin, kueri_cari, halaman, tampilkan=tampilkan, interaktif=interaktif, segarkan=segarkan,
                kendali=kendali, lapor=lapor,
            )
            catat("pencarian")
            hasil_pencarian += len(temuan)
            return temuan

        if mode == "mesin":
            tahap(cari_mesin())
        else:
            lapor("Mencari langsung di situs jurnal" + (f" {situs}" if situs else " dari katalog") + "...")
            hasil_katalog, stat_katalog = _cari_katalog(
                kat, token_urut, situs=situs, jurnal_maks=jurnal_maks, cukup=maks, segarkan=segarkan, lapor=lapor
            )
            catat("katalog")
            hasil_pencarian += len(hasil_katalog)
            if mode == "gabung":
                tahap(hasil_katalog + cari_mesin())
            else:
                tahap(hasil_katalog)
                relevan, ambang = len(_nilai(kandidat, dibuka, token_kueri, terlihat)[2]), min(AMBANG_RELEVAN, maks)
                if mode == "katalog_dulu" and relevan < ambang:
                    lapor(f"Katalog memberi {relevan} artikel relevan (kurang dari {ambang}); lanjut ke mesin pencari...")
                    tahap(cari_mesin())
                else:
                    alasan = "mode katalog saja" if mode == "katalog_saja" else f"katalog sudah memberi {relevan} artikel relevan"
                    status_mesin = {m: _entri_dilewati(alasan) for m in MESIN if m in mesin}
            status_mesin = {"katalog": _entri_katalog(stat_katalog), **status_mesin}

        artikel, unik, tampil, gagal = _nilai(kandidat, dibuka, token_kueri, terlihat)
        dipelajari = _pelajari(kat, tampil, token_kueri)
        calon_tetangga = kat.instalasi_untuk_tetangga(TETANGGA_PER_PENCARIAN)
        with ThreadPoolExecutor(max_workers=1) as latar:
            tugas_tetangga = latar.submit(temukan_tetangga, calon_tetangga) if calon_tetangga else None
            _cek_semua_pdf(sesi, tampil + ([sumber] if sumber else []), browser_cadangan, lapor)
            catat("cek_pdf")
            tetangga = _simpan_tetangga(kat, tugas_tetangga)
        catat("katalog_tetangga")
        jurnal_di_katalog = kat.jumlah_jurnal()

    jurnal = daftar_jurnal(tampil)
    pdf = [a["analisis_pdf"] for a in tampil]
    return {
        "kueri": kueri,
        "kueri_pencarian": kueri_cari,
        "sumber": dari_url,
        "artikel_sumber": sumber,
        "mode": mode,
        "mesin": [nama for nama, s in status_mesin.items() if s["status"] == "ok"],
        "waktu": datetime.now().isoformat(timespec="seconds"),
        "statistik": {
            "mesin": status_mesin,
            "katalog": {
                **(stat_katalog or {"status": "dilewati"}),
                "jurnal_dipelajari": dipelajari,
                "tetangga": tetangga,
                "jurnal_di_katalog": jurnal_di_katalog,
            },
            "hasil_pencarian": hasil_pencarian,
            "kandidat": len(kandidat),
            "dibuka_ulang_browser": len(ulang),
            "pulih_lewat_browser": sum(1 for u in ulang if dibuka[u][0]),
            "gagal_dibuka": len(gagal),
            "artikel_jurnal": len(artikel),
            "duplikat": len(artikel) - len(unik),
            "tanpa_kata_kueri": len(unik) - len(tampil),
            "ditampilkan": len(tampil),
            "jumlah_jurnal": len(jurnal),
            "punya_tombol_pdf": sum(p["ada_tombol"] for p in pdf),
            "pdf_bisa_diunduh": sum(p["status"] == "pdf" for p in pdf),
            "durasi_detik": round(time.perf_counter() - mulai, 1),
            "durasi_fase": durasi_fase,
        },
        "jurnal": jurnal,
        "artikel": tampil,
        "gagal_dibuka": gagal,
    }
