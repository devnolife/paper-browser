import pytest
from scrapling.parser import Selector

from jurnal.metadata import adalah_artikel, ambil_metadata, situs_jurnal

URL_ARTIKEL = "https://jurnal.komputasi.org/index.php/jst/article/view/28"

HTML_ARTIKEL = """<html><head>
<title>Penjadwalan Kuliah | Jurnal Sains</title>
<meta name="citation_title" content="Penjadwalan   Kuliah dengan Algoritma Greedy">
<meta name="citation_author" content="Andi">
<meta name="citation_author_institution" content="Universitas A">
<meta name="citation_author" content="Budi">
<meta name="citation_author_institution" content="Universitas B">
<meta name="citation_author_institution" content="Institut C">
<meta name="citation_journal_title" content="Jurnal Sains dan Teknologi">
<meta name="citation_volume" content="5">
<meta name="citation_issue" content="2">
<meta name="citation_firstpage" content="10">
<meta name="citation_lastpage" content="20">
<meta name="citation_date" content="2024/03/01">
<meta name="citation_keywords" content="greedy">
<meta name="citation_keywords" content="scheduling">
<meta name="citation_doi" content="10.1234/jst.v5i2.28">
<meta name="citation_pdf_url" content="https://jurnal.komputasi.org/index.php/jst/article/download/28/20">
<meta name="DC.Description" content="Abstrak  artikel
ini.">
</head><body></body></html>"""


def halaman(url: str, head: str = "") -> Selector:
    return Selector(f"<html><head>{head}</head><body></body></html>", url=url)


def test_ambil_metadata_artikel_ojs():
    assert ambil_metadata(Selector(HTML_ARTIKEL, url=URL_ARTIKEL)) == {
        "url": URL_ARTIKEL,
        "judul": "Penjadwalan Kuliah dengan Algoritma Greedy",
        "penulis": [
            {"nama": "Andi", "institusi": ["Universitas A"]},
            {"nama": "Budi", "institusi": ["Universitas B", "Institut C"]},
        ],
        "jurnal": "Jurnal Sains dan Teknologi",
        "situs_jurnal": "https://jurnal.komputasi.org/index.php/jst",
        "volume": "5",
        "nomor": "2",
        "halaman": "10-20",
        "tanggal_terbit": "2024/03/01",
        "kata_kunci": ["greedy", "scheduling"],
        "doi": "10.1234/jst.v5i2.28",
        "pdf": "https://jurnal.komputasi.org/index.php/jst/article/download/28/20",
        "abstrak": "Abstrak artikel ini.",
    }


def test_ambil_metadata_memperbaiki_mojibake():
    page = halaman(URL_ARTIKEL, '<meta name="citation_title" content="Donâ€™t Panic">')
    assert ambil_metadata(page)["judul"] == "Don’t Panic"


def test_ambil_metadata_tanpa_meta_memakai_judul_halaman():
    data = ambil_metadata(halaman("https://contoh.ac.id/berita/1", "<title> Berita   Kampus </title>"))
    assert data["judul"] == "Berita Kampus"
    assert data["penulis"] == []
    assert data["kata_kunci"] == []
    assert data["halaman"] is None


def test_adalah_artikel():
    assert adalah_artikel(Selector(HTML_ARTIKEL, url=URL_ARTIKEL))
    assert not adalah_artikel(halaman("https://contoh.ac.id/berita/1", "<title>Berita</title>"))


@pytest.mark.parametrize(
    ("url", "situs"),
    [
        (URL_ARTIKEL, "https://jurnal.komputasi.org/index.php/jst"),
        # Segmen bahasa setelah segmen jurnal dibuang...
        ("https://jurnal.komputasi.org/index.php/jst/en/article/view/28", "https://jurnal.komputasi.org/index.php/jst"),
        ("https://ejournal.contoh.ac.id/jistr/en_US/article/view/3", "https://ejournal.contoh.ac.id/jistr"),
        # ...tetapi jurnal ber-path dua huruf tetap utuh.
        ("https://ejournal.contoh.ac.id/index.php/ji/article/view/3", "https://ejournal.contoh.ac.id/index.php/ji"),
        ("https://ejournal.contoh.ac.id/ji/article/download/3/1", "https://ejournal.contoh.ac.id/ji"),
    ],
)
def test_situs_jurnal_dari_url_artikel_ojs(url, situs):
    assert situs_jurnal(halaman(url)) == situs


def test_situs_jurnal_dari_meta_dc_source_uri():
    page = halaman("https://contoh.ac.id/artikel/123", '<meta name="DC.Source.URI" content="https://contoh.ac.id/jurnal/">')
    assert situs_jurnal(page) == "https://contoh.ac.id/jurnal"


def test_situs_jurnal_dari_domain_situs():
    assert situs_jurnal(halaman("https://contoh.ac.id:8080/artikel/123")) == "https://contoh.ac.id:8080"


@pytest.mark.parametrize(
    "url",
    ["https://www.academia.edu/12345/Judul_Artikel", "https://garuda.kemdikbud.go.id/documents/detail/1"],
)
def test_situs_jurnal_none_untuk_agregator(url):
    assert situs_jurnal(halaman(url)) is None
