"""Analisis tombol unduh PDF di halaman artikel jurnal, dan pengecekan bahwa tautannya benar-benar berkas PDF."""

import re
from urllib.parse import urljoin, urlparse

from curl_cffi import requests as curl
from curl_cffi.curl import CURL_WRITEFUNC_ERROR
from curl_cffi.requests.exceptions import RequestException

# Tautan galley di berbagai versi/tema OJS: OJS 3, tema OpenJournalTheme, dan OJS 2.
_GALLEY = "a.obj_galley_link, a.galley-link, .galleys_links a, .galley_list a, #articleFullText a"
# Daftar pustaka dan ekspor sitasi berisi tautan milik artikel lain atau berkas RIS/BibTeX, bukan tombol unduh artikel ini.
_AREA_LAIN = re.compile(r"reference|citation|bibliograph|pustaka", re.I)
_TEKS_UNDUH = re.compile(r"\b(pdf|download|unduh|full\s*text|teks\s*lengkap)\b", re.I)
_TEKS_UNDUH_PDF = re.compile(r"\b(download|unduh|full\s*text|teks\s*lengkap)\b.*\bpdf\b|\bpdf\b.*\b(download|unduh)\b", re.I)
_BUKAN_ARTIKEL = re.compile(r"template|panduan|guide|petunjuk|pedoman|pernyataan|statement|copyright|etika|ethic", re.I)
# OJS: /article/view/<id>/<galley> membuka penampil PDF (HTML); berkasnya sendiri ada di /article/download/<id>/<galley>.
_GALLEY_OJS = re.compile(r"^(?P<dasar>.+?/article)/(?P<aksi>view|viewFile|download)/(?P<id>[^/?#]+)/(?P<galley>[^/?#]+)", re.I)
BATAS_CEK = 1024  # berkas PDF wajib memuat "%PDF-" di 1024 byte pertamanya

_JS_CEK_PDF = """async ([url, batas]) => {
  const henti = new AbortController();
  const waktu = setTimeout(() => henti.abort(), 15000);
  try {
    const r = await fetch(url, { signal: henti.signal });
    const awal = [];
    if (r.ok && r.body) {
      const pembaca = r.body.getReader();
      while (awal.length < batas) {
        const { value, done } = await pembaca.read();
        if (done) break;
        awal.push(...value.subarray(0, batas - awal.length));
      }
      pembaca.cancel().catch(() => {});
    }
    return { status: r.status, jenis: r.headers.get("content-type"), ukuran: r.headers.get("content-length"), awal };
  } catch (e) {
    return { galat: e.name || String(e) };
  } finally {
    clearTimeout(waktu);
  }
}"""


def url_unduh_ojs(url: str) -> str | None:
    """URL berkas dari tautan galley OJS (`view` -> `download`); None jika bukan pola OJS."""
    if not (cocok := _GALLEY_OJS.match(url)):
        return None
    if cocok["aksi"].lower() == "download":
        return url
    return f"{cocok['dasar']}/download/{cocok['id']}/{cocok['galley']}"


def _kunci_galley(url: str | None) -> tuple | None:
    cocok = _GALLEY_OJS.match(url or "")
    return (cocok["dasar"].lower(), cocok["id"], cocok["galley"]) if cocok else None


def _href(page, a) -> str | None:
    href = (a.attrib.get("href") or "").strip()
    if not href or href.startswith(("#", "javascript:", "mailto:")):
        return None
    url = urljoin(page.url, href).split("#", 1)[0]
    return url if url.startswith(("http://", "https://")) else None


def _teks_tautan(a) -> str:
    teks = " ".join(a.get_all_text(separator=" ", strip=True).split())
    return teks or a.attrib.get("title") or a.attrib.get("aria-label") or a.css("img::attr(alt)").get() or ""


def _di_area_lain(a) -> bool:
    return any(_AREA_LAIN.search(f"{x.attrib.get('class', '')} {x.attrib.get('id', '')}") for x in a.iterancestors())


def analisis_tombol_pdf(page) -> dict:
    """Cari tombol/tautan unduh PDF artikel ini di halaman, beserta URL berkas PDF yang paling bisa dipercaya.

    `status` masih `belum_dicek`; isi lewat `cek_pdf` atau `cek_pdf_di_halaman`.
    """
    meta = (page.css('meta[name="citation_pdf_url"]::attr(content)').get() or "").strip()
    meta = urljoin(page.url, meta) if meta else None
    kunci_meta = _kunci_galley(meta)

    def milik_artikel_ini(url):
        return url == meta or (kunci_meta is not None and _kunci_galley(url) == kunci_meta)

    tombol, terbatas = {}, False
    for a in page.css(_GALLEY):
        if not (url := _href(page, a)):
            continue
        kelas, teks = (a.attrib.get("class") or "").lower().split(), _teks_tautan(a)
        if "pdf" in kelas or re.search(r"\bpdf\b", teks, re.I) or milik_artikel_ini(url):
            tombol.setdefault(url, teks)
            terbatas = terbatas or "restricted" in kelas  # OJS: galley khusus pelanggan
    for a in page.css("a[href]"):
        if not (url := _href(page, a)) or url in tombol:
            continue
        teks = _teks_tautan(a)
        umum = _TEKS_UNDUH_PDF.search(teks) or (urlparse(url).path.lower().endswith(".pdf") and _TEKS_UNDUH.search(teks))
        if milik_artikel_ini(url) or (umum and not _BUKAN_ARTIKEL.search(teks) and not _di_area_lain(a)):
            tombol[url] = teks

    url_unduh = meta or next(iter(tombol), None)
    if url_unduh:
        url_unduh = url_unduh_ojs(url_unduh) or url_unduh
    return {
        "ada_tombol": bool(tombol),
        "tombol": [{"teks": teks, "url": url} for url, teks in tombol.items()],
        "url_unduh": url_unduh,
        "terbatas": terbatas,
        **hasil_cek("belum_dicek" if url_unduh else "tanpa_tautan"),
    }


def hasil_cek(status: str, alasan: str | None = None, ukuran: int | None = None, lewat: str | None = None) -> dict:
    return {"status": status, "alasan": alasan, "ukuran_byte": ukuran, "dicek_lewat": lewat}


def _nilai(status: int, jenis: str | None, ukuran, awal: bytes, lewat: str) -> dict:
    if status != 200:
        return hasil_cek("gagal", f"HTTP {status}", lewat=lewat)
    if b"%PDF-" in awal[:BATAS_CEK]:
        return hasil_cek("pdf", ukuran=int(ukuran) if str(ukuran or "").isdigit() else None, lewat=lewat)
    return hasil_cek("bukan_pdf", (jenis or "jenis tidak diketahui").split(";")[0].strip(), lewat=lewat)


def cek_pdf(url: str, referer: str | None = None, timeout: int = 15) -> dict:
    """Pastikan `url` mengembalikan berkas PDF lewat HTTP; transfer dihentikan setelah 1 KB pertama.

    `referer` sebaiknya halaman artikelnya: sebagian situs menolak unduhan tanpa referer dari situsnya sendiri.
    """
    awal = bytearray()

    def tampung(potongan: bytes):
        awal.extend(potongan)
        return CURL_WRITEFUNC_ERROR if len(awal) >= BATAS_CEK else len(potongan)

    try:
        r = curl.get(
            url, impersonate="chrome", headers={"Referer": referer} if referer else None, timeout=timeout, content_callback=tampung
        )
    except RequestException as e:
        # Penghentian sesudah 1 KB pertama memang disengaja; selain itu berarti galat sungguhan.
        if len(awal) < BATAS_CEK or e.response is None:
            return hasil_cek("gagal", type(e).__name__, lewat="http")
        r = e.response
    except Exception as e:
        return hasil_cek("gagal", type(e).__name__, lewat="http")
    return _nilai(r.status_code, r.headers.get("content-type"), r.headers.get("content-length"), bytes(awal), "http")


def cek_pdf_di_halaman(page, url: str) -> dict:
    """Seperti `cek_pdf`, tapi lewat `fetch` dari dalam halaman Playwright yang sedang terbuka.

    Berguna untuk situs berproteksi bot: browser sudah lolos tantangan dan memegang cookie-nya.
    """
    try:
        h = page.evaluate(_JS_CEK_PDF, [url, BATAS_CEK])
    except Exception as e:
        return hasil_cek("gagal", type(e).__name__, lewat="browser")
    if "galat" in h:
        return hasil_cek("gagal", h["galat"], lewat="browser")
    return _nilai(h["status"], h["jenis"], h["ukuran"], bytes(h["awal"]), "browser")


def ukuran_rapi(n: int | None) -> str | None:
    if not n:
        return None
    satuan = "KB" if n < 1024**2 else "MB"
    return f"{n / (1024 if satuan == 'KB' else 1024**2):.1f} {satuan}".replace(".", ",")


def status_pdf(analisis: dict) -> str:
    """Status unduhan satu baris, mis. 'bisa diunduh (164,9 KB)' atau 'gagal dicek (HTTP 403)'."""
    status, alasan = analisis["status"], analisis["alasan"]
    if status == "pdf":
        teks = "bisa diunduh" + (f" ({u})" if (u := ukuran_rapi(analisis["ukuran_byte"])) else "")
    else:
        teks = {
            "bukan_pdf": f"bukan berkas PDF ({alasan})",
            "gagal": f"gagal dicek ({alasan})",
            "tanpa_tautan": "tidak ada tautan PDF",
        }.get(status, "belum dicek")
    if analisis["dicek_lewat"] == "browser":
        teks += ", dicek lewat browser"
    if analisis["terbatas"]:
        teks += ", akses terbatas"
    return teks


def ringkas_pdf(analisis: dict) -> str:
    """Status unduhan plus tombol yang ditemukan, mis. 'bisa diunduh (164,9 KB) · tombol "PDF"'."""
    tombol = analisis["tombol"]
    if not tombol:
        return f"{status_pdf(analisis)} · tanpa tombol"
    lainnya = f" (+{len(tombol) - 1})" if len(tombol) > 1 else ""
    return f'{status_pdf(analisis)} · tombol "{tombol[0]["teks"]}"{lainnya}'
