"""Cari artikel langsung lewat fitur cari bawaan OJS di situs-situs jurnal pada katalog, tanpa mesin pencari.

Satu instalasi OJS (biasanya satu kampus) bisa dicari sekaligus lewat `{dasar}/index/search/search`, jadi cukup satu
permintaan untuk semua jurnal di dalamnya. Permintaan ke satu host tidak pernah dijalankan bersamaan, hasil disimpan
24 jam, dan situs yang menolak (403/429/503) dilewati sementara.
"""

import re
import threading
import time
from collections import defaultdict
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from html import unescape as html_unescape
from urllib.parse import quote_plus, urljoin, urlparse

from curl_cffi import requests as curl
from scrapling.parser import Selector

from jurnal.katalog import Katalog, beranda_jurnal, dasar_instalasi
from jurnal.padanan import kelompok_konsep
from jurnal.pencarian import HasilCari, normalisasi_url
from jurnal.relevansi import KATA_UMUM, kata_penting

MAKS_KATA = 3
AMBANG_HASIL = 10  # bawaan `cukup`: kueri dilonggarkan (kata dikurangi) selama artikel terkumpul kurang dari ini
PARALEL = 32  # host berbeda yang dihubungi bersamaan; satu host tetap satu permintaan pada satu waktu
TIMEOUT = (6, 12)  # (sambung, baca) detik
BATAS_PUTARAN = 35  # detik maksimal satu putaran
BATAS_LUNAK = 12  # setelah ini, putaran diakhiri bila tinggal segelintir host lambat yang belum menjawab
BATAS_TOTAL = 80  # detik maksimal seluruh pencarian katalog
MAKS_JURNAL_PER_INSTALASI = 3  # bila cari se-instalasi tidak didukung, sebanyak ini jurnalnya dicari satu per satu
MAKS_JURNAL_SITUS = 10  # idem untuk mode fokus satu situs

_ARTIKEL = re.compile(r"/article/view/\d+/?$", re.I)  # halaman artikel, bukan galley /view/<id>/<galley>
_JUDUL_SAMPAH = re.compile(
    r"^(?:pdf|html|xml|epub|abstract|abstrak|full\s*text|view|lihat|download|unduh|read\s*more|selengkapnya"
    r"|doi\b.*|https?://\S+|10\.\d{4,}/\S+)$",
    re.I,
)
_WADAH = (".search_results", ".search-results", "#results", ".page_search", ".pkp_structure_main", "main", "#main-content", "#content")
_TANDA_OJS = re.compile(r"Open Journal Systems|/lib/pkp/|pkp_structure|pkp_page_|class=\"pkp", re.I)
# Token kelas/id bilah samping ("artikel terpopuler" dsb.). Dicocokkan per token agar `has_sidebar` milik pembungkus
# konten OJS tidak ikut tertangkap.
_SAMPING = re.compile(r"^(?:pkp_structure_sidebar|sidebar|side-bar|left-?sidebar|right-?sidebar|pkp_block|widget)$|^sidebar[_-]", re.I)
_SEGMEN_JURNAL = re.compile(r"^[A-Za-z0-9_-]{2,64}$")
_BUKAN_JURNAL = frozenset("index public plugins lib cache templates styles js api assets files images site".split())


def susun_kata(token_urut: list[str], maks: int = MAKS_KATA) -> list[str]:
    """Kata kunci untuk kotak cari OJS (digabung AND): kata khas dulu, kata umum hanya bila kata khas kurang dari dua."""
    token = [t for t in dict.fromkeys(token_urut) if len(t) > 2]
    khusus = [t for t in token if t not in KATA_UMUM]
    if len(khusus) < 2:
        khusus += [t for t in token if t not in khusus][: 2 - len(khusus)]
    return khusus[:maks]


def url_cari(alamat: str, kata: list[str]) -> str:
    """`alamat`: beranda jurnal, atau `{dasar}/index` untuk mencari se-instalasi."""
    return f"{alamat.rstrip('/')}/search/search?query={quote_plus(' '.join(kata))}"


def _host(url: str) -> str:
    return urlparse(url).netloc.lower()


def _ambil(url: str) -> tuple[str, str | None, str]:
    """(status, html, url akhir). Status: ok, diblokir, 404, galat."""
    try:
        r = curl.get(url, impersonate="chrome", timeout=TIMEOUT, allow_redirects=True, max_redirects=5)
    except Exception:
        return "galat", None, url
    if r.status_code in (401, 403, 429, 503):
        return "diblokir", None, r.url
    if r.status_code in (404, 410):
        return "404", None, r.url
    if r.status_code != 200:
        return "galat", None, r.url
    return "ok", r.text, r.url


def _judul_blok(a) -> str:
    """Judul dari blok hasil bila teks tautannya hanya "Abstract"/"PDF". Pada tabel OJS 2 judul ada di sel tanpa
    tautan (sel lain berisi tautan ke jurnal dan terbitan)."""
    for induk in a.iterancestors():
        if induk.tag == "tr":
            sel = [_teks(td, 300) for td in induk.children if td.tag in ("td", "th") and not td.css("a")]
            if sel := [s for s in sel if s]:
                return max(sel, key=len)
        kelas = (induk.attrib.get("class") or "").lower()
        if induk.tag in ("li", "tr", "article") or "summary" in kelas or "article" in kelas:
            teks = " ".join(induk.get_all_text(separator=" ", strip=True).split())
            teks = re.sub(r"\b(?:Abstract|Abstrak|PDF|HTML|Full\s*Text)\b", " ", teks, flags=re.I)
            return " ".join(teks.split())[:300]
    return ""


def _di_samping(a) -> bool:
    for x in a.iterancestors():
        if x.tag in ("nav", "aside"):
            return True
        if any(_SAMPING.match(t) for t in (x.attrib.get("class") or "").split() + [x.attrib.get("id") or ""]):
            return True
    return False


def urai_hasil(html: str, url: str) -> list[dict]:
    """Hasil [{judul, url}] dari halaman cari OJS (tema bawaan, OpenJournalTheme, OJS 2). URL artikel dinormalisasi."""
    page = Selector(html, url=url)
    wadah = next((w for sel in _WADAH if (w := page.css(sel).first)), page)
    hasil: dict[str, list[str]] = {}
    tautan_pertama = {}
    for a in wadah.css("a[href]"):
        href = urljoin(url, a.attrib.get("href", "").strip())
        if not href.startswith(("http://", "https://")) or not _ARTIKEL.search(urlparse(href).path):
            continue
        if _di_samping(a):
            continue
        artikel = normalisasi_url(href)
        if not artikel:
            continue
        teks = " ".join(a.get_all_text(separator=" ", strip=True).split())
        hasil.setdefault(artikel, [])
        tautan_pertama.setdefault(artikel, a)
        if teks and not _JUDUL_SAMPAH.match(teks):
            hasil[artikel].append(teks)
    return [
        {"judul": max(judul, key=len) if judul else _judul_blok(tautan_pertama[u]), "url": u} for u, judul in hasil.items()
    ]


def cari_halaman(url: str) -> tuple[str, list[dict]]:
    """Buka satu halaman cari OJS. Status: ok, diblokir, galat, tidak_didukung (404/bukan halaman cari), bukan_ojs."""
    status, html, akhir = _ambil(url)
    if status != "ok":
        return {"404": "tidak_didukung"}.get(status, status), []
    if not _TANDA_OJS.search(html):
        return "bukan_ojs", []
    if "/search" not in urlparse(akhir).path:
        return "tidak_didukung", []  # mis. dialihkan ke beranda atau halaman login
    return "ok", urai_hasil(html, akhir)


def _kerjakan_host(tugas: list[dict], kata: list[str], berhenti_bila_ok: bool, henti: threading.Event,
                   maks_jurnal: int) -> list[dict]:
    """Jalankan rencana satu host secara berurutan (tidak pernah dua permintaan bersamaan ke satu host).
    `tugas`: entri rencana {dasar, situs, jurnal}. Berhenti begitu `henti` diset oleh pemanggil."""
    keluaran = []
    for t in tugas:
        langkah = ([("situs", f"{t['dasar']}/index", None)] if t["situs"] else []) + [
            ("jurnal", b, b) for b in t["jurnal"][:maks_jurnal]
        ]
        for jenis, alamat, beranda in langkah:
            if henti.is_set():
                return keluaran
            url = url_cari(alamat, kata)
            status, hasil = cari_halaman(url)
            keluaran.append(
                {"jenis": jenis, "dasar": t["dasar"], "beranda": beranda, "url": url, "status": status, "hasil": hasil}
            )
            if status in ("diblokir", "galat"):
                return keluaran  # host menolak atau tak terjangkau: jangan diteruskan
            if status == "ok":
                if berhenti_bila_ok:
                    return keluaran
                if jenis == "situs":
                    break  # seluruh jurnal di instalasi ini sudah tercakup
    return keluaran


def _kunci_instalasi(dasar: str) -> str:
    return re.sub(r"^https?://", "", dasar.lower()).removesuffix("/index.php")


def _rencana(dipilih: list[dict]) -> list[dict]:
    """Kelompokkan jurnal terpilih per instalasi, urut peringkat. Alamat yang sama dengan/tanpa `index.php` atau
    http/https dianggap satu instalasi; yang dipakai https + index.php (selalu didukung OJS)."""
    rencana: dict[str, dict] = {}
    for j in dipilih:
        r = rencana.setdefault(_kunci_instalasi(j["dasar"]), {"dasar": j["dasar"], "cari_situs": j.get("cari_situs"), "jurnal": []})
        if (j["dasar"].startswith("https:"), j["dasar"].endswith("/index.php")) > (
            r["dasar"].startswith("https:"), r["dasar"].endswith("/index.php")
        ):
            r["dasar"] = j["dasar"]
        r["jurnal"].append(j["beranda"])
    for r in rencana.values():
        skema = r["dasar"].split("://", 1)[0]
        r["jurnal"] = list(dict.fromkeys(f"{skema}://{b.split('://', 1)[1]}" for b in r["jurnal"]))
        # Jurnal di akar domain (beranda == dasar) tidak punya tingkat situs terpisah untuk dicari.
        r["situs"] = r["cari_situs"] != 0 and any(b != r["dasar"] for b in r["jurnal"])
    return list(rencana.values())


def _rencana_situs(katalog: Katalog, situs: str) -> list[dict]:
    """Rencana untuk mode fokus situs: jurnal tertentu bila alamatnya menyertakan jalur jurnal, selain itu instalasi
    di host itu yang ada di katalog, ditambah tebakan `https://host/index.php`."""
    alamat = situs.strip() if re.match(r"https?://", situs.strip(), re.I) else f"https://{situs.strip()}"
    host = urlparse(alamat).netloc.lower()
    if not beranda_jurnal(alamat):
        return []
    beranda = beranda_jurnal(alamat)
    if urlparse(beranda).path.strip("/") and _jurnal_valid(beranda, dasar_instalasi(beranda)):
        return [{"dasar": dasar_instalasi(beranda), "cari_situs": 0, "situs": False, "jurnal": [beranda]}]
    rencana = _rencana(katalog.jurnal_di_host(host))
    if _kunci_instalasi(f"https://{host}") not in {_kunci_instalasi(r["dasar"]) for r in rencana}:
        rencana.append({"dasar": f"https://{host}/index.php", "cari_situs": None, "jurnal": []})
    for r in rencana:
        r["situs"] = r["cari_situs"] != 0
    return rencana


def _skor_judul(judul: str, kelompok: list[set[str]]) -> float:
    kata = set(kata_penting(judul))
    return sum(1 for k in kelompok if k & kata) / len(kelompok) if kelompok else 0.0


def _jalankan_putaran(katalog: Katalog, rencana: list[dict], kata: list[str], *, segarkan: bool, stat: dict,
                      catatan: dict, berhenti_bila_ok: bool, batas: float, maks_jurnal: int) -> list[tuple[int, int, dict]]:
    """Satu putaran pencarian ke semua instalasi aktif. Hasil: [(urutan instalasi, urutan di halaman, hasil)].

    Instalasi yang diblokir, gagal, atau terlalu lambat dinonaktifkan untuk putaran berikutnya. Putaran berakhir
    pada `batas`, atau lebih awal bila setelah BATAS_LUNAK detik tinggal sedikit host lambat yang belum menjawab."""
    temuan, per_host = [], {}
    for r in rencana:
        if not r["aktif"]:
            continue
        urls = [url_cari(f"{r['dasar']}/index", kata)] if r["situs"] else [url_cari(b, kata) for b in r["jurnal"][:maks_jurnal]]
        if not urls:
            r["aktif"] = False
            continue
        tersimpan = [] if segarkan else [katalog.ambil_cache(u) for u in urls]
        if tersimpan and all(h is not None for h in tersimpan):
            stat["dari_cache"] += len(urls)
            for h in tersimpan:
                if h:
                    catatan["memberi_hasil"].add(r["dasar"])
                temuan += [(r["urutan"], i, x) for i, x in enumerate(h)]
            continue
        per_host.setdefault(_host(r["dasar"]), []).append(r)
    if not per_host:
        return temuan
    per_dasar = {r["dasar"]: r for tugas in per_host.values() for r in tugas}
    henti = threading.Event()
    pool = ThreadPoolExecutor(max_workers=min(PARALEL, len(per_host)))
    try:
        milik = {pool.submit(_kerjakan_host, tugas, kata, berhenti_bila_ok, henti, maks_jurnal): tugas for tugas in per_host.values()}
        sisa, lunak = set(milik), time.monotonic() + BATAS_LUNAK
        while sisa:
            sekarang = time.monotonic()
            if sekarang >= batas or (
                sekarang >= lunak and len(sisa) <= max(2, 0.15 * len(milik)) and all(f.running() for f in sisa)
            ):
                break
            selesai, sisa = wait(sisa, timeout=(lunak if sekarang < lunak else batas) - sekarang, return_when=FIRST_COMPLETED)
            for f in selesai:
                try:
                    keluaran = f.result()
                except Exception:  # galat tak terduga di satu host tidak boleh menggagalkan seluruh pencarian
                    for r in milik[f]:
                        r["aktif"] = False
                        catatan["galat"].add(r["dasar"])
                    continue
                for k in keluaran:
                    r = per_dasar[k["dasar"]]
                    _catat_keluaran(katalog, k, stat, catatan, r)
                    if k["status"] == "ok":
                        temuan += [(r["urutan"], i, x) for i, x in enumerate(k["hasil"])]
        for f in sisa:
            for r in milik[f]:
                catatan["lambat"].add(r["dasar"])
                if f.running():  # yang belum sempat dimulai tetap ditanya di putaran berikutnya
                    r["aktif"] = False
    finally:
        henti.set()
        pool.shutdown(wait=False, cancel_futures=True)
    return temuan


def _catat_keluaran(katalog: Katalog, k: dict, stat: dict, catatan: dict, r: dict) -> None:
    """Simpan hasil satu permintaan ke katalog (cache, status) dan sesuaikan rencana untuk putaran berikutnya."""
    stat["permintaan"] += 1
    status, dasar = k["status"], k["dasar"]
    if status == "ok":
        if k["hasil"]:
            catatan["memberi_hasil"].add(dasar)
        katalog.simpan_cache(k["url"], k["hasil"])
        katalog.tandai_instalasi(dasar, status="ok", cari_situs=True if k["jenis"] == "situs" else None)
        if k["jenis"] == "jurnal":
            katalog.tandai_jurnal(k["beranda"], "ok")
    elif status in ("diblokir", "galat"):
        catatan[status].add(dasar)
        katalog.tandai_instalasi(dasar, status="diblokir" if status == "diblokir" else "gagal")
        if status == "galat" and k["jenis"] == "jurnal":
            katalog.tandai_jurnal(k["beranda"], "gagal")
        r["aktif"] = False
    elif k["jenis"] == "situs":
        katalog.tandai_instalasi(dasar, cari_situs=False)  # tidak_didukung / bukan_ojs: cari per jurnal
        r["situs"] = False
    else:
        catatan["bukan_ojs"].add(k["beranda"])
        katalog.tandai_jurnal(k["beranda"], "bukan_ojs")
        if k["beranda"] in r["jurnal"]:
            r["jurnal"].remove(k["beranda"])


def daftar_putaran(kata: list[str]) -> list[list[str]]:
    """Kueri dari yang paling sempit (semua kata, AND) ke yang paling luas (satu kata khas)."""
    putaran = [kata]
    if len(kata) > 2:
        putaran.append(kata[:2])
    if len(kata) > 1:
        putaran += [[k] for k in kata[:2]]
    return putaran


def cari_di_katalog(
    katalog: Katalog, token_urut: list[str], *, jurnal_maks: int = 100, situs: str | None = None,
    cukup: int = AMBANG_HASIL, segarkan: bool = False, lapor=lambda pesan: None,
) -> tuple[list[HasilCari], dict]:
    """Cari kata kunci di situs-situs jurnal katalog (atau satu situs pada mode fokus). Kueri dilonggarkan
    bertahap sampai terkumpul `cukup` artikel. Hasil diurutkan menurut kecocokan judul dengan kueri.

    Hasil: (daftar HasilCari bermesin "katalog", statistik)."""
    mulai = time.monotonic()
    stat = {
        "jurnal_dipilih": 0, "instalasi": 0, "permintaan": 0, "dari_cache": 0, "situs_memberi_hasil": 0,
        "diblokir": 0, "gagal": 0, "bukan_ojs": 0, "terlalu_lambat": 0, "jurnal_baru": 0, "hasil": 0,
        "putaran": [], "durasi_detik": 0.0,
    }
    catatan = {k: set() for k in ("memberi_hasil", "diblokir", "galat", "bukan_ojs", "lambat")}
    kata = susun_kata(token_urut)
    if situs:
        rencana, maks_jurnal = _rencana_situs(katalog, situs), MAKS_JURNAL_SITUS
    else:
        dipilih = katalog.pilih(set(token_urut), jurnal_maks)
        stat["jurnal_dipilih"] = len(dipilih)
        rencana, maks_jurnal = _rencana(dipilih), MAKS_JURNAL_PER_INSTALASI
    for i, r in enumerate(rencana):
        r.update(urutan=i, aktif=True)
    stat["instalasi"] = len(rencana)

    kelompok = kelompok_konsep(set(token_urut))
    terkumpul: dict[str, tuple] = {}
    for ke, kata_putaran in enumerate(daftar_putaran(kata) if kata and rencana else []):
        sisa_waktu = BATAS_TOTAL - (time.monotonic() - mulai)
        aktif = sum(r["aktif"] for r in rencana)
        if sisa_waktu < 5 or not aktif:
            break
        lapor(
            f"Katalog: mencari \"{' '.join(kata_putaran)}\" di "
            + (f"situs {situs}" if situs else f"{aktif} situs jurnal")
            + (f" (putaran {ke + 1}, {len(terkumpul)} artikel terkumpul)" if ke else "")
            + "..."
        )
        sebelum = len(terkumpul)
        for urutan, posisi, h in _jalankan_putaran(
            katalog, rencana, kata_putaran, segarkan=segarkan, stat=stat, catatan=catatan, berhenti_bila_ok=bool(situs),
            batas=time.monotonic() + min(BATAS_PUTARAN, sisa_waktu), maks_jurnal=maks_jurnal,
        ):
            kunci = (-_skor_judul(h["judul"], kelompok), ke, urutan, posisi)
            if h["url"] not in terkumpul or kunci < terkumpul[h["url"]][0]:
                terkumpul[h["url"]] = (kunci, h)
        stat["putaran"].append({"kata": " ".join(kata_putaran), "hasil_baru": len(terkumpul) - sebelum})
        if len(terkumpul) >= cukup:
            break

    # Jurnal yang muncul di hasil (mis. dari pencarian se-instalasi) masuk katalog untuk pencarian berikutnya.
    beranda_hasil = {b for _, h in terkumpul.values() if (b := beranda_jurnal(h["url"]))}
    if beranda_hasil:
        stat["jurnal_baru"] = katalog.tambah_banyak(((b, None, ()) for b in sorted(beranda_hasil)), "otomatis")["baru"]
    # Dalam tingkat skor judul & putaran yang sama, jurnal diselang-seling supaya artikel teratas (yang akan dibuka)
    # berasal dari banyak jurnal, bukan dari satu situs yang kebetulan memberi banyak hasil.
    giliran: dict[tuple, int] = defaultdict(int)
    urut = []
    for kunci, h in sorted(terkumpul.values(), key=lambda x: x[0]):
        tingkat = kunci[:2]
        giliran[(tingkat, beranda_jurnal(h["url"]))] += 1
        urut.append(((tingkat, giliran[(tingkat, beranda_jurnal(h["url"]))], kunci[2:]), h))
    urut.sort(key=lambda x: x[0])
    hasil = [HasilCari(h["judul"], h["url"], "", "katalog", i) for i, (_, h) in enumerate(urut, 1)]
    stat.update(
        hasil=len(hasil), situs_memberi_hasil=len(catatan["memberi_hasil"]), diblokir=len(catatan["diblokir"]),
        gagal=len(catatan["galat"]), bukan_ojs=len(catatan["bukan_ojs"]), terlalu_lambat=len(catatan["lambat"]),
        durasi_detik=round(time.monotonic() - mulai, 1),
    )
    return hasil, stat


def _jurnal_valid(beranda: str, dasar: str) -> bool:
    segmen = beranda[len(dasar):].strip("/")
    return bool(_SEGMEN_JURNAL.match(segmen)) and segmen.lower() not in _BUKAN_JURNAL


def _teks(x, batas: int = 200) -> str:
    return " ".join(x.get_all_text(separator=" ", strip=True).split())[:batas]


def _nama_jurnal(a, cari_blok: bool) -> str | None:
    """Nama jurnal dari judul (h2-h4) yang memuat tautan; bila `cari_blok`, juga judul di blok terdekat (mis. tautan
    "View Journal" atau gambar sampul di daftar jurnal OJS)."""
    for i, x in enumerate(a.iterancestors()):
        if x.tag in ("h2", "h3", "h4"):
            return _teks(x) or None
        if not cari_blok or i >= 3:
            return None
        if judul := x.css("h2, h3, h4").first:
            return _teks(judul) or None
    return None


def _alamat_polos(url: str) -> str:
    alamat = urlparse(url)
    return f"{alamat.scheme.lower()}://{alamat.netloc.lower()}{alamat.path}".rstrip("/")


def _ambil_tetangga(dasar: str) -> list[tuple[str, str | None]]:
    """Jurnal lain di satu instalasi OJS: dari halaman daftar jurnal situs, cadangan OAI ListSets.

    Hanya tautan yang menunjuk tepat ke beranda jurnal yang dihitung (bukan subhalaman yang disebut di deskripsi).
    Bila halaman punya daftar jurnal OJS (`.journals` / `#journals`), hanya isi daftar itu yang dipakai."""
    status, html, akhir = _ambil(f"{dasar}/index")
    jurnal: dict[str, str | None] = {}
    if status == "ok" and html and _TANDA_OJS.search(html):
        page = Selector(html, url=akhir)
        daftar = page.css(".journals").first or page.css("#journals").first
        for a in (daftar or page).css("a[href]"):
            if daftar is None and _di_samping(a):
                continue
            href = _alamat_polos(urljoin(akhir, a.attrib.get("href", "").strip()))
            b = beranda_jurnal(href)
            if not b or b != href or dasar_instalasi(b) != dasar or not _jurnal_valid(b, dasar):
                continue
            if not jurnal.get(b):
                jurnal[b] = _nama_jurnal(a, cari_blok=daftar is not None)
    if not jurnal:
        status, xml, _ = _ambil(f"{dasar}/index/oai?verb=ListSets")
        if status == "ok" and xml:
            for spec, nama in re.findall(r"<setSpec>([^<:]+)</setSpec>\s*<setName>([^<]*)</setName>", xml):
                b = f"{dasar}/{spec.strip()}"
                if _jurnal_valid(b, dasar):
                    jurnal[b] = html_unescape(nama).strip() or None
    return list(jurnal.items())


def temukan_tetangga(dasar_daftar: list[str], batas_waktu: float = 15) -> dict[str, list[tuple[str, str | None]]]:
    """Daftar jurnal tiap instalasi (paralel antar host, berurutan dalam satu host). Instalasi yang belum selesai
    saat batas waktu tidak ada di hasil. Dijalankan di thread pekerja; penulisan ke katalog dilakukan pemanggil."""
    per_host: dict[str, list[str]] = defaultdict(list)
    for d in dict.fromkeys(dasar_daftar):
        per_host[_host(d)].append(d)
    hasil: dict[str, list] = {}
    if not per_host:
        return hasil
    pool = ThreadPoolExecutor(max_workers=min(PARALEL, len(per_host)))
    try:
        tugas = [pool.submit(lambda daftar: {d: _ambil_tetangga(d) for d in daftar}, daftar) for daftar in per_host.values()]
        selesai, _ = wait(tugas, timeout=batas_waktu)
        for f in selesai:
            try:
                hasil.update(f.result())
            except Exception:
                pass  # dicoba lagi pada pencarian berikutnya
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    return hasil
