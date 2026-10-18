"""Pencarian web lewat Brave dan Bing memakai sesi browser Scrapling, plus normalisasi URL artikel.

DuckDuckGo tidak disertakan: dari jaringan pengembangan domainnya diblokir ISP (DNS diarahkan ke
Internet Positif), sehingga parsernya tidak bisa diuji.
"""

import base64
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urlparse

from scrapling.parser import Selector

# OJS: /article/view/<id>[/<galley>], /article/download/<id>/<galley>[/<file>], /article/viewFile/<id>/<galley>
_ARTIKEL_OJS = re.compile(r"^(?P<dasar>https?://[^?#]+?/article)/(?:view|viewFile|download)/(?P<id>\d+)(?:[/?#]|$)", re.I)


@dataclass
class HasilCari:
    judul: str
    url: str
    cuplikan: str
    mesin: str
    posisi: int


class CaptchaError(RuntimeError):
    """Mesin pencari meminta verifikasi bahwa pengguna bukan bot."""


def normalisasi_url(url: str) -> str | None:
    """Arahkan URL hasil pencarian ke halaman artikelnya; None jika tak layak dibuka (PDF non-OJS)."""
    if cocok := _ARTIKEL_OJS.match(url):
        return f"{cocok['dasar']}/view/{cocok['id']}"
    url = url.split("#", 1)[0]
    if urlparse(url).path.lower().endswith(".pdf"):
        return None
    return url


def _teks(el) -> str:
    return " ".join(el.get_all_text(separator=" ", strip=True).split()) if el else ""


def _tanpa_jeda(i: int) -> None:
    pass


def cari_brave(
    sesi, kueri: str, halaman: int = 1, tunggu_verifikasi: int = 0, sebelum_permintaan=_tanpa_jeda, info=None
) -> list[HasilCari]:
    """Ambil `halaman` halaman hasil Brave. `sebelum_permintaan(i)` dipanggil sebelum memuat halaman ke-i.

    Bila `tunggu_verifikasi` > 0 (detik), jendela dibawa ke depan dan dibiarkan terbuka selama itu supaya
    pengguna sendiri bisa menekan "Verify" saat Brave meminta captcha; `info["captcha_terlihat"]` mencatat
    apakah captcha sempat muncul. Tanpa itu, captcha langsung dilaporkan sebagai CaptchaError.
    """
    info = {} if info is None else info
    if tunggu_verifikasi:

        def aksi(page):
            try:
                page.wait_for_selector('[data-type="web"], .captcha-wrapper', timeout=15_000)
            except Exception:
                return
            if page.query_selector(".captcha-wrapper"):
                info["captcha_terlihat"] = True
                page.bring_to_front()

        tunggu = {"wait_selector": '[data-type="web"]', "timeout": tunggu_verifikasi * 1000, "page_action": aksi}
    else:
        tunggu = {"wait_selector": '[data-type="web"], .captcha-wrapper'}
    hasil = []
    for i in range(halaman):
        try:
            sebelum_permintaan(i)
        except Exception:
            if i == 0:
                raise
            break  # mis. batas permintaan habis di tengah jalan: halaman yang sudah didapat tetap dipakai
        page = sesi.fetch(
            "https://search.brave.com/search?" + urlencode({"q": kueri, "source": "web", "offset": i}),
            network_idle=False,
            **tunggu,
        )
        item_web = page.css('[data-type="web"]')
        if not item_web and page.css(".captcha-wrapper"):
            raise CaptchaError("Brave meminta verifikasi captcha")
        for item in item_web:
            if url := item.css('a[href^="http"]::attr(href)').get():
                judul = item.css(".title::attr(title)").get() or _teks(item.css(".title").first)
                cuplikan = _teks(item.css(".generic-snippet .content, .snippet-description").first)
                hasil.append(HasilCari(judul, url, cuplikan, "brave", len(hasil) + 1))
    return hasil


def _url_asli_bing(href: str) -> str:
    """Bing membungkus link dalam redirect `/ck/a?...&u=a1<base64url>`; kembalikan URL aslinya."""
    kode = parse_qs(urlparse(href).query).get("u", [""])[0]
    if kode.startswith("a1"):
        try:
            url = base64.urlsafe_b64decode(kode[2:] + "=" * (-len(kode[2:]) % 4)).decode()
        except ValueError:
            url = ""
        if url.startswith("http"):
            return url
    return href


def _item_bing(html: str, mulai: int) -> list[HasilCari]:
    hasil = []
    for item in Selector(html).css("li.b_algo"):
        tautan = item.css("h2 a").first
        if tautan and (href := tautan.attrib.get("href")):
            hasil.append(
                HasilCari(_teks(tautan), _url_asli_bing(href), _teks(item.css("p").first), "bing", mulai + len(hasil))
            )
    return hasil


_BING_HASIL = "li.b_algo"
# Tantangan Cloudflare Turnstile Bing ("One last step"); tidak pernah dikerjakan otomatis.
_BING_CAPTCHA = "div.captcha, #turnstile-widget"


def cari_bing(
    sesi, kueri: str, halaman: int = 1, tunggu_verifikasi: int = 0, sebelum_permintaan=_tanpa_jeda, info=None
) -> list[HasilCari]:
    """Ambil `halaman` halaman hasil Bing. `sebelum_permintaan(i)` dipanggil sebelum memuat halaman ke-i.

    Halaman 2 dan seterusnya dibuka dengan mengklik "Next page" seperti pengguna, supaya konteks pencarian Bing
    (`FPIG`, `FORM`) ikut terbawa; URL `first=` buatan sendiri terbukti menghasilkan halaman generik.
    Bila `tunggu_verifikasi` > 0 (detik), jendela dibawa ke depan dan dibiarkan terbuka selama itu supaya pengguna
    sendiri menyelesaikan tantangan Bing; `info["captcha_terlihat"]` mencatat apakah tantangan sempat muncul. Tanpa
    itu, tantangan di halaman pertama langsung dilaporkan sebagai CaptchaError.
    """
    info = {} if info is None else info
    kumpulan: list[str] = []

    def tunggu_hasil(page) -> bool:
        page.wait_for_selector(f"{_BING_HASIL}, {_BING_CAPTCHA}", timeout=30_000)
        if page.query_selector(_BING_HASIL):
            return True
        info["captcha_terlihat"] = True
        if not tunggu_verifikasi:
            return False
        page.bring_to_front()
        page.wait_for_selector(_BING_HASIL, timeout=tunggu_verifikasi * 1000)
        return True

    def aksi(page):
        # Semua galat ditangkap di sini: halaman yang sudah terkumpul tetap dipakai walau halaman berikutnya
        # gagal dimuat, diminta verifikasi, atau batas permintaan mesin habis di tengah jalan.
        try:
            if not tunggu_hasil(page):
                return
            kumpulan.append(page.content())
            for i in range(1, halaman):
                berikutnya = page.query_selector("a.sb_pagN")
                if not berikutnya:
                    break
                sebelum_permintaan(i)
                with page.expect_navigation():
                    berikutnya.click()
                if not tunggu_hasil(page):
                    break
                kumpulan.append(page.content())
        except Exception:
            pass

    sebelum_permintaan(0)
    page = sesi.fetch(
        "https://www.bing.com/search?" + urlencode({"q": kueri}),
        page_action=aksi,
        network_idle=False,
        **({"timeout": (tunggu_verifikasi + 30) * 1000} if tunggu_verifikasi else {}),
    )
    if not kumpulan and (info.get("captcha_terlihat") or page.css(_BING_CAPTCHA)):
        raise CaptchaError("Bing meminta verifikasi captcha")
    hasil = []
    for html in kumpulan or [page.html_content]:
        hasil += _item_bing(html, len(hasil) + 1)
    return hasil


MESIN = {"bing": cari_bing, "brave": cari_brave}  # urutan pemakaian: Bing (cepat, stabil) lalu Brave
