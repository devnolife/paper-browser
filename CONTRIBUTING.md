# Panduan kontribusi

Terima kasih sudah ingin membantu Paper Browser.

## Bertanya atau melapor

- Pertanyaan cara pakai: [Discussions (Q&A)](https://github.com/devnolife/paper-browser/discussions/categories/q-a).
- Bug dan usulan fitur: [Issues](https://github.com/devnolife/paper-browser/issues/new/choose), memakai template
  yang tersedia.

## Mengubah kode

1. Fork repo ini, lalu buat branch dari `main`.
2. Siapkan lingkungan seperti di [README](README.md#instalasi), lalu pasang dependensi pengembangan:

   ```powershell
   .venv\Scripts\python.exe -m pip install -r requirements-dev.txt
   ```

3. Tambahkan atau perbarui tes di `tests/` untuk setiap perubahan perilaku. Tes tidak boleh memakai jaringan maupun
   browser: `Katalog` dan `Kendali` bisa diberi SQLite `:memory:` dan jam palsu (lihat `tests/conftest.py`).
4. Pastikan semua tes lulus:

   ```powershell
   .venv\Scripts\python.exe -m pytest
   ```

5. Buka pull request ke `main` dengan penjelasan singkat apa yang berubah dan alasannya. CI menjalankan tes di Ubuntu
   dan Windows.

## Hal yang perlu dijaga

- Ikuti gaya kode yang sudah ada, termasuk nama dan docstring berbahasa Indonesia.
- Pertahankan batasan di bagian [Etika dan batasan](README.md#etika-dan-batasan) pada README, misalnya jumlah
  permintaan sekaligus per situs jurnal dan captcha yang tidak pernah dikerjakan otomatis.
- Jangan meng-commit data lokal: `.data/`, `.profil-*/` (berisi cookie), dan `hasil/`.
