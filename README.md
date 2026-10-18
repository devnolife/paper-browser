# paper-browser

**Paper Browser** adalah alat lokal untuk mencari artikel jurnal Indonesia yang berkaitan dengan sebuah topik,
judul, atau artikel, tanpa Google Scholar. Kata kunci dicari langsung lewat fitur cari situs-situs jurnal
(Open Journal Systems) yang tercatat di katalog; Bing dan Brave hanya cadangan. Setiap artikel diverifikasi dari
metadata halamannya, dikelompokkan per jurnal beserta link website-nya, lalu dicek apakah PDF-nya benar-benar bisa
diunduh.

## Fitur

- **Katalog situs jurnal**: ±2.700 jurnal Indonesia dari DOAJ, bertambah otomatis dari hasil pencarian dan dari
  jurnal lain di situs OJS yang sama, dan bisa ditambah manual.
- **Pencarian langsung ke situs jurnal**: 100 jurnal paling sesuai dipilih, kata kunci dikirim ke kotak cari OJS dan
  dilonggarkan bertahap bila hasilnya kurang.
- **Mode sumber**: `katalog_dulu` (bawaan; Bing/Brave dipakai bila artikel relevan kurang dari 10), `katalog_saja`,
  `gabung`, dan `mesin` (Bing/Brave saja).
- **Relevansi**: skor dari judul, kata kunci, dan abstrak, termasuk padanan kata Indonesia–Inggris.
- **Analisis PDF**: deteksi tombol "Download PDF" dan pengecekan bahwa tautannya benar-benar berkas PDF.
- **Antarmuka web dan CLI**, dengan ekspor JSON dan Markdown.

## Instalasi

Diuji dengan Python 3.13 di Windows.

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\scrapling.exe install
```

Di Linux/macOS, pakai `.venv/bin/python` dan `.venv/bin/scrapling install`.

## Pemakaian

Isi katalog sekali (mengunduh ±25 MB daftar jurnal dari DOAJ):

```powershell
.venv\Scripts\python.exe katalog_jurnal.py impor-doaj
```

Jalankan antarmuka web, lalu buka http://127.0.0.1:8000:

```powershell
.venv\Scripts\python.exe web_jurnal.py
```

Atau pakai CLI. Hasil pencarian disimpan di `hasil/` sebagai JSON dan Markdown; lihat `--help` untuk semua opsi.

```powershell
.venv\Scripts\python.exe cari_jurnal.py "algoritma greedy penjadwalan"
.venv\Scripts\python.exe cari_jurnal.py --dari-url https://jurnal.komputasi.org/index.php/jst/article/view/28
.venv\Scripts\python.exe cari_jurnal.py "sistem pakar" --situs jurnal.komputasi.org --mode katalog_saja
.venv\Scripts\python.exe katalog_jurnal.py tambah https://jurnal.komputasi.org/index.php/jst
.venv\Scripts\python.exe katalog_jurnal.py daftar --cari informatika
.venv\Scripts\python.exe buka_jurnal.py https://jurnal.komputasi.org/index.php/jst/article/view/28
```

## Cara kerja

Arsitekturnya *federated search*: satu kueri dikirim ke banyak koleksi (situs jurnal), lalu hasilnya digabung dan
diperingkat.

| Tahap | Metode | Modul |
|---|---|---|
| Kumpulkan situs jurnal | *Resource description*: DOAJ + jurnal se-situs (indeks OJS, OAI-PMH) | `katalog.py`, `ojs.py` |
| Pilih 100 jurnal | *Resource selection*: bobot IDF + *query expansion* padanan Indonesia–Inggris + riwayat temuan | `katalog.py`, `padanan.py` |
| Kirim kata kunci | Kueri Boolean AND + *query relaxation* (3 → 2 → 1 kata) | `ojs.py` |
| Gabung hasil | *Result merging*: urut skor judul, selang-seling per jurnal | `ojs.py` |
| Ambil data artikel | *Web scraping* meta `citation_*` dan Dublin Core | `metadata.py` |
| Peringkat dan saring | *Weighted Zone Scoring* (0,6 judul + 0,25 kata kunci + 0,15 abstrak) + aturan relevansi | `relevansi.py` |
| Cek PDF | Deteksi berbasis aturan + cek tanda berkas `%PDF-` | `pdf.py` |

## Etika dan batasan

- Satu situs jurnal menerima paling banyak satu permintaan sekaligus saat dicari (dua saat membuka artikel). Hasil
  disimpan 24 jam, dan situs yang menolak (HTTP 403/429/503) dilewati selama 24 jam.
- Captcha tidak pernah dikerjakan otomatis. Bila Bing atau Brave meminta verifikasi, jendelanya dibuka dan Anda
  menyelesaikannya sendiri; tanpa verifikasi, mesin itu diistirahatkan 30 menit.
- Data lokal tidak ikut ke repo: katalog dan cache (`.data/`), profil browser yang berisi cookie (`.profil-*/`), dan
  keluaran pencarian (`hasil/`).

## Struktur

```
jurnal/             pustaka inti
  katalog.py        katalog situs jurnal (SQLite) dan impor DOAJ
  ojs.py            pencarian langsung di situs OJS, penemuan jurnal se-situs
  terkait.py        alur pencarian lengkap: mode, verifikasi artikel, cek PDF
  pencarian.py      pencarian Bing/Brave
  kendali.py        cache, jeda, dan batas permintaan mesin pencari
  metadata.py       metadata artikel
  relevansi.py      skor dan penyaring relevansi
  padanan.py        padanan kata Indonesia–Inggris dan penanda bidang
  pdf.py            deteksi tombol dan verifikasi PDF
web/index.html      antarmuka web
web_jurnal.py       server web lokal (hanya 127.0.0.1)
cari_jurnal.py      CLI pencarian
katalog_jurnal.py   CLI katalog
buka_jurnal.py      buka dan analisis satu artikel
```
