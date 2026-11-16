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
| P-3 | `Gear` ham değeri korunur. 1–10 numarasıyla gösterilir; **0 "R/N code, unverified"** olarak ham gösterilir, R veya N denmez. Diğer değerler "unrecognised gear code". | Belge yalnız "Current gear" diyor. |
| P-4 | Lastik sıcaklığı **birimsiz, dönüştürülmeden** gösterilir; arayüz "temperature unit not documented by the game" yazar. | Belge yalnız "Tire temperature" diyor. Celsius/Fahrenheit dönüşümü uydurulmaz. |
| P-5 | `CarClass` harfleri C/B/A/S1/S2/R gelenekten gelir; belge yalnız 0 = D ve 7 = X'i adlandırır. Aralık dışı kodlar etiketsiz kalır ve işaretlenir. | |
| P-6 | `Steer` −128 temsil edilebilir ama belge dışıdır: ham −128 korunur, normalize değer −1'e sınırlanır, `out_of_range` işaretlenir. Aynı kural `NormalizedDrivingLine` ve `NormalizedAIBrakeDifference` için geçerlidir. | Eski prototip −100.787 % üretiyordu. |
| P-7 | Belgelenmiş aralık dışındaki değerler (PI, CarClass, Drivetrain, Fuel, süspansiyon, 0/1 bayraklar) atılmaz; normalize katmanda tutulur (enum'larda etiket `null`) ve `quality.fields` içinde `out_of_range` olarak işaretlenir. Negatif güç/tork fiziksel olarak mümkündür ve işaretlenmez. | |
| P-8 | Tur süresi 0.0 → normalize değer `null`, kalite `not_applicable`. Ölçülmüş sıfır hız 0.0 kalır. NaN/±Inf → `null` + `non_finite`; bağımlı türetilmiş değerler de `null` olur. | Eski `or 0` zincirlerinin yerine. |

## Zaman damgası, sıra ve oturum

`TimestampMS` (U32, ms) — kural [`hub.py`](../backend/telemetry/hub.py) başında:

- daha yeni → kabul; eşit → `duplicates`; önceki 2³²−600 000 üstünde ve yeni < 600 000 → **wraparound** (kabul);
- en fazla 2000 ms geride ve son geçerli paket < 1 s önce → **out_of_order** (red, canlı snapshot'ı ezmez);
- daha geride → **reset adayı**. Tek bir geri gidiş hiçbir şeyi sıfırlamaz; 3 ardışık ve kendi içinde artan aday gelirse reset kabul edilir. Arada orijinal akış devam ederse adaylar atılır.

Gönderen adresi oturuma bağlanır. Başka bir adresten gelen geçerli paketler, bağlı kaynak 3 s susana kadar `foreign_source` olarak reddedilir ve Diagnostics → *Ignored senders* altında listelenir. Yeni kaynak = yeni `session_id`. Bu fazdaki `session_id` bir "canlı akış segmenti"dir; reset/araç değişimi yalnız olay olarak kaydedilir. Kayıt fazında (Faz 3) bunlar birlikte değerlendirilecek.

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

**Gerçek oyun yakalaması (fixture) henüz yok** (`real-game validation pending`).

### Pasif gözlem — 16 Kasım 2026 (yalnız okuma)

Kullanıcının 8080'de canlı modda çalışan örneğinden, durumuna dokunmadan `GET /api/v1/diagnostics` ve `/telemetry/latest` okundu. Bu kontrollü bir doğrulama oturumu değil; kanıt niteliği sınırlı:

- 66 611 paketin **tamamı 324 byte**; kabul edilen 58 436 paketin **324. byte'ı her zaman `00`** (P-1 için güçlü işaret: dolgu byte'ı).
- Değerler little-endian okumayla tutarlı ve makul (ör. `EngineMaxRpm` 9999.995, `EngineIdleRpm` 799.9995, `Speed` 39.5 m/s, `Steer` 127 tam sağda, PI 916, `CarClass` 6) — P-2 için işaret.
- Gönderen kaynak portu `5200` (belgedeki "oyun 5200–5300 arasında kendi soketini açar" ile tutarlı); gözlenen hız ~50 Hz, `TimestampMS` ortalama aralığı ~21.7 ms.
- Lastik sıcaklıkları sürüşte ~98–210 aralığında görüldü. Bu Fahrenheit ile uyumlu olabilir, ama birim **hâlâ doğrulanmadı**; dönüşüm yapılmıyor (P-4).
- **Açık bulgu D-1:** ilk okumada paketlerin ~%12'si (66 611'in 8 175'i) bir önceki kabul edilen paketle **aynı `TimestampMS`** taşıdığı için `duplicates` olarak reddedilmişti. Aynı oturumun ikinci okumasında (2 710 s, 160 848 paket) oran **%5.4**'e (8 672) inmişti: tekrarların neredeyse tamamı oturumun ilk dakikalarında birikmiş, sonra ~500 eklenmiş. Yani sürekli değil, belli durumlarda yoğunlaşıyor. Hangi durum olduğu ve içeriğin aynı olup olmadığı o sürümün sayaçlarından anlaşılamıyor.
  - **Ölçüm eklendi (henüz gerçek veriyle çalışmadı):** hub artık her tekrarı önceki kabul edilen paketle karşılaştırıyor: `dup_identical` (byte byte aynı) / `dup_changed` (aynı zaman damgası, farklı içerik), tekrar anındaki `IsRaceOn`, değişen alanların sıklığı ve 5 örnek. Diagnostics → Packets bunları gösterir; `GET /api/v1/diagnostics` → `duplicate_analysis`. Politika değişmedi: tekrar yine reddedilir ve canlı değeri değiştirmez.
  - Karar kuralı: `changed ≈ 0` ise tekrarlar gerçekten aynı paket, reddetmek kayıpsız. `changed` belirgin ise eşit zaman damgalı yeni içerik kabul edilmeli (UDP localhost'ta sırayı korur; aynı ms içinde gelen sonraki paket daha yeni sayılabilir) ve bu fixture + test ile sabitlenmeli.
  - Toplama: kullanıcının çalışan örneği eski kodla çalışıyor; Pitwall yeniden başlatılıp birkaç dakika (menü/pause dahil) sürülünce sayaçlar dolar.
- **Bulgu D-2:** 40 `car_change` olayının çoğu `4156 → 0 → 4156` biçiminde: zaman zaman `CarOrdinal = 0` taşıyan geçerli paketler geliyor (muhtemelen menü/yükleme anları; doğrulanmadı). Yeni sayaçlar `accepted_car_ordinal_zero` ve `accepted_race_off` bunu `IsRaceOn` ile ilişkilendirmeye yarar. Ordinal 0 doğrulanırsa olay listesinde ve araç kimliğinde "araç yok" olarak ele alınmalı.

## Gerçek oyunla doğrulama listesi

1. Data Out açık; IP ve port uygulamayla aynı. Diagnostics → *Lengths seen* yalnız `324` göstermeli.
2. Rölanti, ileri vites, geri vites ve mümkünse boş viteste kısa sürüş → `Gear` ham değerleri (P-3).
3. Gaz/fren tam bas-bırak; direksiyon iki uç (−127/127 mi, −128 görülüyor mu? P-6).
4. Lastik sıcaklığını oyun içi telemetri ekranıyla aynı anda karşılaştır (P-4).
5. Menü/pause, dönüş, rewind, yarış bitişi, araç değişimi → *Events* listesi ve stale davranışı.
6. *Trailing byte 324* dağılımı (P-1).

Sonra bu oturumdan kişisel veri içermeyen en küçük regression fixture çıkarılıp `fixtures/real/` altına manifest notuyla eklenmeli.
