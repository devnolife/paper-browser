"""Padanan istilah Indonesia-Inggris dan peta bidang ilmu, untuk memilih jurnal di katalog yang sesuai dengan kueri.

Nama jurnal Indonesia kebanyakan berbahasa Indonesia, tetapi kata kunci & subjeknya (terutama dari DOAJ) banyak yang
berbahasa Inggris, sedangkan kueri pengguna biasanya berbahasa Indonesia.
"""

# Kata Indonesia -> padanan/variasinya. Satu kata kueri dianggap cocok bila kata itu atau salah satu padanannya muncul.
PADANAN = {
    "algoritma": {"algorithm", "algorithms", "algoritme"},
    "penjadwalan": {"scheduling", "schedule", "jadwal"},
    "optimasi": {"optimization", "optimisation", "optimal", "optimalisasi"},
    "jaringan": {"network", "networks", "networking"},
    "saraf": {"neural"},
    "pakar": {"expert"},
    "informasi": {"information", "informatics", "informatika"},
    "pembelajaran": {"learning", "teaching", "instruction"},
    "kecerdasan": {"intelligence", "intelligent"},
    "buatan": {"artificial"},
    "citra": {"image", "imaging"},
    "pengenalan": {"recognition"},
    "klasifikasi": {"classification", "classifier"},
    "prediksi": {"prediction", "forecasting", "peramalan"},
    "peramalan": {"forecasting", "prediction", "prediksi"},
    "keamanan": {"security", "secure"},
    "kriptografi": {"cryptography", "encryption"},
    "perangkat": {"device", "devices", "software", "hardware"},
    "pemrograman": {"programming", "program"},
    "komputer": {"computer", "computing", "computers"},
    "penyakit": {"disease", "diseases"},
    "diagnosa": {"diagnosis", "diagnostic"},
    "diagnosis": {"diagnosa", "diagnostic"},
    "kesehatan": {"health", "healthcare"},
    "pasien": {"patient", "patients"},
    "obat": {"drug", "drugs", "medicine", "pharmacy"},
    "gizi": {"nutrition", "nutritional"},
    "kehamilan": {"pregnancy", "maternal"},
    "ibu": {"maternal", "mother"},
    "anak": {"child", "children", "pediatric"},
    "pendidikan": {"education", "educational"},
    "siswa": {"student", "students"},
    "mahasiswa": {"student", "students", "university"},
    "guru": {"teacher", "teachers"},
    "ekonomi": {"economics", "economy", "economic"},
    "manajemen": {"management"},
    "akuntansi": {"accounting"},
    "keuangan": {"finance", "financial"},
    "perbankan": {"banking", "bank"},
    "pemasaran": {"marketing"},
    "bisnis": {"business"},
    "pajak": {"tax", "taxation"},
    "hukum": {"law", "legal"},
    "pertanian": {"agriculture", "agricultural", "agronomy"},
    "tanaman": {"plant", "plants", "crop", "crops"},
    "tanah": {"soil", "land"},
    "air": {"water"},
    "lingkungan": {"environment", "environmental"},
    "energi": {"energy"},
    "listrik": {"electrical", "electricity", "electric"},
    "industri": {"industrial", "industry"},
    "produksi": {"production", "manufacturing"},
    "kualitas": {"quality"},
    "transportasi": {"transportation", "transport"},
    "konstruksi": {"construction", "civil"},
    "pariwisata": {"tourism"},
    "komunikasi": {"communication", "communications"},
    "sosial": {"social", "society"},
    "budaya": {"culture", "cultural"},
    "politik": {"political", "politics"},
    "pemerintahan": {"government", "governance"},
    "bahasa": {"language", "languages", "linguistics"},
    "sastra": {"literature", "literary"},
    "matematika": {"mathematics", "mathematical"},
    "statistika": {"statistics", "statistical"},
    "fisika": {"physics"},
    "kimia": {"chemistry", "chemical"},
    "biologi": {"biology", "biological"},
    "psikologi": {"psychology", "psychological"},
    "agama": {"religion", "religious"},
    "islam": {"islamic"},
    "pangan": {"food"},
    "makanan": {"food"},
    "ikan": {"fish", "fisheries"},
    "ternak": {"livestock", "animal"},
    "desa": {"village", "rural"},
}

# Bidang -> (kata kueri yang memicu bidang itu, kata penanda bidang pada nama/kata kunci/subjek jurnal).
BIDANG = {
    "komputer": (
        {
            "algoritma", "greedy", "genetika", "jaringan", "saraf", "website", "web", "android", "aplikasi", "pakar",
            "data", "mining", "klasifikasi", "prediksi", "peramalan", "citra", "iot", "internet", "database",
            "perangkat", "software", "keamanan", "kriptografi", "enkripsi", "machine", "learning", "deep", "neural",
            "chatbot", "rekomendasi", "fuzzy", "naive", "bayes", "knn", "svm", "cnn", "lstm", "nodemcu", "arduino",
            "mikrokontroler", "sensor", "robot", "game", "multimedia", "pemrograman", "cloud", "server",
            "blockchain", "ecommerce", "penjadwalan", "optimasi", "informasi", "komputer", "digital", "monitoring",
        },
        {
            "informatika", "informatics", "komputer", "computer", "computers", "computing", "software", "informasi",
            "information", "telematika", "telematics", "cyber", "kecerdasan", "intelligence", "data",
        },
    ),
    "kesehatan": (
        {
            "penyakit", "diagnosa", "diagnosis", "pasien", "kesehatan", "gizi", "stunting", "ibu", "hamil",
            "kehamilan", "bayi", "balita", "obat", "puskesmas", "rumah", "sakit", "kanker", "diabetes", "hipertensi",
            "covid", "perawat", "keperawatan", "bidan", "kebidanan", "farmasi", "klinis", "medis", "darah",
            "jantung", "gigi", "mata", "kulit",
        },
        {
            "kesehatan", "health", "medis", "medical", "medicine", "kedokteran", "keperawatan", "nursing",
            "kebidanan", "midwifery", "farmasi", "pharmacy", "pharmaceutical", "gizi", "nutrition", "clinical",
            "klinis", "epidemiology", "epidemiologi", "dental",
        },
    ),
    "pendidikan": (
        {
            "pendidikan", "pembelajaran", "siswa", "guru", "sekolah", "mahasiswa", "kurikulum", "belajar",
            "mengajar", "kelas", "literasi", "media", "evaluasi",
        },
        {"pendidikan", "education", "educational", "pembelajaran", "learning", "teaching", "pedagogy", "keguruan",
         "tarbiyah", "curriculum"},
    ),
    "ekonomi": (
        {
            "ekonomi", "keuangan", "akuntansi", "pajak", "bank", "perbankan", "saham", "investasi", "umkm",
            "pemasaran", "penjualan", "bisnis", "manajemen", "koperasi", "syariah", "zakat", "inflasi", "harga",
            "keuntungan", "laba",
        },
        {"ekonomi", "economics", "economic", "economy", "akuntansi", "accounting", "keuangan", "finance", "financial",
         "manajemen", "management", "bisnis", "business", "perbankan", "banking", "marketing", "pemasaran"},
    ),
    "hukum": (
        {"hukum", "undang", "pidana", "perdata", "peradilan", "pengadilan", "kontrak", "regulasi"},
        {"hukum", "law", "legal", "jurisprudence"},
    ),
    "pertanian": (
        {
            "pertanian", "tanaman", "padi", "jagung", "pupuk", "tanah", "petani", "panen", "hortikultura", "kebun",
            "sawit", "peternakan", "ternak", "sapi", "ayam", "ikan", "perikanan", "budidaya",
        },
        {"pertanian", "agriculture", "agricultural", "agronomi", "agronomy", "agribisnis", "agribusiness",
         "peternakan", "animal", "livestock", "perikanan", "fisheries", "aquaculture", "kehutanan", "forestry",
         "plant", "crop", "soil"},
    ),
    "teknik": (
        {
            "konstruksi", "beton", "jembatan", "jalan", "struktur", "mesin", "motor", "listrik", "energi", "surya",
            "turbin", "material", "manufaktur", "produksi", "industri", "kualitas", "ergonomi", "pabrik", "logistik",
            "transportasi", "penjadwalan", "optimasi",
        },
        {"teknik", "engineering", "sipil", "civil", "mesin", "mechanical", "elektro", "electrical", "industri",
         "industrial", "energi", "energy", "material", "materials", "manufacturing"},
    ),
    "agama": (
        {"islam", "dakwah", "quran", "hadis", "fiqih", "fikih", "tafsir", "zakat", "wakaf", "pesantren", "madrasah",
         "syariah", "akhlak", "tauhid"},
        {"islam", "islamic", "agama", "religion", "religious", "syariah", "sharia", "dakwah", "tarbiyah",
         "ushuluddin", "quran", "hadith", "theology"},
    ),
    "sosial": (
        {"sosial", "masyarakat", "komunikasi", "politik", "pemerintahan", "desa", "budaya", "gender", "kemiskinan",
         "pariwisata"},
        {"sosial", "social", "sociology", "sosiologi", "komunikasi", "communication", "politik", "political",
         "politics", "pemerintahan", "government", "administrasi", "administration", "budaya", "culture",
         "humaniora", "humanities", "pariwisata", "tourism"},
    ),
    "matematika": (
        {"matematika", "statistika", "statistik", "regresi", "persamaan", "peluang", "probabilitas", "numerik",
         "aljabar", "graf"},
        {"matematika", "mathematics", "mathematical", "statistika", "statistics", "statistical"},
    ),
    "lingkungan": (
        {"lingkungan", "sampah", "limbah", "pencemaran", "iklim", "banjir", "bencana"},
        {"lingkungan", "environment", "environmental", "ecology", "ekologi", "geografi", "geography", "disaster"},
    ),
    "bahasa": (
        {"bahasa", "sastra", "linguistik", "puisi", "novel", "terjemahan", "wacana"},
        {"bahasa", "language", "linguistics", "linguistik", "sastra", "literature", "literary"},
    ),
    "psikologi": (
        {"psikologi", "stres", "kecemasan", "motivasi", "kepribadian", "perilaku"},
        {"psikologi", "psychology", "psychological", "konseling", "counseling"},
    ),
}


def kelompok_konsep(token: set[str]) -> list[set[str]]:
    """Tiap kata kueri menjadi satu kelompok: kata itu sendiri beserta padanannya."""
    return [{t} | PADANAN.get(t, set()) for t in sorted(token)]


def penanda_bidang(token: set[str]) -> set[str]:
    """Kata penanda semua bidang yang dipicu oleh kata kueri."""
    penanda = set()
    for pemicu, tanda in BIDANG.values():
        if token & pemicu:
            penanda |= tanda
    return penanda
