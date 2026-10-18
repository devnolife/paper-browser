"""Ekstraksi metadata artikel dari meta tag Google Scholar & Dublin Core (dipasang OJS dan platform jurnal lain)."""

import re
from urllib.parse import urlparse

# OJS: beranda jurnal adalah bagian URL artikel sebelum /article/(view|viewFile|download)/...
_BERANDA_OJS = re.compile(r"^(?P<beranda>https?://[^?#]+?)/article/(?:view|viewFile|download)/", re.I)
# Beranda yang diakhiri segmen bahasa setelah segmen jurnal: .../index.php/<jurnal>/<bahasa> atau <host>/<jurnal>/<bahasa>.
_ADA_BAHASA = re.compile(r"^https?://[^/]+/(?:index\.php/)?(?!index\.php/)[^/]+/[a-z]{2}(?:_[A-Z]{2})?$")
# Agregator/repositori menampung artikel dari banyak jurnal, jadi domainnya bukan website jurnal itu sendiri.
_AGREGATOR = (
    "academia.edu", "researchgate.net", "neliti.com", "semanticscholar.org", "scribd.com", "core.ac.uk",
    "europepmc.org", "scholar.archive.org", "zenodo.org", "doaj.org", "onesearch.id",
    "garuda.kemdikbud.go.id", "garuda.kemdiktisaintek.go.id",
)


def situs_jurnal(page) -> str | None:
    """Alamat website (beranda) jurnal: dari URL artikel OJS, lalu meta `DC.Source.URI`, lalu domain situsnya.

    URL artikel OJS didahulukan karena pasti bisa dibuka; `DC.Source.URI` kadang salah tulis (mis. path jurnal
    terulang `…/juwarta/juwarta` yang 404). None bila halaman berasal dari agregator (mis. academia.edu),
    karena domainnya bukan website jurnal.
    """
    url = page.url or ""
    if cocok := _BERANDA_OJS.match(url):
        beranda = cocok["beranda"]
        # Segmen bahasa OJS (…/<jurnal>/en/article/…) bukan bagian dari alamat beranda jurnal; dibuang hanya bila
        # masih ada segmen jurnal sebelumnya, supaya jurnal ber-path dua huruf (mis. /index.php/ji) tetap utuh.
        return beranda.rsplit("/", 1)[0] if _ADA_BAHASA.match(beranda) else beranda
    uri = (page.css('meta[name="DC.Source.URI"]::attr(content)').get() or "").strip()
    if uri.lower().startswith(("http://", "https://")):
        return uri.rstrip("/")
    alamat = urlparse(url)
    host = alamat.netloc.lower().split(":")[0]
    if not host or any(host == d or host.endswith("." + d) for d in _AGREGATOR):
        return None
    return f"{alamat.scheme}://{alamat.netloc}"


def _rapikan(teks: str | None) -> str | None:
    """Rapikan spasi dan perbaiki mojibake yang tersimpan di sebagian situs (mis. 'â€™' -> '’')."""
    if not teks:
        return teks
    if "â€" in teks or "Ã" in teks:
        try:
            teks = teks.encode("cp1252").decode("utf-8")
        except UnicodeError:
            pass
    return " ".join(teks.split())


def adalah_artikel(page) -> bool:
    """Halaman dianggap artikel jurnal jika punya meta `citation_title`."""
    return bool(page.css('meta[name="citation_title"]::attr(content)').get())


def ambil_metadata(page) -> dict:
    def meta(name):
        return _rapikan(page.css(f'meta[name="{name}"]::attr(content)').get())

    def metas(name):
        return [_rapikan(isi) for isi in page.css(f'meta[name="{name}"]::attr(content)').getall()]

    # Tiap citation_author_institution mengikuti citation_author pemiliknya, jadi urutan dokumen dipakai.
    penulis = []
    for tag in page.css('meta[name^="citation_author"]'):
        nama, isi = tag.attrib.get("name"), _rapikan(tag.attrib.get("content"))
        if nama == "citation_author":
            penulis.append({"nama": isi, "institusi": []})
        elif nama == "citation_author_institution" and penulis:
            penulis[-1]["institusi"].append(isi)

    abstrak = meta("DC.Description")
    if not abstrak and (bagian := page.css("section.item.abstract").first):
        abstrak = _rapikan(bagian.get_all_text(separator=" ", strip=True))

    awal, akhir = meta("citation_firstpage"), meta("citation_lastpage")
    return {
        "url": page.url,
        "judul": meta("citation_title") or " ".join((page.css("title::text").get() or "").split()),
        "penulis": penulis,
        "jurnal": meta("citation_journal_title") or meta("DC.Source"),
        "situs_jurnal": situs_jurnal(page),
        "volume": meta("citation_volume"),
        "nomor": meta("citation_issue"),
        "halaman": f"{awal}-{akhir}" if awal and akhir else awal,
        "tanggal_terbit": meta("citation_date") or meta("DC.Date.issued"),
        "kata_kunci": metas("citation_keywords"),
        "doi": meta("citation_doi") or meta("DC.Identifier.DOI"),
        "pdf": meta("citation_pdf_url"),
        "abstrak": abstrak,
    }
