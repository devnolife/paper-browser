from jurnal.relevansi import berkaitan, kata_penting, skor


def test_kata_penting_huruf_kecil_tanpa_stopword_dan_duplikat():
    assert kata_penting("Analisis Algoritma Greedy untuk Penjadwalan, algoritma") == [
        "algoritma",
        "greedy",
        "penjadwalan",
    ]


def test_kata_penting_membuang_kata_satu_huruf_dan_garis_bawah():
    assert kata_penting("a b_c x1 C++") == ["x1"]


def test_kata_penting_teks_kosong():
    assert kata_penting(None) == []
    assert kata_penting("") == []


def test_skor_kueri_kosong():
    assert skor(set(), {"judul": "Algoritma Greedy"}) == 0.0


def test_skor_semua_konsep_di_judul_kata_kunci_dan_abstrak():
    artikel = {
        "judul": "Penjadwalan Kuliah dengan Algoritma Greedy",
        "kata_kunci": ["greedy", "scheduling"],
        "abstrak": "Penelitian ini membahas penjadwalan kuliah memakai algoritma greedy.",
    }
    assert skor({"greedy", "penjadwalan"}, artikel) == 1.0


def test_skor_sebagian_konsep_di_judul():
    assert skor({"greedy", "penjadwalan"}, {"judul": "Algoritma Greedy"}) == 0.3


def test_skor_memakai_padanan_bahasa_inggris():
    artikel = {"judul": "Course Timetabling", "kata_kunci": ["Scheduling"], "abstrak": "A scheduling approach."}
    assert skor({"penjadwalan"}, artikel) == 0.4


def test_berkaitan_bila_kata_khas_ada_di_judul():
    assert berkaitan({"algoritma", "greedy"}, {"judul": "Penerapan Greedy pada Penjadwalan"})


def test_berkaitan_lewat_padanan_di_kata_kunci():
    assert berkaitan({"penjadwalan"}, {"judul": "Course Timetabling", "kata_kunci": ["scheduling"]})


def test_tidak_berkaitan_bila_judul_hanya_memuat_kata_umum():
    assert not berkaitan({"sistem", "greedy"}, {"judul": "Sistem Informasi Akademik"})


def test_tidak_berkaitan_bila_kata_kueri_kebetulan_ada_di_abstrak():
    artikel = {"judul": "Etika Harta dalam Islam", "abstrak": "Sifat greedy dan tamak dikritik dalam ajaran agama."}
    assert not berkaitan({"algoritma", "greedy"}, artikel)


def test_berkaitan_bila_dua_konsep_tersebar_di_judul_dan_abstrak():
    artikel = {"judul": "Sistem Diagnosa Penyakit Tanaman", "abstrak": "An expert system for plant disease diagnosis."}
    assert berkaitan({"sistem", "pakar"}, artikel)


def test_kueri_satu_kata_cukup_muncul_di_abstrak():
    assert berkaitan({"greedy"}, {"judul": "Studi Lain", "abstrak": "Metode greedy dipakai di sini."})


def test_kueri_kosong_tidak_berkaitan():
    assert not berkaitan(set(), {"judul": "Algoritma Greedy"})
