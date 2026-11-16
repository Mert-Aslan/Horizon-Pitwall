# FH6 Data Out protokolü

Kaynak: Forza Support, *Forza Horizon 6 "Data Out" Documentation* —
<https://support.forza.net/hc/en-us/articles/51744149102611-Forza-Horizon-6-Data-Out-Documentation>
(16 Kasım 2026'da tarayıcıdan okundu; otomatik HTTP isteği 403 döndü). Şemanın kod karşılığı tek yerdedir: [`backend/telemetry/schema.py`](../backend/telemetry/schema.py); makine okunur hali `GET /api/v1/capabilities` → `schema`.

## Belgenin söyledikleri

- Tek yönlü UDP, oyunun kare hızında; tek ve sabit paket formatı (Forza Motorsport'taki format seçimi yok).
- **Toplam paket: 324 byte.**
- Alan sırası ve tipleri (S/U/F + bit) listelenir; **offset verilmez.**
- Veri yalnız aktif sürüşte gönderilir; menü, pause, replay, rewind ve yarış bitişinden sonra gönderilmez.
- FM'den farklı olarak `NumCylinders` ile `PositionX` arasına `CarGroup` (U32), `SmashableVelDiff` (F32, m/s), `SmashableMass` (F32, kg) eklenmiştir; `TireWear` ve `TrackOrdinal` yoktur.
- Birimler: hız m/s, güç W, tork N·m, boost "PSI above atmospheric", konum m (world), ivme/hız car-local (X sağ, Y yukarı, Z ileri), açısal hız rad/s, yön rad, yakıt 0–1, süspansiyon 0 = tam uzama / 1 = tam sıkışma, slip değerlerinde |x| > 1 tutuş kaybı, tur süreleri s ve "0.0 if not applicable", `TimestampMS` "can overflow to 0 eventually".
- Aralıklar: `CarClass` 0 (D) … 7 (X), `CarPerformanceIndex` 100–999, `DrivetrainType` 0 FWD / 1 RWD / 2 AWD, `Steer` −127 tam sol … 127 tam sağ, `NormalizedDrivingLine` / `NormalizedAIBrakeDifference` −127…127, pedallar 0–255.

## Kararlar

| No | Karar | Gerekçe |
|---|---|---|
| P-1 | 88 alan tipler arka arkaya (hizalama boşluğu olmadan) dizilince 323. byte'ta biter. Belgedeki 324. byte **belgelenmemiş opak kuyruk** olarak saklanır (`quality.tail_hex`, Diagnostics → *Trailing byte*). Yalnız **324 byte** kabul edilir; 323 byte'lık paket `length_323_undocumented` nedeniyle reddedilir. | Belge 324 diyor; 323'ü kabul etmek doğrulanmamış bir formatı canlı veriye çevirmek olur. Önceki Forza Horizon sürümlerinde de son byte dolgu olarak gözlenmişti; bu bir ipucu, kanıt değil. |
| P-2 | Byte sırası **little-endian varsayılır.** | Belge belirtmiyor. Windows/x64 oyun ve önceki tüm Forza başlıkları little-endian. Test `test_little_endian_is_used` varsayımı sabitler; gerçek yakalama doğrulayacak. |
| P-3 | `Gear` ham değeri korunur. 1–10 numarasıyla gösterilir. **0 → "R"**, **11 → "N"** (vites arası); ikisi de belgelenmemiş, gerçek gözleme dayalı etiket olarak notuyla gösterilir (`gear.note`, `verified: false`). Diğer değerler "unrecognised gear code". | Belge yalnız "Current gear" diyor. Gerçek oturum (16 Kasım 2026, 24 285 sürüş örneği): geri giden her örnekte (107/107) vites 0; 11 yalnız vites değişimlerinin ortasında (3→11→2, 2→11→3 …), hızda ve gaz/frenle. Tek araç, tek oturum. |
| P-4 | Lastik sıcaklığı **birimsiz, dönüştürülmeden** gösterilir; arayüz "temperature unit not documented by the game" yazar. | Belge yalnız "Tire temperature" diyor. Celsius/Fahrenheit dönüşümü uydurulmaz. |
| P-5 | `CarClass` harfleri C/B/A/S1/S2/R gelenekten gelir; belge yalnız 0 = D ve 7 = X'i adlandırır. Aralık dışı kodlar etiketsiz kalır ve işaretlenir. | |
| P-6 | `Steer` −128 temsil edilebilir ama belge dışıdır: ham −128 korunur, normalize değer −1'e sınırlanır, `out_of_range` işaretlenir. Aynı kural `NormalizedDrivingLine` ve `NormalizedAIBrakeDifference` için geçerlidir. | Eski prototip −100.787 % üretiyordu. |
| P-7 | Belgelenmiş aralık dışındaki değerler (PI, CarClass, Drivetrain, Fuel, süspansiyon, 0/1 bayraklar) atılmaz; normalize katmanda tutulur (enum'larda etiket `null`) ve `quality.fields` içinde `out_of_range` olarak işaretlenir. Negatif güç/tork fiziksel olarak mümkündür ve işaretlenmez. | |
| P-8 | Tur süresi 0.0 → normalize değer `null`, kalite `not_applicable`. Ölçülmüş sıfır hız 0.0 kalır. NaN/±Inf → `null` + `non_finite`; bağımlı türetilmiş değerler de `null` olur. | Eski `or 0` zincirlerinin yerine. |
| P-9 | **Boş paket:** `IsRaceOn = 0` ve `TimestampMS` dışındaki tüm alanlar 0 ise paket kabul edilir (bağlantı canlı, sıra korunur, kayda girer) ama veri taşımaz: normalize/türetilmiş değerler `null`, kalite bayrağı `empty_packet`, sayaç `accepted_empty`. Arayüz "No driving data" gösterir ve son gerçek değerleri soluk tutar; özetlerde hız ortalamasına girmez. | Gerçek oturumda `IsRaceOn = 0` olan 1418/1418 paketin 8–322. byte'ları tamamen sıfırdı (hız 0, devir 0, max devir 0). Bunları ölçülmüş sıfır göstermek yanlış olurdu. Algılama byte içeriğine dayanır; `IsRaceOn` tek başına yeterli sayılmaz. |

## Zaman damgası, sıra ve oturum

`TimestampMS` (U32, ms) — kural [`hub.py`](../backend/telemetry/hub.py) başında:

- daha yeni → kabul; eşit → `duplicates` (D-1, aşağıda; `equal_timestamp_policy`); önceki 2³²−600 000 üstünde ve yeni < 600 000 → **wraparound** (kabul);
- en fazla 2000 ms geride ve son geçerli paket < 1 s önce → **out_of_order** (red, canlı snapshot'ı ezmez);
- daha geride → **reset adayı**. Tek bir geri gidiş hiçbir şeyi sıfırlamaz; 3 ardışık ve kendi içinde artan aday gelirse reset kabul edilir. Arada orijinal akış devam ederse adaylar atılır.

Gönderen adresi **stream segment**'e bağlanır. Başka bir adresten gelen geçerli paketler, bağlı kaynak 3 s susana kadar `foreign_source` olarak reddedilir ve Diagnostics → *Ignored senders* altında listelenir (en çok 16 adres, 10 dk TTL). Yeni kaynak veya mod = yeni `stream_segment_id`; segmentte geçmiş, hız tahmini, araç kimliği ve sıra durumu sıfırdan başlar. `session_id` artık bu segment kimliğinin eskimiş takma adıdır. Zaman damgası reseti segmenti değiştirmez, olay olarak kaydedilir. Kimlik ve zaman modeli: [ARCHITECTURE.md](ARCHITECTURE.md#kimlik-ve-zaman). Alım sırası oyunun fizik sırasının kanıtı değildir; localhost'ta bile UDP'nin sırayı koruduğu varsayılmaz.

UDP'de sıra numarası yoktur: `estimated_gaps` / `estimated_missing_packets`, `TimestampMS` farkının ortalama aralığa (EMA) oranından **tahmindir**; kesin paket kaybı yüzdesi iddia edilmez. 2 s'den uzun boşluk `interruptions` sayılır.

## Bağlantı durumları

Sunucuda monotonic saatle hesaplanır (tarayıcı/sunucu duvar saati karşılaştırılmaz):

| Durum | Koşul |
|---|---|
| `starting` | UDP soketi henüz bağlanmadı |
| `error` | bind veya alım hatası (mesajıyla birlikte) |
| `waiting` | soket açık, bu modda hiç geçerli paket yok |
| `receiving` | son geçerli paket ≤ 750 ms önce (`stale_after_ms`) |
| `stale` | son geçerli paket daha eski |

Ayrıca `traffic_warning`: son 2 s içinde reddedilen paket var ve durum `receiving` değil → `invalid_packets` / `foreign_source`. Geçersiz paketler paket yaşını **yenilemez**. `IsRaceOn` ayrı alandır (`normalized.race_on`); paket gelmemesinden pause/disconnect nedeni çıkarılmaz.

## Fixture kökeni

`fixtures/synthetic/` — tamamı sentetik, `tools/make_fixtures.py` ile üretilir; paketler `tests/packets.py` içindeki elle yazılmış `struct.pack_into` offset'leriyle yazılır (şema modülünden bağımsız). `CarOrdinal` daima negatiftir; gerçek bir araç ID'si gibi kataloglanamaz. `manifest.json` her dosyanın beklenen sonucunu içerir.

`fixtures/real/` — **gerçek oyun byte'ları** (16 Kasım 2026 kaydından 7 datagram, `manifest.json` kaynağı ve beklentileri yazar). Yalnız araç telemetrisi içerir; ad veya ağ adresi yok. Eşit zaman damgalı değişen içerik için gerçek fixture henüz yok (Diagnostics → *Diagnostic capture* ile alınabilir).

### Gerçek oturum — 16 Kasım 2026, 0.3.0 (kullanıcının kaydı + sayaçlar, yalnız okuma)

Kayıt `20261116T192643Z-e26480`: 484 s, 25 703 örnek, 0 düşen, kapsama %100, dosya doğrulaması geçti. Bulgular (ayrıntı yukarıda ve kararlarda):

- Uzunluk her zaman 324, 324. byte her zaman `00` (P-1: dolgu byte'ı olarak güçlü kanıt).
- Little-endian okumayla değerler fiziksel olarak tutarlı: PI 999, sınıf 7 (X), AWD, max devir 11 000, tepe hız 316.5 km/h (P-2: güçlü kanıt).
- Direksiyon uçları −127 / 127; −128 hiç görülmedi (P-6).
- Gaz/fren 0–255 tam aralık; debriyaj sürüş boyunca 0 (otomatik vites olabilir; doğrulanmadı).
- Lastik sıcaklığı sürüşte 94–300 aralığında. Fahrenheit ile uyumlu görünüyor (≈ 34–149 °C) ama birim hâlâ belgelenmedi; dönüşüm yapılmıyor (P-4).
- Serbest sürüşte tur alanları 0 (P-8 ile tutarlı); NaN/Inf hiç yok.
- Gerçek byte'lardan regresyon fixture'ları: `fixtures/real/` (boş paket, geri vites, vites arası, ileri vites, tam fren, direksiyon iki uç; `tools/extract_real_fixture.py`, `tests/test_real_fixtures.py`).

### Pasif gözlem — 16 Kasım 2026, 0.2.0 (yalnız okuma)

Kullanıcının 8080'de canlı modda çalışan örneğinden, durumuna dokunmadan `GET /api/v1/diagnostics` ve `/telemetry/latest` okundu. Bu kontrollü bir doğrulama oturumu değil; kanıt niteliği sınırlı:

- 66 611 paketin **tamamı 324 byte**; kabul edilen 58 436 paketin **324. byte'ı her zaman `00`** (P-1 için güçlü işaret: dolgu byte'ı).
- Değerler little-endian okumayla tutarlı ve makul (ör. `EngineMaxRpm` 9999.995, `EngineIdleRpm` 799.9995, `Speed` 39.5 m/s, `Steer` 127 tam sağda, PI 916, `CarClass` 6) — P-2 için işaret.
- Gönderen kaynak portu `5200` (belgedeki "oyun 5200–5300 arasında kendi soketini açar" ile tutarlı); gözlenen hız ~50 Hz, `TimestampMS` ortalama aralığı ~21.7 ms.
- Lastik sıcaklıkları sürüşte ~98–210 aralığında görüldü. Bu Fahrenheit ile uyumlu olabilir, ama birim **hâlâ doğrulanmadı**; dönüşüm yapılmıyor (P-4).
- **D-1 — karar verildi (0.3.1):** 0.2.0 sürümünde paketlerin %5.4–12'si aynı `TimestampMS` yüzünden reddediliyordu. 0.3.0 sayaçlarıyla gerçek oturum (16 Kasım 2026, 1 847 s, 105 059 paket) şunu gösterdi:
  - 6 619 eşit zaman damgalı paketin **6 402'si byte byte aynı** ve **hepsi `IsRaceOn = 0`** iken (boş paketlerin tekrarı, P-9);
  - **217'si yeni içerik** ve **hepsi `IsRaceOn = 1`** iken: her birinde 28–49 alan farklı (hız 39.84 → 39.92 m/s, devir, ivme, hız vektörü, açısal hız, süspansiyon, slip). Yani sürüş sırasında oyun bazen yeni fizik karesini önceki zaman damgasıyla gönderiyor; `tail_only` ve `non_finite` sınıfı 0.
  - **Karar:** varsayılan `equal_timestamp_policy = accept_changed`. Aynı byte'lar yine tekrar sayılır ve reddedilir; yeni içerik kabul edilir. Δt = 0 hız/boşluk tahminine girmez; alım sırası uygulamanın `sequence`'i (kayıtta `i`) ile korunur. Aynı ms'deki iki karenin oyun içi sırası kesin sayılmaz. `reject` ile 0.3.0 davranışı geri alınabilir. Testler: `test_segments.py`; gerçek byte'larla değişen içerik fixture'ı henüz yok (o anların yakalaması alınmadı; 5 örnekteki alan listeleri yukarıdaki sonucun kaynağı).
  - Kayıttaki zaman damgası adımları: 16/15 ms (%79), 31/32 ms (%20), 47/63 ms (7 kez); ortalama alım ~53 Hz, oyunun adımı ~64 Hz.
- **D-2 — açıklandı (0.3.1):** `CarOrdinal = 0` yalnız boş paketlerde görülüyor. Kayıtta 1418/1418 `IsRaceOn = 0` paketinin ordinal'i 0, 24 285 sürüş paketinin hepsi gerçek araç kimliği taşıyor; 30 dakikalık sayaçlarda da `accepted_car_ordinal_zero` = `accepted_race_off` = 35 255. 0.3.0'daki `car_identity` modeli bu oturumda yalnız **bir** araç değişimi olayı üretti (`2010 → 4210`, kullanıcının gerçek araç değişimi); 0.2.0 benzer bir oturumda 40 sahte olay üretmişti. Boş paketler 10 s'den uzun sürünce bir kez `car_unavailable` yazılır. Eşikler (20 örnek + 0.3 s onay, 10 s) şimdilik değişmedi.

## Gerçek oyunla doğrulama listesi

1. Data Out açık; IP ve port uygulamayla aynı. Diagnostics → *Lengths seen* yalnız `324` göstermeli.
2. Rölanti, ileri vites, geri vites ve mümkünse boş viteste kısa sürüş → `Gear` ham değerleri (P-3).
3. Gaz/fren tam bas-bırak; direksiyon iki uç (−127/127 mi, −128 görülüyor mu? P-6).
4. Lastik sıcaklığını oyun içi telemetri ekranıyla aynı anda karşılaştır (P-4).
5. Menü/pause, dönüş, rewind, yarış bitişi, araç değişimi → *Events* listesi ve stale davranışı.
6. *Trailing byte 324* dağılımı (P-1).

Sonra bu oturumdan kişisel veri içermeyen en küçük regression fixture çıkarılıp `fixtures/real/` altına manifest notuyla eklenmeli.
