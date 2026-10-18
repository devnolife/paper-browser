"""Cari artikel jurnal yang judulnya berkaitan dengan sebuah topik atau artikel, tanpa Google Scholar.

Bawaannya kata kunci langsung diketik ke fitur cari bawaan situs-situs jurnal (OJS) yang tercatat di katalog
(`katalog_jurnal.py`), mulai dari 100 jurnal yang paling sesuai; Bing/Brave hanya dipakai bila artikel relevan
dari katalog kurang dari 10 (lihat --mode). Tiap kandidat lalu dibuka lewat HTTP dan hanya halaman artikel
jurnal sungguhan (punya meta `citation_title`) yang disimpan, diurutkan menurut kemiripannya dengan kueri, lalu
dikelompokkan per jurnal beserta link website-nya. Tiap artikel juga dianalisis: adakah tombol unduh PDF di
halamannya, dan apakah tautannya benar-benar mengembalikan berkas PDF. Jurnal tempat artikel ditemukan dicatat
di katalog sehingga pencarian berikutnya makin tepat.

Supaya IP tidak ditandai sebagai bot, hasil pencarian disimpan di cache 24 jam, satu situs jurnal tidak pernah
menerima dua permintaan sekaligus, dan permintaan ke tiap mesin pencari diberi jeda. Bila Bing atau Brave tetap
meminta verifikasi, jendelanya dibuka agar Anda sendiri menyelesaikannya; tanpa verifikasi, mesin itu
diistirahatkan sementara dan mesin lain tetap dipakai.

Contoh:
    .venv\\Scripts\\python.exe katalog_jurnal.py impor-doaj          (sekali, mengisi katalog)
    .venv\\Scripts\\python.exe cari_jurnal.py "algoritma greedy penjadwalan"
    .venv\\Scripts\\python.exe cari_jurnal.py --dari-url https://jurnal.komputasi.org/index.php/jst/article/view/28
    .venv\\Scripts\\python.exe cari_jurnal.py "sistem pakar" --situs jurnal.komputasi.org
    .venv\\Scripts\\python.exe cari_jurnal.py "sistem pakar" --mode mesin   (cara lama: Bing/Brave saja)
"""

import argparse
import json
import logging
import re
import sys
from datetime import datetime
from pathlib import Path

from jurnal.pdf import ringkas_pdf
from jurnal.pencarian import MESIN
from jurnal.terkait import AMBANG_RELEVAN, MODE, TUNGGU_VERIFIKASI, InputTidakValid, cari_terkait

PENJELASAN_MODE = {
    "katalog_dulu": f"situs jurnal di katalog dulu; Bing/Brave bila artikel relevan < {AMBANG_RELEVAN} (bawaan)",
    "katalog_saja": "hanya situs jurnal di katalog",
    "gabung": "situs jurnal di katalog + Bing/Brave sekaligus",
    "mesin": "hanya Bing/Brave (cara lama)",
}


def ringkas(artikel: dict) -> str:
    penulis = ", ".join(p["nama"] for p in artikel["penulis"])
    tahun = (artikel["tanggal_terbit"] or "")[:4]
    return " · ".join(x for x in (penulis, tahun) if x)


def teks_jurnal(artikel: dict) -> str:
    nama, situs = artikel.get("jurnal") or "(nama jurnal tidak ada)", artikel.get("situs_jurnal")
    return f"{nama} — {situs}" if situs else nama


def md_jurnal(nama: str | None, situs: str | None) -> str:
    nama = nama or "(nama jurnal tidak ada)"
    return f"[{nama}]({situs})" if situs else nama


def ringkasan_pdf(stat: dict) -> str:
    return (
        f"{stat['punya_tombol_pdf']} dari {stat['ditampilkan']} artikel punya tombol unduh PDF, "
        f"{stat['pdf_bisa_diunduh']} PDF benar-benar bisa diunduh"
    )


def ringkasan_sumber(stat: dict) -> str:
    """Hasil tiap sumber pencarian dalam satu baris, mis. "katalog 68 hasil · bing dilewati (...)"."""
    bagian = []
    for nama, s in stat["mesin"].items():
        if s["status"] == "ok":
            bagian.append(f"{nama} {s['hasil']} hasil" + (" (cache)" if s["dari_cache"] else ""))
        else:
            bagian.append(f"{nama} {s['status']}" + (f" ({s['pesan']})" if s["pesan"] else ""))
    return " · ".join(bagian)


def ringkasan_katalog(k: dict) -> str | None:
    """Rincian pencarian langsung di situs jurnal dan pertumbuhan katalog; None bila katalog tidak dipakai."""
    tumbuh = k.get("jurnal_baru", 0) + k.get("jurnal_dipelajari", 0)
    akhir = (
        f"katalog kini {k['jurnal_di_katalog']} jurnal (+{tumbuh} dari hasil ini, "
        f"+{k['tetangga']['jurnal_baru']} dari {k['tetangga']['instalasi_dicek']} situs se-kampus)"
    )
    if k.get("status") in ("kosong", "gagal"):
        return f"{k['pesan']}; {akhir}"
    if k.get("status") != "ok":
        return akhir
    rincian = f"{k['permintaan']} permintaan, {k['dari_cache']} dari cache, {k['durasi_detik']} dtk"
    ditanya = (
        f"{k['jurnal_dipilih']} jurnal ditanya ({k['instalasi']} situs, {rincian})"
        if k["jurnal_dipilih"]
        else f"{k['instalasi']} situs ditanya ({rincian})"
    )
    teks = f"{k['hasil']} hasil dari {k['situs_memberi_hasil']} situs jurnal; {ditanya}"
    if kata := " → ".join(p["kata"] for p in k["putaran"]):
        teks += f"; kata kunci: {kata}"
    if k["diblokir"] or k["gagal"] or k["terlalu_lambat"]:
        teks += f"; {k['diblokir']} situs menolak, {k['gagal']} tak terjangkau, {k['terlalu_lambat']} terlalu lambat"
    return f"{teks}; {akhir}"


def baris_pdf_md(pdf: dict) -> str:
    teks = ringkas_pdf(pdf)
    return f"   PDF: [{teks}]({pdf['url_unduh']})" if pdf["status"] == "pdf" else f"   PDF: {teks}"


def ke_markdown(laporan: dict) -> str:
    stat = laporan["statistik"]
    baris = [
        f"# Judul terkait: {laporan['kueri']}",
        "",
        f"_Dicari lewat {', '.join(laporan['mesin']) or '-'} (mode {laporan['mode']}) pada {laporan['waktu']}_",
        "",
        f"**Sumber:** {ringkasan_sumber(stat)}.",
        "",
    ]
    if katalog := ringkasan_katalog(stat["katalog"]):
        baris += [f"**Katalog:** {katalog}.", ""]
    baris += [
        f"**PDF:** {ringkasan_pdf(stat)}.",
        "",
        f"## Jurnal ditemukan ({len(laporan['jurnal'])})",
        "",
        *(f"- {md_jurnal(j['nama'], j['situs'])} — {j['jumlah_artikel']} artikel" for j in laporan["jurnal"]),
        "",
        f"## Artikel ({len(laporan['artikel'])})",
        "",
    ]
    if sumber := laporan.get("artikel_sumber"):
        baris += [f"Artikel sumber: [{sumber['judul']}]({sumber['url']})", baris_pdf_md(sumber), ""]
    for i, a in enumerate(laporan["artikel"], 1):
        baris.append(f"{i}. **[{a['judul']}]({a['url']})** — skor {a['skor']:.2f}, ditemukan lewat {', '.join(a['ditemukan_oleh'])}")
        if info := ringkas(a):
            baris.append(f"   {info}")
        baris.append(f"   Jurnal: {md_jurnal(a.get('jurnal'), a.get('situs_jurnal'))}")
        baris.append(baris_pdf_md(a["analisis_pdf"]))
    return "\n".join(baris) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Cari judul artikel jurnal yang berkaitan langsung di situs-situs jurnal (katalog) dan/atau "
        "lewat Bing/Brave + Scrapling (tanpa Google Scholar)."
    )
    sumber = parser.add_mutually_exclusive_group(required=True)
    sumber.add_argument("kueri", nargs="?", help="topik atau judul yang dicari")
    sumber.add_argument("--dari-url", metavar="URL", help="cari judul yang berkaitan dengan artikel di URL ini")
    parser.add_argument(
        "--situs", metavar="DOMAIN", help="fokus ke satu situs, mis. jurnal.komputasi.org (dicari langsung di situsnya)"
    )
    parser.add_argument(
        "--mode",
        choices=MODE,
        default="katalog_dulu",
        help="sumber pencarian: " + "; ".join(f"{m} = {p}" for m, p in PENJELASAN_MODE.items()),
    )
    parser.add_argument(
        "--jurnal-maks",
        type=int,
        default=100,
        metavar="N",
        help="jumlah jurnal katalog paling sesuai yang ditanya, 1-1000 (default: 100)",
    )
    parser.add_argument(
        "--mesin",
        default=",".join(MESIN),
        help=f"mesin pencari dipisah koma, dipakai berurutan {' lalu '.join(MESIN)} (default: {','.join(MESIN)})",
    )
    parser.add_argument("--halaman", type=int, default=1, help="jumlah halaman hasil per mesin (default: 1)")
    parser.add_argument("--maks", type=int, default=30, help="maksimal kandidat yang dibuka (default: 30)")
    parser.add_argument(
        "--tampilkan",
        action="store_true",
        help="tampilkan jendela browser dan siap memverifikasi sekarang (masa istirahat mesin diabaikan); "
        f"tantangan ditunggu {TUNGGU_VERIFIKASI} detik untuk Anda selesaikan sendiri",
    )
    parser.add_argument(
        "--non-interaktif",
        action="store_true",
        help="jangan pernah membuka jendela verifikasi; mesin yang meminta captcha langsung diistirahatkan",
    )
    parser.add_argument(
        "--segarkan", action="store_true", help="abaikan cache hasil pencarian (24 jam) dan cari ulang ke mesin"
    )
    parser.add_argument(
        "--cepat",
        action="store_true",
        help="jangan buka ulang lewat browser halaman yang diblokir/gagal SSL (lebih cepat, hasil bisa lebih sedikit)",
    )
    parser.add_argument("--out", default="hasil", help="folder output (default: hasil)")
    parser.add_argument("-v", "--verbose", action="store_true", help="tampilkan log Scrapling")
    args = parser.parse_args()

    mesin = [m.strip().lower() for m in args.mesin.split(",") if m.strip()]
    if not mesin or any(m not in MESIN for m in mesin):
        parser.error(f"--mesin hanya boleh berisi: {', '.join(MESIN)}")
    if args.halaman < 1 or args.maks < 1:
        parser.error("--halaman dan --maks minimal 1")
    if not 1 <= args.jurnal_maks <= 1000:
        parser.error("--jurnal-maks harus 1-1000")

    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    logging.getLogger("scrapling").setLevel(logging.INFO if args.verbose else logging.CRITICAL)

    try:
        laporan = cari_terkait(
            kueri=args.kueri,
            dari_url=args.dari_url,
            situs=args.situs,
            mesin=mesin,
            halaman=args.halaman,
            maks=args.maks,
            tampilkan=args.tampilkan,
            browser_cadangan=not args.cepat,
            interaktif=not args.non_interaktif,
            segarkan=args.segarkan,
            mode=args.mode,
            jurnal_maks=args.jurnal_maks,
            lapor=lambda pesan: print(pesan, file=sys.stderr),
        )
    except InputTidakValid as e:
        sys.exit(str(e))

    stat = laporan["statistik"]
    for nama, s in stat["mesin"].items():
        if s["status"] in ("captcha", "istirahat") and s["istirahat_sampai"]:
            sampai = datetime.fromisoformat(s["istirahat_sampai"])
            print(
                f"  Tip: {nama} diistirahatkan sampai {sampai:%H:%M}. "
                "Jalankan dengan --tampilkan untuk memverifikasinya sekarang.",
                file=sys.stderr,
            )
    if not stat["hasil_pencarian"]:
        saran = []
        if stat["katalog"].get("status") == "kosong":
            saran.append("isi katalog dulu: .venv\\Scripts\\python.exe katalog_jurnal.py impor-doaj")
        if args.mode != "katalog_saja":
            saran.append("coba lagi nanti, ganti --mesin, atau pakai --tampilkan")
        sys.exit(f"Tidak ada hasil pencarian ({ringkasan_sumber(stat)}). Saran: {'; '.join(saran) or 'coba kata lain'}.")

    artikel = laporan["artikel"]
    rincian = f"{stat['kandidat']} kandidat dibuka, {stat['gagal_dibuka']} gagal, {stat['tanpa_kata_kueri']} kurang berkaitan"
    if not artikel:
        sys.exit(f"Tidak ada artikel jurnal yang berkaitan ({rincian}).")

    print(f'\nJudul terkait "{laporan["kueri"]}": {len(artikel)} artikel dari {stat["jumlah_jurnal"]} jurnal ({rincian})')
    print(f"Sumber: {ringkasan_sumber(stat)}")
    if katalog := ringkasan_katalog(stat["katalog"]):
        print(f"Katalog: {katalog}")
    print(f"PDF: {ringkasan_pdf(stat)}\n")
    print(f"Jurnal ditemukan ({stat['jumlah_jurnal']}):")
    for j in laporan["jurnal"]:
        print(f"  - {j['nama']} ({j['jumlah_artikel']} artikel)" + (f"\n    {j['situs']}" if j["situs"] else ""))
    print()
    if sumber := laporan["artikel_sumber"]:
        print(f"Artikel sumber: {sumber['judul']}\n    PDF: {ringkas_pdf(sumber['analisis_pdf'])}\n")
    for i, a in enumerate(artikel, 1):
        print(f"{i:>2}. [{a['skor']:.2f}] {a['judul']}")
        if info := ringkas(a):
            print(f"    {info}")
        print(f"    {a['url']}  (lewat {', '.join(a['ditemukan_oleh'])})")
        print(f"    Jurnal: {teks_jurnal(a)}")
        print(f"    PDF: {ringkas_pdf(a['analisis_pdf'])}")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    nama_file = "cari-" + (re.sub(r"[^a-z0-9]+", "-", laporan["kueri"].lower()).strip("-")[:60].rstrip("-") or "hasil")
    (out / f"{nama_file}.json").write_text(json.dumps(laporan, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / f"{nama_file}.md").write_text(ke_markdown(laporan), encoding="utf-8")
    print(f"\nTersimpan: {(out / nama_file).resolve()}.json dan .md")


if __name__ == "__main__":
    main()
