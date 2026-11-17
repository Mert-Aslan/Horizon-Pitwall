# Uygulama durumu

Son güncelleme: 17 Kasım 2026 · **Sürüm 0.7.0** (backend `APP_VERSION` 0.7.0, frontend `package.json` 0.7.0, aynı build'de). Çalışan build'in kimliği `GET /api/v1/build` ve Diagnostics → Connection'da görünür.

## Güncel durum

| Milestone | Durum | Not |
|---|---|---|
| Faz 0–2 (0.2.0) | ✅ | veri doğruluğu, Live kokpit, Diagnostics; aşağıda "Geçmiş" |
| **M1** akış/config/segment + tanılama yakalaması + build kimliği | ✅ | D-1 ve D-2 **gerçek veriyle karara bağlandı** (0.3.1) |
| **M2** manuel kayıt + Sessions + kurtarma | ✅ | **gerçek oyunla doğrulandı**: 484 s, 25 703 örnek, 0 düşen, dosya kontrolü geçti |
| **M3** istemciye özel replay | ✅ | gerçek 8 dk kaydın kopyasıyla tarayıcıda doğrulandı |
| **M4** Telemetry Lab + olaylar + notlar + CSV | ✅ | gerçek kaydın kopyasıyla tarayıcıda doğrulandı |
| **M5** performans denemeleri + iki denemeyi karşılaştırma | ✅ (ilk sürüm) | gerçek kayıtta 17 geçerli deneme; mesafe/rota bazlı delta yok |
| **M6** araç adları (Garage) + Windows kurulum/başlatma + güncelleme/geri dönüş | ✅ | internetsiz kurulum ağ kesikken temiz klasörde doğrulandı |

Kanıt türleri ayrı tutulur: **kod bulgusu**, **deterministik test**, **gerçek tarayıcı testi** (headless Chromium), **pasif oyun gözlemi** (kullanıcının çalışan örneğinden salt okuma), **kontrollü gerçek oyun doğrulaması** (henüz yok).

### Bulgu → çözüm (inceleme belgesi F-01…F-08)

| Bulgu | Doğrulama (önce) | Çözüm | Kanıt (sonra) |
|---|---|---|---|
| F-01 watchdog render zamanına bakıyor | gerçek tarayıcı: 0.2.0 bundle'ında rAF tutulurken 8 s'de 5 kopma/6 soket | taşıma canlılığı (`lastRxAt`) mesaj anında; watchdog yalnız ona bakar; start/stop/dispose idempotent | vitest (gizli sekme, gerçek sessizlik, yaşam döngüsü); gerçek tarayıcı: 0.3.0'da 8 s'de 1 soket, 0 kopma; `ui_smoke` kontrolü |
| F-02 history kaynaklar arası karışıyor | test, baseline'da başarısız | stream segment; segmentte geçmiş/hız/araç/sıra sıfırlanır; geçmiş adet sınırlı | `test_regressions_api_stable`, `test_segments` |
| F-03 sequence/raw kimliksiz | kod bulgusu (0.2.0 store test edilemezdi) | `(server_instance_id, stream_segment_id, sequence)`; raw ayrı örnek, akış değişince temizlenir | vitest: A→B aynı sequence, sunucu yeniden başlama, raw izolasyonu |
| F-04 config tür doğrulaması yok | test, baseline'da 4/4 başarısız | sıkı doğrulayıcı (bool/int/finite/host/liste/enum), JSON NaN reddi, satır numaralı JSON hatası, CLI ve JSON aynı yoldan | `test_config` (21), `test_regressions_api_stable` |
| F-05 / D-1 eşit zaman damgası | kod bulgusu + pasif gözlem | 4 sınıf, tanılama yakalaması, yapılandırılabilir politika (varsayılan değişmedi) | `test_segments`, `test_api_recording`; **gerçek veri bekleniyor** |
| F-06 / D-2 ordinal 0 sahte araç değişimi | test, baseline'da başarısız | `car_identity` histerezis modeli | `test_segments` (5 senaryo); eşikler geçici |
| F-07 sınırsız yapılar | test (200 kaynak → 200 kayıt) | kaynak TTL + üst sınır + taşma sayacı; geçmiş adet sınırı; yük aracı bellek sınırlı | `test_segments`; loadtest JSON serileri |
| F-08 metrik adları, sürüm, belge çelişkileri | kod/belge bulgusu | `ui_rate_limited_frames` ≠ kayıp; akış/recorder sayaç havuzları ayrı; tek sürüm; build kimliği; bu belge yeniden düzenlendi | `test_api_recording::test_build_identity`; Diagnostics |
| WinError 10054 logu | kullanıcının logunda 1 kez (15:20:50) | yalnız tam bu durum tek satır INFO + sayaç; diğerleri varsayılan işleyicide | yeniden üretilemedi (0.2.0 ve 0.3.0 backend, 4 desen × 5); **tetikleyici açık** |

**Fail-first:** `tests/test_regressions_api_stable.py` yalnız 0.2.0'da da bulunan API'yi kullanır. 0.2.0 baseline commit'i (geçmişteki ilk commit) üzerinde (geçici git worktree) **6/6 başarısız**, 0.3.0'da **6/6 geçer**. F-01 için aynı backend'le iki UI bundle'ı gerçek Chromium'da karşılaştırıldı (yukarıdaki tablo). F-03'ün yeni testleri yeni test edilebilir arayüzü (enjekte edilen ortam) kullandığı için baseline'da çalıştırılamaz; o bulgunun "önce"si kod incelemesidir.

### 0.7.0 — M6 araç adları ve kurulum

- **Garage (araç adları):** oyun yalnız `CarOrdinal` gönderir; Pitwall hazır bir ad listesiyle **gelmez** (bu proje için doğrulanmış, lisanslı bir FH6 kaynağı yok; tahmin yanlış etiket olurdu). Adlar kullanıcının yazdıklarıdır veya kullanıcının seçtiği bir JSON listesinden içe aktarılır. Her kayıt kaynağını (`user` / `import:<etiket>` + sürüm, oluşturma/güncelleme zamanı) taşır. İçe aktarma kullanıcının adını asla ezmez; başka bir listenin adıyla çakışmayı değiştirmeden raporlar; aynı listenin yeni sürümü kendi kayıtlarını günceller. Negatif (sentetik) ve 0 kimlikler reddedilir. Adlar üst şeritte (kaynağıyla), Sessions'ta, Runs'ta ve karşılaştırmada kullanılır; güç/PI/çekiş her zaman telemetriden gelir. Kayıtlarda görülen araçlar (özetlerden) ve o an pistteki araç Garage'da listelenir.
- **Kurulum:** `Install Pitwall.cmd` uygulama klasöründe özel bir `.venv` oluşturur (`wheels` klasörü varsa internetsiz, yoksa pypi.org'dan); `Start Pitwall.cmd` Python/paket kontrolü yapar, anlaşılır mesajla durur, `--open` ile başlatır ve ek argümanları geçirir. Yolda boşluk olabilir (bu projenin yolunda var; ilk sürümde tırnak hatası bulunup düzeltildi). `tools/build_release.py` yalnız gerekli dosyalarla klasör + zip ve SHA-256'lı `MANIFEST.json` üretir.
- **Veri güvenliği:** kayıt veritabanı ve araç kataloğu şema sürümü taşır; daha yeni bir sürümün yazdığı veriyi eski sürüm **değiştirmez** (kayıt/ad özellikleri kapanır, canlı görünüm çalışır, log'a neden yazılır). `--paths` ve `GET /api/v1/paths` veri, log, kayıt, yakalama ve katalog yollarını gösterir; Diagnostics'te de var.
- **Rehber:** [INSTALL.md](INSTALL.md) — kurulum, veri konumu, yedek, güncelleme (yeni klasöre), geri dönüş, kaldırma, sorun giderme, paket oluşturma.
- **Doğrulama:** paket (`HorizonPitwall-0.7.0.zip`, 0.2 MiB, 37 dosya) geçici bir klasöre açıldı; `Start Pitwall.cmd --paths` boşluklu yolda doğru çalıştı; paketteki uygulama izole portlarda başlatıldı: `/api/v1/build` backend özeti ve UI build kimliği `MANIFEST.json` ile **eşleşti**, arayüz servis edildi, tüm özellikler açık; yalnız o süreç kapatıldı, port boşaldı. **İnternetsiz kurulum (kullanıcı izniyle, 17 Kasım 2026):** `build_release.py --wheels` 14 paketi pypi.org'dan indirdi (3.4 MB). Zip boşluklu yeni bir klasöre açıldı, `Install Pitwall.cmd` ağ bilerek kesikken (geçersiz proxy ve paket dizini) 12.1 s'de kurdu; `.venv` sistem paketlerini görmüyor. `Start Pitwall.cmd` bu ortamla çalıştı; uygulama izole portlarda başladı, kimliği manifestle eşleşti, UDP alındı, arayüz açıldı; süreç ve port temizlendi. Bulunan risk ve çözüm: derlenmiş paketler Python sürümüne özgü ve bu makinede 3.12 + 3.13 var; paket artık `wheels\PYTHON_VERSION.txt` taşır, kurulum `py -<sürüm>` seçer ve uyuşmazlıkta açık mesajla durur. Not: test ortamında `cmd /c` dosyayı yalnız tam yolla buldu (`NoDefaultCurrentDirectoryInExePath` açık); çift tıklamayı etkilemez.

### 0.6.0 — M5 performans denemeleri ve karşılaştırma

- **Canlı:** Live → Car output altında *Performance run*: tür seç (0–100, 100–200, 100–0 fren), **Arm**; durum sunucudan gelir (Armed / Ready / Measuring + kronometre / sonuç veya "Not valid — neden"). Algılayıcı hub'ın örnek akışında çalışır (kilit altında yalnız bir datagram açılır, G/Ç yok). Sonucu saklamak için sürüşü kaydetmek yeterli: kayıtlar da aynı algılayıcıyla taranır.
- **Kayıtlar:** Sessions → *Runs & comparison*: tüm kayıtlardaki denemeler, tür ve "Valid only" filtresi, en iyi süre işaretli; kullanıcının araç/setup etiketi ve PI yanında. Sonuçlar SQLite'ta `runs` tablosunda (algılayıcı sürümü + örnek sayısı anahtarlı) önbelleklenir; süren kayıt önbelleklenmez.
- **Karşılaştırma:** iki deneme seçilince fark ("B 0.11 s yavaş · 3.6 m uzun"), kendi başlangıçlarına hizalı hız eğrileri, tablo (süre, mesafe, araç, setup etiketi, PI, tarih, en büyük örnek boşluğu) ve sınırlar: farklı araç / PI, demo verisi, eksik setup etiketi, verideki olmayan koşullar (yol, yüzey, hava, eğim).
- **Kurallar ve geçersizlik:** [PROTOCOL.md](PROTOCOL.md#performans-denemeleri-m5). Testler 15 senaryoyu kapsar: interpolasyonlu süre ve mesafe, boşluk arkasından başlangıç uydurmama, beş veri sorunu, ölçüm sırasında boşluk, yanlış başlangıç (20 km/h altı gürültü sayılır), geri gitme, 100–200 kuralları, fren kuralları, viraj frenlemesinin deneme sayılmaması, canlı kurma.
- **Gerçek kayıt (kopya, izole örnek):** 0–100 km/h 1.71 s / 2.62 s, 100–200 km/h 15 geçerli deneme (1.48–1.94 s), veri sorunuyla biten 2 frenleme; ilk tarama 1.3 s, sonra önbellekten.
- **Bulunan ve düzeltilen:** kanvas çizim durumu (metin hizası) yeniden çizimler arasında kalıyordu ve karşılaştırma grafiğinin eksen etiketlerini yarıya kesiyordu; `fitCanvas` artık her çizimde varsayılanlara döner (G-G'yi de etkiliyordu).

### 0.5.0 — M4 Telemetry Lab

Sessions → **Analyze** bir kaydı inceleme ekranında açar:

- **Arayüzdeki kanallar**: hız, devir, vites kodu, gaz/fren/debriyaj/el freni/direksiyon, güç/tork/boost, yanal/boyuna/dikey ivme, yaw hızı, dört teker combined slip, süspansiyon, lastik sıcaklığı (ham, birim belgesiz), dünya X/Y/Z. Her kanalın birimi, notu ve "veri olan örnek" oranı görünür; en çok 6 kanal aynı anda çizilir, seçim tarayıcıda saklanır (bozuk depolamada varsayılana döner).
- **Grafik:** kanal başına şerit, ortak zaman ekseni ve imleç (fare/klavye), sürükleyerek yakınlaştırma, "Zoom out" / "Whole recording", veri olmayan aralıklar renkli (kırmızımsı: örnek yok; amber: boş oyun paketi), olay ve not işaretleri. Çizim piksel sütunu başına **min–max zarfıdır** (tek örneklik tepe kaybolmaz; test edildi). İstatistik tablosu (min/ortalama/max/geçerli örnek) seyreltilmiş çizgiden değil, aralıktaki **tüm örneklerden** hesaplanır (tarayıcı testi: tablo max'ı = tam çözünürlük max'ı).
- **Paneller:** seçili aralığın özeti (süre, veri olan süre, hızdan entegre mesafe, tepe/ortalama hız, tam gaz/fren/tutuş kaybı süreleri, tepe ivmeler, vites değişimi), G-G dağılımı (her n'inci örnek çizilir; tepe ve %95 tüm örneklerden), üstten rota (dünya X/Z, boşluk/boş paket/hızla açıklanamayan sıçramada kesilir), devir bandına göre güç (tam gaz, ileri vites, debriyaj ≤ %5, medyan; "dyno değildir").
- **Olaylar:** kayıt sırasında hub'ın yazdıkları (`certain`) + sezgisel tespitler (`heuristic`, kuralıyla): 0.5 s'den uzun veri boşluğu, boş paket aralıkları, segment değişimi, gaz altında ≥ 0.5 s tutuş kaybı (en yüksek slip ve teker), olası "smashable" çarpması (`SmashableVelDiff` > 0.5 m/s). Her olaydan "Show" (aralığa yakınlaş) ve "Replay" (3 s önceden oynat).
- **Notlar:** imleçteki (yoksa aralık başındaki) ana kullanıcı notu ekleme/silme (`annotations` tablosu); grafikte mavi işaret.
- **CSV:** seçili kanallar, seçili aralık, tam çözünürlük (`sample_index, recording_s, game_timestamp_ms, segment, driving_data, …`); boş paketlerde değer hücreleri boş.

Gerçek veriden öğrenilen ve kanallara yansıyanlar:
- İvmeler yerçekimi içermiyor (araç dururken dikey ivme ≈ 0).
- Yanal ivme, hız × yaw hızıyla eğim 0.90 / r 0.94; boyuna ivme hız türeviyle eğim 0.84 / r 0.92 uyuşuyor; birim m/s² ile tutarlı. Değerler yüksek (yanal medyan ~2 g, %99 ~8.6 g) — oyunun fiziği; olduğu gibi gösteriliyor, yorum eklenmiyor.
- `DistanceTraveled` serbest sürüşte negatife düştü (−1118 m); kullanılmıyor, mesafe hızdan hesaplanıyor (gerçek kayıt: 29.9 km / 8 dk).

Ölçüm (gerçek 8 dk kayıt kopyası, izole örnek): sütun önbelleğini kurmak 0.34 s (25 703 örnek, 30 kanal), tüm kayıt için 6 kanallık seri 0.04 s ve ~200 KiB, özet 0.04 s. Önbellek (kayıt, örnek sayısı) anahtarlı LRU, en çok 3 kayıt; büyüyen bir kayıt yeniden kurulur.

### 0.4.0 — M3 replay

Sessions → **Replay** bir kaydı aynı kokpit panelleriyle oynatır: oynat/duraklat, 0.5× / 1× / 2×, zaman çizelgesinde tıklama/sürükleme ve klavye ile sarma (←/→ 5 s, Shift 1 s, Home/End, Space), olaya atlama, "Close replay".

- **Canlıdan bağımsız:** replay yalnız HTTP ile kayıt dosyalarını okur; hub'ın modu değişmez, UDP'ye hiçbir şey gönderilmez, canlı akış ve süren kayıt devam eder (tarayıcı testinde replay sırasında canlı sequence 583 → 652, mod `live`). Üst şerit canlı bağlantıyı `Live: …` olarak ayrıca gösterir; replay'de mavi **Replay** rozeti ve kontrol şeridi hep görünür.
- **Saat:** oynatma başı kayıt saniyesidir (`el`, monotonic). Oyun zaman damgası yalnız bilgi olarak gösterilir; eşit zaman damgalı örnekler kayıt indeksi `i` ile sıralı kalır.
- **Sınırlı veri:** `GET /api/v1/recordings/{id}/frames?from&to` (en çok 60 s / 8000 satır, yalnız kesişen chunk'lar açılır, gzip). Oynatıcı oynatma başının 22 s gerisini ve 12 s ilerisini tutar, ilerisini 5 s kala ister; eskileri atar. Test: 60 s'lik kayıt 2× oynatılırken bellekte en çok ~2 900 satır.
- **Sarma güvenliği:** her sarma bir *generation* artırır; eski pencerenin geç cevabı yeni konumu ezmez (test). Art arda sarmalar 150 ms durulunca tek istek gönderir — bu olmadan hızlı sarmada sunucu iptal edilen istekleri de işlediği için son konum ~5 s gecikmişti (gerçek kayıtla ölçüldü).
- **Boşluklar ve boş paketler:** oynatma başının 250 ms yakınında örnek yoksa "No data was recorded at this moment (gap)", boş oyun paketlerinde (P-9) "The game sent no driving data here"; ikisinde de son gerçek değerler soluk. Zaman sıkıştırılmaz.
- **Ölçüm (gerçek kayıt kopyası, izole örnek):** 12 s'lik pencere 632–646 satır, 276–398 KiB gzip (≈ 2 MiB ham JSON), sunucuda 190–214 ms; 2:00'a atlayıp oynatma, 0:12'deki boş paket aralığı doğru; sayfa JS heap'i ~10–22 MiB.

### 0.3.1 — gerçek oyun oturumunun sonuçları (16 Kasım 2026)

Kullanıcı 0.3.0'ı başlatıp sürdü ve bir kayıt aldı; sayaçlar `GET` ile, kayıt dosyaları salt okunur açılarak incelendi.

| Konu | Sonuç | Değişiklik |
|---|---|---|
| D-1 eşit zaman damgası | 6 402 aynı byte (hepsi IsRaceOn 0) / 217 yeni içerik (hepsi sürüşte, 28–49 alan farklı) | varsayılan `accept_changed`; aynı byte'lar yine reddedilir |
| Boş paketler (yeni bulgu) | IsRaceOn 0 paketlerinin 1418/1418'i TimestampMS dışında tamamen sıfır | P-9: kabul edilir ama veri taşımaz (`empty_packet`); arayüz "No driving data", son değerler soluk; kayıt özetinde ayrı sayılır |
| D-2 ordinal 0 | yalnız boş paketlerde; 24 285 sürüş örneğinin hepsinde gerçek kimlik | model doğrulandı: tek gerçek araç değişimi olayı (2010 → 4210) |
| P-3 vites | 0 = geri (107/107), 11 = vites arası | "R" / "N" + gözlem notu |
| P-1, P-2, P-6 | 324 byte, kuyruk `00`; little-endian tutarlı; direksiyon ±127 | güçlü kanıt; gerçek fixture testleri |
| P-4 lastik sıcaklığı | 94–300 (birim belgelenmedi) | değişiklik yok |
| Kayıt | 484 s, 25 703 örnek, 0 düşen, kuyruk tepe 1, en yavaş flush 13.6 ms, kapsama %100, `verify` OK | — |
| Akış kontrolü (gerçek tarayıcı, 30 dk) | 47 272 frame gönderildi, `flow_withheld_frames` 0, yavaş gönderim 0 | — |

Not: bu kaydın özeti 0.3.0 ile hesaplandı; boş paketleri 0 km/h saydığı için ortalama hız (223.3 km/h) olduğundan biraz düşük. 0.3.1 sonrası kayıtlarda boş paketler hız istatistiğine girmez.

### Akış kontrolü (0.3.0'da bulunan sorun)

Yük testindeki yavaş istemci (5 mesaj/s okuyan) **29.6 s (p50) / 50.6 s (max)** eski veri görüyordu: işletim sisteminin TCP tamponları birikmeyi sunucudan saklıyor. Çözüm: istemci onaylı pencere (6 mesaj, her 2 mesajda `ack`). Sonra: yavaş istemci p50 **1.3 s**, max 1.45 s; normal istemciler değişmedi (30.7 frame/s, p95 1.6 ms). Eski istemciler onay göndermez ve eskisi gibi çalışır. Ayrıntı: [ARCHITECTURE.md](ARCHITECTURE.md#yayın-modeli-akış-kontrolü-ve-ölçüler).

### Çalıştırılan testler (16 Kasım 2026, bu PC)

| Komut | Sonuç |
|---|---|
| `python -m pytest` | **248 passed** (0.7.0: +17 araç adları, içe/dışa aktarma kuralları, şema koruması, görülen araçlar, API) (0.6.0: +15 performans denemeleri, +1 deneme/kurma API'si) (0.4.0: +1 replay pencere API'si; 0.5.0: +18 Lab — kanalların normalizer ile birebir eşleşmesi (gerçek paketler), tepe koruyan seyreltme ve tam istatistik, boşluk/boş paket, mesafe, vites değişimi, G-G, rota kesilmesi, güç filtresi, olay kuralları, CSV, önbellek; +1 Lab API + notlar) (decoder/şema 83, hub 15, segment/D-1/D-2/sınırlar 16, fail-first regresyonları 6, config 21, recorder 13, kayıt/build/capture API 8, UDP+API+WS 11, sentetik fixture 11, **gerçek oyun fixture'ları 11** — parametrik testler ayrı sayılır) |
| `cd frontend; npm test` (vitest) | **47 passed** (0.5.0: +2 ortak grafik yardımcıları; önceki: (21 mantık + 14 akış istemcisi + 10 replay oynatıcısı: sarma doğruluğu, eski pencerenin yeni konumu ezmemesi, 2× sıra ve bitiş, boşluk, sınırlı bellek, boş paket, hata/yeniden deneme, durulan sarma, başlangıç)) |
| `npx tsc --noEmit`, `npm run build` | temiz; build `dist/build-manifest.json` üretir |
| `python tools/ui_smoke.py` | **99/99** gerçek tarayıcı kontrolü (0.7.0: Garage — ad verme, kaynak etiketi, dışa aktarma, içe aktarmada kullanıcının adının korunması, silme) (0.6.0: Live'da kurma/durum/iptal, Runs görünümü) (0.5.0: Lab açılışı, rozet, tablo max = tam çözünürlük max, sürükleyerek yakınlaştırma, kanal ekleme, seçimin yeniden yüklemede korunması, not ekle/sil, CSV, paneller, Lab → Replay) (0.3.1: boş paketlerde "No driving data" …; 0.4.0: replay rozeti, paneller, `Live:` etiketi, oynatma başına göre grafik, 0. saniyede ve sonda değerin kayıtla eşleşmesi, 2× ilerleme, replay sırasında canlı akışın sürmesi, Close replay) (headless Chromium, izole portlar 18100/15620), arka arkaya 2 kez; süreçler PID ile kapatıldı, portlar boş doğrulandı |
| `tools/repro_close_reset.py` | 10 RST → 0 traceback (0.2.0 ve 0.3.0 backend) |

Smoke testine eklenenler: rAF askıdayken veri gelirken yeniden bağlanma olmaması, sayfa tekrar görünür olunca Receiving, Record/Stop'un sunucu durumunu yansıtması, kayıt süresi/örnek sayısı, Sessions listesi/detay/kaydedilen örnek/dosya bütünlüğü/etiket kaydetme/hız özeti, Sessions'ta 390 px taşma yok, **sayfa kapalıyken kaydın sürmesi** (127 örnek) ve yeniden açılan sayfanın kaydı göstermesi, Diagnostics'ten yakalama ve özeti, yeniden başlayan sunucunun yeni instance kimliği, aynı bundle'da sahte "yeni UI" uyarısı olmaması.

Headless Chromium arka plana alınan sekmeyi gizlemiyor (`document.hidden` false kaldı); bu yüzden arka plan koşulu rAF callback'lerini 6 s tutarak üretildi. Gerçek bir masaüstü tarayıcısında simge durumuna küçültme ayrıca denenmedi.

### Performans (bu PC, sentetik akış, `tools/loadtest.py`, izole örnek)

| Senaryo | Sonuç |
|---|---|
| 60 Hz, 90 s (15 s ısınma), 2 normal + 1 yavaş (akış kontrolsüz) + 1 yeniden bağlanan istemci, kayıt açık | normal 30.7 frame/s, p50 0.8 ms, p95 1.6 ms, p99 ~31 ms; yavaş istemci p50 22 s (sorun bulundu); kayıt 5400/5400, 0 düşen; CPU ort. %4.8; RSS 57.3 → 59.2 MiB |
| 60 Hz, 60 s, 1 normal + 1 yavaş, akış kontrolü **yok** (`--no-ack`) | yavaş p50 29.6 s, max 50.6 s |
| 60 Hz, 60 s, 2 normal + 1 yavaş + 1 yeniden bağlanan, akış kontrolü **var** | yavaş p50 1.28 s, max 1.45 s; normal p95 1.6–1.7 ms; CPU ort. %4.1 |
| 120 Hz, 150 s (20 s ısınma), 2 istemci, kayıt açık | 32.2 frame/s, p95 1.3 ms; 17 999 örnek, 0 düşen, kuyruk tepe 2, en yavaş chunk flush 11.1 ms; CPU ort. %5.8, max %37; RSS 57.6 → 61.4 MiB |

Ham ölçümler (saniyelik CPU/RSS dahil): `docs/measurements/*.json`. Gecikme = sunucu alımı → istemci alımı (aynı makine duvar saati); tarayıcı render'ı dahil değil. Oyunun FPS'ine etkisi oyun olmadan ölçülemez; iddia edilmez.

**Bellek, hâlâ açık:** ısınmadan sonra da RSS birkaç dakikada 2–4 MiB artıyor (120 Hz + kayıt: 130 s'de +3.8 MiB). Saniyelik seri artık var ama ölçümler kısa; SQLite sayfa önbelleği, gzip tamponları ve allocator davranışı ayrıştırılmadı. Sıradaki adım: kayıt açık/kapalı 30 dk soak, dakikalık seriyle karşılaştırma.

### Gerçek oyun durumu (alan bazında)

| Konu | Pasif gözlem (16 Kasım 2026) | Kontrollü doğrulama |
|---|---|---|
| 324 byte, 324. byte = `00` (P-1) | 160 848 paket, hepsi 324; kabul edilenlerin hepsinde `00` | 105 059 + kayıt: aynı; gerçek fixture testi |
| Little-endian (P-2) | değerler makul | gerçek fixture'da elle yazılmış offset'lerle doğrulandı |
| Vites kodları (P-3), steer uçları (P-6), lastik sıcaklık birimi (P-4) | — / 127 görüldü / ~98–210 (birim bilinmiyor) | 0 = R, 11 = N (gözlem) / ±127 / 94–300, birim hâlâ belgesiz |
| Eşit zaman damgası (D-1) | %5.4 tekrar, başlangıçta yoğun | **karar verildi** (0.3.1, yukarıda) |
| `CarOrdinal = 0` (D-2) | 4156→0→4156 ve 0→2010→0 desenleri | **açıklandı**: yalnız boş paketler |
| Boş paketler (P-9) | — | 1418/1418, karar verildi |
| Menü/pause/rewind davranışı | stale doğru görüldü (0.2.0) | hangi ekranın boş paket, hangisinin sessizlik ürettiği ayrıca eşlenmedi |
| Kayıt | — | **gerçek oyunla doğrulandı** |

## Sıradaki tek somut iş

Brief'teki M1–M6 kilometre taşları tamamlandı. Sıradaki en değerli iş **gerçek kullanımla doğrulama**: 0.7.0 ile birkaç farklı araçta (en az bir arkadan itişli) kayıtlı sürüş, bilinçli 0–100 / 100–0 denemeleri (Live → Performance run) ve bir *Diagnostic capture*. Bu, olay eşiklerini, deneme kurallarını ve eşit zaman damgası kararını tek araçlık kanıttan çıkarır; ardından mesafe/rota bazlı karşılaştırma (aynı yol ve yön doğrulamasıyla).

## Açık işler

- Eşit zaman damgalı yeni içerik için gerçek byte fixture'ı (bir *Diagnostic capture* yeterli); araç kimliği eşiklerinin farklı araçlarla gözden geçirilmesi.
- Hangi oyun ekranının boş paket, hangisinin hiç paket göndermediği (menü/pause/fotoğraf modu/yükleme) eşlenmedi.
- WinError 10054 tetikleyicisi yeniden üretilemedi.
- RSS eğilimi için uzun soak.
- Masaüstü tarayıcıda gerçek simge durumu/arka plan testi (headless sınırı).
- Gerçek %200 tarayıcı yakınlaştırması ve farklı DPI (şimdiye kadar 960×540 viewport eşdeğeri kullanıldı; birebir aynı değil).
- M5: mesafe/rota bazlı karşılaştırma (aynı yol ve yön doğrulaması) ve kişisel referans turu yok; deneme kuralları tek araçla doğrulandı. 100–200 denemelerinin çoğu normal sürüşten otomatik bulundu, bilinçli test değil.
- Replay/Lab sayfa adresine yazılmaz (yenileyince Sessions'a dönülür). Lab'da imleçteki rota noktası ve çoklu kayıt karşılaştırması yok (M5).
- Sezgisel olay eşikleri (tutuş kaybı, smashable) tek oturumla seçildi; farklı araçlarla gözden geçirilmeli. Gerçek kayıtta 8 dakikada 58 tutuş kaybı olayı çıktı (X sınıfı AWD, sert sürüş) — liste filtrelenebilir.
- Kurulum bu PC'de temiz bir klasörde doğrulandı; Python'u hiç olmayan ayrı bir makinede denenmedi. İnternetsiz paket yalnız Python 3.13 içindir. İmzalı .exe, otomatik güncelleme ve masaüstü kısayolu yok.
- Kayıt silme/arşivleme arayüzü bilinçli olarak yok; kayıtlar `<data_dir>/recordings` altında ve yalnız elle silinir.
- Araç adı listesi yok (bilinçli); paylaşılabilir liste biçimi hazır (`pitwall-car-aliases` v1).

## Sürüm kontrolü

Kod git ile sürümlenir; geçmişteki ilk commit 0.2.0 baseline'ıdır ve sonraki her sürüm bir commit'tir. Depo: <https://github.com/Mert-Aslan/Horizon-Pitwall>.

---

## Geçmiş — 0.2.0 (16 Kasım 2026, tarihsel kayıt)

Aşağıdaki sayılar 0.2.0 dönemine aittir ve olduğu gibi korunmuştur; güncel durum yukarıdadır.

### Faz özeti (0.2.0)

| Faz | Durum | Not |
|---|---|---|
| 0 — Baseline | ✅ | İlk tek dosyalık prototipin davranışı incelendi ve sorunları kayda geçirildi (aşağıdaki tablo); prototip artık depoda tutulmuyor. |
| 1 — Veri doğruluğu | ✅ | Modüler decoder/normalizer, alan kalitesi, sıra/kaynak politikası, sunucu tarafı durum makinesi, görünür bind hatası, yapılandırma, temiz kapanış. |
| 2 — Live ürün | ✅ | React/TS/Vite arayüzü, WebSocket akışı, bağlantı durumları, etiketli demo, Diagnostics + ham inspector, responsive. |

### Eski prototip bulgularının karşılığı (0.2.0)

| Bulgu | Çözüm |
|---|---|
| P0 her uzunluk kabul | yalnız 324 byte; diğerleri reddedilir, canlı snapshot'ı ve paket yaşını değiştirmez |
| P0 `Speed=None` → `SpeedKmh=0` | `null` ≠ `0`; `or 0` yok |
| P0 NaN rpm → %100 | finite kontrolü önce; `rpm_fraction` `null` |
| P0 koşulsuz `connected=True` | receiver durumu ile paket sağlığı ayrı |
| P0 bind/recv try dışında | bind hatası senkron, UI'de mesajla |
| P1 eskimeyen durum, duvar saati karşılaştırması | monotonic paket yaşı sunucuda |
| P1 steer −128 → −100.787 | ham korunur, normalize −1, `out_of_range` |
| P1 üst üste fetch | tek WebSocket, yeniden bağlanma + watchdog |
| P1 raw tablo her frame `innerHTML` | ayrı Diagnostics, ~4 Hz raw aboneliği |
| P1 snapshot'ı 3 haneye yuvarlama | tam hassasiyet; yuvarlama yalnız `format.ts` |
| P1 `gearLabel(0)` = R/N | `0` + "R/N code, unverified" |
| P1 lastik sıcaklığı birimi | birimsiz gösterim + açık not |
| P1 `abs` direksiyon | merkez sıfırlı çift yönlü bar |
| P2 iki hız alanı, sabit 420 ölçek, eşit kartlar | tek ana hız; hiyerarşik düzen; otomatik ölçek |
| P2 `outline:none`, placeholder-label, TR/EN karışık | focus halkası, `<label>`, arayüz İngilizce |
| P2 `startswith` route | tam path; bilinmeyen 404 |

### 0.2.0 testleri ve ölçümleri (tarihsel)

- İlk teslim: pytest 116, vitest 15; tarayıcı kontrolleri o sırada playwright-cli ile elle yapıldı. UI geçişinden sonra: pytest 118, vitest 20, `tools/ui_smoke.py` 40/40 (otomatik).
- Yük (0.2.0): 60 Hz / 30 dk → 30.7 frame/s, p95 1.6–2.4 ms, CPU ort. %4.0, RSS 52.1 → 55.4 MiB; 120 Hz / 10 dk → 32.2 frame/s, p95 1.0 ms, RSS 50.5 → 55.2 MiB. İlk sürümde sabit `sleep(1/30)` döngüsü Windows'ta 21.6 frame/s veriyordu → olay tetiklemeli yayın.
- UI geçişi önce → sonra (profiling build, 60 Hz, 60 s): rAF/s 90.4 → 59.3; rAF ms/s 17.9 → 9.7; CDP Script 30.8 → 24.4 ms/s; Layout 9.4 → 9.9 ms/s; React commit ort. 0.18 → 0.20 ms; long task 0; heap 4.8 → 9.1 / 4.3 → 5.8 MiB. Görüntüler: `docs/screenshots/compare/`.
- UI geçişinde `frontend-design` ve `webapp-testing` skill'leri yüklenip uygulandı; `with_server.py` Windows'ta sunucuyu kapatmadığı için `tools/isolated_server.py` yazıldı. Claude Design kullanılmadı.
