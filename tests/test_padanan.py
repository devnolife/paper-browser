from jurnal.padanan import BIDANG, PADANAN, kelompok_konsep, penanda_bidang
from jurnal.relevansi import kata_penting


def test_kelompok_konsep_terurut_dan_berisi_padanan():
    assert kelompok_konsep({"penjadwalan", "greedy"}) == [
        {"greedy"},
        {"penjadwalan", "scheduling", "schedule", "jadwal"},
    ]


def test_kelompok_konsep_kosong():
    assert kelompok_konsep(set()) == []


def test_penanda_bidang_dari_satu_bidang():
    assert penanda_bidang({"algoritma"}) == BIDANG["komputer"][1]


def test_penanda_bidang_menggabungkan_semua_bidang_yang_dipicu():
    # "penjadwalan" memicu bidang komputer sekaligus teknik.
    assert {"informatika", "engineering"} <= penanda_bidang({"penjadwalan"})


def test_penanda_bidang_kata_tak_dikenal():
    assert penanda_bidang({"xyzabc"}) == set()


def test_semua_entri_berupa_satu_token_yang_bisa_dicocokkan():
    """Pencocokan memakai token dari `kata_penting()`, jadi entri berspasi, berhuruf besar, atau berupa stopword
    tidak akan pernah cocok."""
    entri = set(PADANAN) | {p for padanan in PADANAN.values() for p in padanan}
    for pemicu, penanda in BIDANG.values():
        entri |= pemicu | penanda
    assert sorted(k for k in entri if kata_penting(k) != [k]) == []
