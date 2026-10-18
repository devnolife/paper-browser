import pytest

from jurnal.kendali import BATAS_PER_JAM, ISTIRAHAT_CAPTCHA, JEDA, TTL_CACHE, Kendali, MesinIstirahat


@pytest.fixture
def kendali(jam):
    with Kendali(":memory:", jam=jam, tidur=jam.tidur) as k:
        yield k


def test_cache_memakai_kueri_ternormalisasi_dan_kedaluwarsa(kendali, jam):
    kendali.simpan_cache("bing", "  Algoritma   GREEDY ", 1, [{"url": "https://a.ac.id"}])
    assert kendali.ambil_cache("bing", "algoritma greedy", 1) == [{"url": "https://a.ac.id"}]
    assert kendali.ambil_cache("bing", "algoritma greedy", 2) is None
    assert kendali.ambil_cache("brave", "algoritma greedy", 1) is None
    jam.sekarang += TTL_CACHE
    assert kendali.ambil_cache("bing", "algoritma greedy", 1) is None


def test_tunggu_giliran_memberi_jeda_antar_permintaan_ke_mesin_yang_sama(kendali, jam):
    kendali.tunggu_giliran("bing")
    kendali.tunggu_giliran("brave")
    assert jam.jeda == []
    kendali.tunggu_giliran("bing")
    assert jam.jeda == [JEDA["bing"]]


def test_batas_permintaan_per_jam(kendali, jam):
    awal, batas = jam.sekarang, BATAS_PER_JAM["brave"]
    for _ in range(batas):
        kendali.tunggu_giliran("brave")
    with pytest.raises(MesinIstirahat) as info:
        kendali.tunggu_giliran("brave")
    assert info.value.sampai == awal + 3600
    jam.sekarang = info.value.sampai
    kendali.tunggu_giliran("brave")


def test_istirahat_setelah_captcha_berakhir_sendiri(kendali, jam):
    sampai = kendali.istirahatkan("brave", "captcha")
    assert sampai == jam.sekarang + ISTIRAHAT_CAPTCHA
    assert kendali.istirahat_sampai("brave") == (sampai, "captcha")
    with pytest.raises(MesinIstirahat):
        kendali.cek_boleh("brave")
    kendali.cek_boleh("bing")
    jam.sekarang = sampai
    kendali.cek_boleh("brave")


def test_pulihkan_mengakhiri_istirahat(kendali):
    kendali.istirahatkan("brave", "captcha")
    kendali.pulihkan("brave")
    assert kendali.istirahat_sampai("brave") is None
    kendali.cek_boleh("brave")
