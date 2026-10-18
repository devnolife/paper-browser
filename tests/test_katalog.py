import pytest

from jurnal.katalog import (
    BLOKIR_DETIK,
    GAGAL_MAKS,
    TTL_CACHE,
    Katalog,
    baca_csv_doaj,
    beranda_jurnal,
    dasar_instalasi,
    impor_doaj,
)

CSV_DOAJ = (
    "Journal title,Journal URL,Alternative title,Keywords,Subjects,Country of publisher\n"
    "Jurnal Informatika,https://a.ac.id/index.php/jik,JIK,\"informatika, algoritma\","
    "\"Science: Mathematics | Technology: Electronic computers\",Indonesia\n"
    "Jurnal Tetangga,https://contoh.my/index.php/jt,,law,Law,Malaysia\n"
)


@pytest.fixture
def katalog(jam):
    with Katalog(":memory:", jam=jam) as k:
        yield k


@pytest.mark.parametrize(
    ("url", "beranda"),
    [
        ("https://jurnal.komputasi.org/index.php/jst/article/view/28", "https://jurnal.komputasi.org/index.php/jst"),
        ("jurnal.komputasi.org/index.php/jst", "https://jurnal.komputasi.org/index.php/jst"),
        ("HTTPS://Jurnal.Komputasi.ORG/index.php/jst/", "https://jurnal.komputasi.org/index.php/jst"),
        ("https://jurnal.komputasi.org/index.php/jst/en/about", "https://jurnal.komputasi.org/index.php/jst"),
        ("https://ejournal.contoh.ac.id/jistr/article/view/5", "https://ejournal.contoh.ac.id/jistr"),
        ("https://ejournal.contoh.ac.id/jistr/en_US/about", "https://ejournal.contoh.ac.id/jistr"),
        ("https://ejournal.contoh.ac.id/ji", "https://ejournal.contoh.ac.id/ji"),
        ("http://203.0.113.5:8080/index.php/jurnal", "http://203.0.113.5:8080/index.php/jurnal"),
    ],
)
def test_beranda_jurnal(url, beranda):
    assert beranda_jurnal(url) == beranda


@pytest.mark.parametrize(
    "url",
    ["", "   ", "bukan url", "https://", "localhost/jurnal", "https://jurnal.komputasi.org/index.php/index"],
)
def test_beranda_jurnal_tidak_valid(url):
    assert beranda_jurnal(url) is None


@pytest.mark.parametrize(
    ("beranda", "dasar"),
    [
        ("https://jurnal.komputasi.org/index.php/jst", "https://jurnal.komputasi.org/index.php"),
        ("https://ejournal.contoh.ac.id/jistr", "https://ejournal.contoh.ac.id"),
        ("https://contoh.ac.id/ojs/jistr", "https://contoh.ac.id/ojs"),
        ("https://jurnal.contoh.ac.id", "https://jurnal.contoh.ac.id"),
    ],
)
def test_dasar_instalasi(beranda, dasar):
    assert dasar_instalasi(beranda) == dasar


def test_tambah_menormalkan_url_tanpa_menggandakan_jurnal(katalog):
    beranda, baru = katalog.tambah(
        "https://jurnal.komputasi.org/index.php/jst/article/view/28", "Jurnal  Sains ", ["algoritma"]
    )
    assert (beranda, baru) == ("https://jurnal.komputasi.org/index.php/jst", True)
    assert katalog.tambah("jurnal.komputasi.org/index.php/jst/about", kata_kunci=["greedy"]) == (beranda, False)
    assert katalog.jumlah_jurnal() == 1
    jurnal = katalog.jurnal(beranda)
    assert jurnal["nama"] == "Jurnal Sains"
    assert jurnal["dasar"] == "https://jurnal.komputasi.org/index.php"


def test_tambah_url_tidak_valid(katalog):
    assert katalog.tambah("bukan url") == (None, False)
    assert katalog.jumlah_jurnal() == 0


def test_pilih_jurnal_yang_cocok_konsep_dan_bidang(katalog):
    katalog.tambah("https://a.ac.id/index.php/informatika", "Jurnal Informatika", ["scheduling", "algorithm"])
    katalog.tambah("https://b.ac.id/index.php/hukum", "Jurnal Hukum", ["law"])
    assert [j["nama"] for j in katalog.pilih({"penjadwalan"})] == ["Jurnal Informatika"]


def test_riwayat_temuan_menaikkan_peringkat(katalog):
    satu, _ = katalog.tambah("https://a.ac.id/index.php/satu", "Jurnal Satu", ["scheduling"])
    dua, _ = katalog.tambah("https://b.ac.id/index.php/dua", "Jurnal Dua", ["scheduling"])
    assert [j["beranda"] for j in katalog.pilih({"penjadwalan"})] == [satu, dua]
    katalog.catat_temuan({dua: 2}, {"penjadwalan"})
    assert [j["beranda"] for j in katalog.pilih({"penjadwalan"})] == [dua, satu]


def test_pilih_melewati_instalasi_diblokir_sampai_masa_blokir_habis(katalog, jam):
    beranda, _ = katalog.tambah("https://a.ac.id/index.php/informatika", "Jurnal Informatika", ["scheduling"])
    katalog.tandai_instalasi("https://a.ac.id/index.php", status="diblokir")
    assert katalog.pilih({"penjadwalan"}) == []
    jam.sekarang += BLOKIR_DETIK
    assert [j["beranda"] for j in katalog.pilih({"penjadwalan"})] == [beranda]


def test_pilih_melewati_jurnal_yang_gagal_berturut_turut(katalog):
    beranda, _ = katalog.tambah("https://a.ac.id/index.php/informatika", "Jurnal Informatika", ["scheduling"])
    for _ in range(GAGAL_MAKS - 1):
        katalog.tandai_jurnal(beranda, "gagal")
    assert len(katalog.pilih({"penjadwalan"})) == 1
    katalog.tandai_jurnal(beranda, "gagal")
    assert katalog.pilih({"penjadwalan"}) == []


def test_cache_ojs_kedaluwarsa_setelah_ttl(katalog, jam):
    url = "https://a.ac.id/index.php/satu/search/search?query=greedy"
    katalog.simpan_cache(url, [{"judul": "Greedy"}])
    jam.sekarang += TTL_CACHE - 1
    assert katalog.ambil_cache(url) == [{"judul": "Greedy"}]
    jam.sekarang += 1
    assert katalog.ambil_cache(url) is None


def test_baca_csv_doaj_hanya_negara_yang_diminta():
    assert baca_csv_doaj(CSV_DOAJ) == [
        (
            "https://a.ac.id/index.php/jik",
            "Jurnal Informatika",
            ["informatika", "algoritma", "Science", "Mathematics", "Technology", "Electronic computers", "JIK"],
        )
    ]


def test_impor_doaj_dari_teks_csv_tanpa_mengunduh(katalog):
    assert impor_doaj(katalog, teks_csv=CSV_DOAJ) == {"jurnal_indonesia": 1, "baru": 1, "diperbarui": 0, "dilewati": 0}
    assert katalog.jurnal("https://a.ac.id/index.php/jik")["sumber"] == "doaj"
