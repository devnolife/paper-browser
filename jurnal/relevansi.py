"""Skor relevansi sederhana antara kueri dan metadata artikel, tanpa dependensi tambahan."""

import re

from jurnal.padanan import kelompok_konsep

STOPWORD = frozenset(
    """
    ada adalah agar akan antara atas atau bagaimana bagi bahwa banyak beberapa belum berbagai berbasis bisa
    dalam dan dapat dari dengan di hal hingga ini itu juga jurnal ke kepada lebih maka masih melalui
    menggunakan menjadi menurut namun oleh pada para sebagai sebuah secara sehingga serta suatu tentang
    terhadap tersebut untuk yaitu yang analisis implementasi penerapan studi kasus
    a an and are as at by for from in into is of on or the to using with journal
    """.split()
)
# Kata yang sering ada di judul tetapi tidak menentukan topik. Dibuang dari kueri kotak cari OJS (yang menggabungkan
# kata dengan AND) dan tidak cukup sendirian untuk menganggap sebuah judul berkaitan.
KATA_UMUM = frozenset(
    """
    sistem metode aplikasi perancangan rancang bangun pengembangan berbasis model studi kasus teknik pengaruh hubungan
    faktor peran upaya peningkatan meningkatkan evaluasi tinjauan kajian desain baru algoritma data informasi web
    website android online digital teknologi pendekatan strategi
    """.split()
)


def kata_penting(teks: str | None) -> list[str]:
    """Kata bermakna (huruf kecil, tanpa stopword) sesuai urutan kemunculan, tanpa duplikat."""
    kata = re.findall(r"[^\W_]+", (teks or "").lower())
    return list(dict.fromkeys(k for k in kata if len(k) > 1 and k not in STOPWORD))


def _cocok(kelompok: list[set[str]], teks: str | None) -> int:
    kata = set(kata_penting(teks))
    return sum(1 for k in kelompok if k & kata)


def skor(kueri: set[str], artikel: dict) -> float:
    """0..1: porsi konsep kueri yang muncul di judul (bobot 0.6), kata kunci (0.25), dan abstrak (0.15).

    Satu konsep = kata kueri beserta padanannya (mis. penjadwalan ~ scheduling), karena kata kunci dan abstrak
    jurnal Indonesia sering berbahasa Inggris."""
    if not kueri:
        return 0.0
    kelompok = kelompok_konsep(kueri)

    def cakupan(teks):
        return _cocok(kelompok, teks) / len(kelompok)

    return round(
        0.6 * cakupan(artikel.get("judul"))
        + 0.25 * cakupan(" ".join(artikel.get("kata_kunci") or []))
        + 0.15 * cakupan(artikel.get("abstrak")),
        3,
    )


def berkaitan(kueri: set[str], artikel: dict) -> bool:
    """Judul/kata kunci artikel memuat kata kueri yang khas (bukan kata umum seperti "sistem" atau "algoritma"),
    atau judul, kata kunci, dan abstraknya bersama-sama memuat minimal dua konsep kueri (semuanya bila kueri hanya
    satu kata). Satu kata kueri yang hanya muncul di abstrak sering kebetulan, mis. "greedy" (serakah) di artikel
    studi agama."""
    if not kueri:
        return False
    utama = set(kata_penting(" ".join([artikel.get("judul") or "", *(artikel.get("kata_kunci") or [])])))
    if any(kelompok_konsep({t})[0] & utama for t in kueri if t not in KATA_UMUM):
        return True
    semua = utama | set(kata_penting(artikel.get("abstrak")))
    return sum(1 for k in kelompok_konsep(kueri) if k & semua) >= min(2, len(kueri))
