"""Buka halaman artikel jurnal OJS dengan Scrapling, tampilkan metadata dan analisis tombol unduh PDF-nya, lalu simpan.

Di mode browser, tombol PDF yang terdeteksi ditandai garis merah di screenshot.

Contoh:
    .venv\\Scripts\\python.exe buka_jurnal.py
    .venv\\Scripts\\python.exe buka_jurnal.py <url-artikel> --http
"""

import argparse
import json
import sys
import textwrap
from pathlib import Path

from scrapling.fetchers import Fetcher, StealthyFetcher
from scrapling.parser import Selector

from jurnal.metadata import ambil_metadata
from jurnal.pdf import analisis_tombol_pdf, cek_pdf, cek_pdf_di_halaman, status_pdf

DEFAULT_URL = "https://jurnal.komputasi.org/index.php/jst/article/view/28"
ARTICLE_SELECTOR = "article.obj_article_details"
_JS_TANDAI = """(urls) => {
  let jumlah = 0;
  for (const a of document.querySelectorAll("a[href]")) {
    if (urls.includes(a.href.split("#")[0])) {
      a.style.outline = "3px solid #e11d48";
      a.style.outlineOffset = "3px";
      jumlah += 1;
    }
  }
  return jumlah;
}"""


def tampilkan(data: dict) -> None:
    penulis = "; ".join(
        f"{p['nama']} ({', '.join(p['institusi'])})" if p["institusi"] else p["nama"] for p in data["penulis"]
    )
    terbitan = ", ".join(
        bagian
        for bagian in (
            data["jurnal"],
            data["volume"] and f"Vol. {data['volume']}",
            data["nomor"] and f"No. {data['nomor']}",
            data["halaman"] and f"hlm. {data['halaman']}",
        )
        if bagian
    )
    pdf = data["analisis_pdf"]
    tombol = "; ".join(f'"{t["teks"]}" -> {t["url"]}' for t in pdf["tombol"]) or "tidak ditemukan"
    unduh = f"{status_pdf(pdf)} -> {pdf['url_unduh']}" if pdf["url_unduh"] else status_pdf(pdf)
    for label, nilai in (
        ("Judul", data["judul"]),
        ("Penulis", penulis),
        ("Terbitan", terbitan),
        ("Situs jurnal", data["situs_jurnal"]),
        ("Terbit", data["tanggal_terbit"]),
        ("Kata kunci", ", ".join(data["kata_kunci"])),
        ("DOI", data["doi"]),
        ("Tombol PDF", tombol),
        ("Unduh PDF", unduh),
    ):
        print(f"{label:<12}: {nilai or '-'}")
    if data["abstrak"]:
        print("\nAbstrak:")
        print(textwrap.fill(data["abstrak"], width=100))


def main() -> None:
    parser = argparse.ArgumentParser(description="Buka artikel jurnal OJS dengan Scrapling.")
    parser.add_argument("url", nargs="?", default=DEFAULT_URL, help="URL halaman artikel")
    parser.add_argument("--http", action="store_true", help="pakai HTTP biasa: lebih cepat, tanpa browser & screenshot")
    parser.add_argument("--tampilkan", action="store_true", help="tampilkan jendela browser (non-headless)")
    parser.add_argument("--out", default="hasil", help="folder output (default: hasil)")
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    screenshot = out / "screenshot.png"

    di_browser = {}

    def aksi(p):
        analisis = analisis_tombol_pdf(Selector(p.content(), url=p.url))
        di_browser["ditandai"] = p.evaluate(_JS_TANDAI, [t["url"] for t in analisis["tombol"]])
        p.screenshot(path=str(screenshot), full_page=True)
        if analisis["url_unduh"]:
            di_browser["cek"] = cek_pdf_di_halaman(p, analisis["url_unduh"])

    if args.http:
        page = Fetcher.get(args.url, impersonate="chrome", stealthy_headers=True, timeout=60)
    else:
        screenshot.unlink(missing_ok=True)
        page = StealthyFetcher.fetch(
            args.url,
            headless=not args.tampilkan,
            network_idle=True,
            timeout=60_000,
            page_action=aksi,
        )

    if page.status != 200:
        sys.exit(f"Gagal membuka {args.url}: HTTP {page.status}")

    data = ambil_metadata(page)
    analisis = analisis_tombol_pdf(page)
    if "cek" in di_browser:
        analisis.update(di_browser["cek"])
    elif analisis["url_unduh"]:
        analisis.update(cek_pdf(analisis["url_unduh"], referer=page.url))
    data["analisis_pdf"] = analisis
    tampilkan(data)

    (out / "metadata.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown = page.markdown(css_selector=ARTICLE_SELECTOR) or page.markdown(main_content_only=True)
    (out / "artikel.md").write_text(markdown, encoding="utf-8")

    disimpan = ["metadata.json", "artikel.md"]
    if not args.http and screenshot.exists():
        disimpan.append("screenshot.png" + (" (tombol PDF ditandai merah)" if di_browser.get("ditandai") else ""))
    print(f"\nTersimpan di {out.resolve()}: {', '.join(disimpan)}")


if __name__ == "__main__":
    main()
