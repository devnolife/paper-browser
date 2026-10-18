"""Kelola katalog "tempat publish" jurnal yang dipakai `cari_jurnal.py` untuk mencari langsung di situs jurnal.

Katalog terisi dengan tiga cara: impor daftar jurnal Indonesia dari DOAJ, ditambah manual (URL beranda jurnal
atau URL artikel apa pun di situsnya), dan otomatis dari setiap pencarian (jurnal tempat artikel ditemukan, serta
jurnal lain di situs OJS yang sama). Disimpan di .data/katalog.sqlite3.

Contoh:
    .venv\\Scripts\\python.exe katalog_jurnal.py impor-doaj
    .venv\\Scripts\\python.exe katalog_jurnal.py tambah https://jurnal.komputasi.org/index.php/jst
    .venv\\Scripts\\python.exe katalog_jurnal.py tambah --berkas daftar_jurnal.txt
    .venv\\Scripts\\python.exe katalog_jurnal.py daftar --cari informatika
    .venv\\Scripts\\python.exe katalog_jurnal.py statistik
"""

import argparse
import sys
from pathlib import Path

from jurnal.katalog import Katalog, impor_doaj

NAMA_SUMBER = {"doaj": "DOAJ", "manual": "manual", "otomatis": "dari hasil pencarian", "tetangga": "se-situs OJS"}


def tampilkan_statistik(k: Katalog) -> None:
    s = k.statistik()
    print(f"Katalog: {s['jurnal']} jurnal di {s['instalasi']} situs")
    for sumber, n in sorted(s["per_sumber"].items(), key=lambda x: -x[1]):
        print(f"  - {NAMA_SUMBER.get(sumber, sumber)}: {n}")
    status = ", ".join(f"{st} {n}" for st, n in sorted(s["per_status"].items(), key=lambda x: -x[1]))
    print(f"Status jurnal: {status or '-'}")
    print(f"Pernah memberi hasil: {s['pernah_memberi_hasil']} jurnal; situs sedang dilewati (menolak): {s['instalasi_diblokir']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Kelola katalog situs jurnal untuk pencarian langsung.")
    sub = parser.add_subparsers(dest="perintah", required=True)
    sub.add_parser("impor-doaj", help="impor/perbarui jurnal Indonesia dari DOAJ (unduh ±25 MB, aman diulang)")
    p = sub.add_parser("tambah", help="tambah jurnal dari URL beranda atau URL artikelnya")
    p.add_argument("url", nargs="*", help="satu atau beberapa URL")
    p.add_argument("--berkas", type=Path, help="berkas teks berisi satu URL per baris (# untuk komentar)")
    p.add_argument("--nama", help="nama jurnal (hanya bila menambah satu URL)")
    p = sub.add_parser("daftar", help="tampilkan jurnal di katalog (yang paling sering memberi hasil di atas)")
    p.add_argument("--cari", help="saring menurut nama, alamat, atau kata kunci")
    p.add_argument("--batas", type=int, default=50, help="jumlah baris (default: 50)")
    sub.add_parser("statistik", help="ringkasan isi katalog")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

    with Katalog() as k:
        if args.perintah == "impor-doaj":
            try:
                h = impor_doaj(k, lapor=lambda pesan: print(pesan, file=sys.stderr))
            except Exception as e:
                sys.exit(f"Gagal mengimpor DOAJ: {type(e).__name__}: {e}")
            print(
                f"DOAJ: {h['jurnal_indonesia']} jurnal Indonesia; {h['baru']} baru, {h['diperbarui']} diperbarui, "
                f"{h['dilewati']} dilewati (alamat tidak valid)"
            )
            tampilkan_statistik(k)
        elif args.perintah == "tambah":
            urls = list(args.url)
            if args.berkas:
                teks = args.berkas.read_text(encoding="utf-8-sig")
                urls += [b.strip() for b in teks.splitlines() if b.strip() and not b.lstrip().startswith("#")]
            if not urls:
                parser.error("isi URL atau --berkas")
            if args.nama and len(urls) > 1:
                parser.error("--nama hanya untuk satu URL")
            hitung = {"baru": 0, "sudah ada": 0, "tidak valid": 0}
            for url in urls:
                beranda, baru = k.tambah(url, args.nama, sumber="manual")
                ket = "tidak valid" if not beranda else "baru" if baru else "sudah ada"
                hitung[ket] += 1
                print(f"  [{ket}] {beranda or url}")
            print(", ".join(f"{n} {ket}" for ket, n in hitung.items() if n))
        elif args.perintah == "daftar":
            daftar = k.daftar(args.cari, args.batas)
            for j in daftar:
                print(f"- {j['nama'] or '(nama belum diketahui)'}")
                print(f"  {j['beranda']}  [{NAMA_SUMBER.get(j['sumber'], j['sumber'])}, {j['status']}, {j['jumlah_temuan']} temuan]")
            if not daftar:
                print("Tidak ada jurnal yang cocok." if args.cari else "Katalog kosong; jalankan: katalog_jurnal.py impor-doaj")
        else:
            tampilkan_statistik(k)


if __name__ == "__main__":
    main()
