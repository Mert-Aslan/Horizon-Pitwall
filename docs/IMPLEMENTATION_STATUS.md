# Uygulama durumu

Son güncelleme: 16 Kasım 2026 · **Sürüm 0.3.1** (backend `APP_VERSION` 0.3.1, frontend `package.json` 0.3.1, aynı build'de). Çalışan build'in kimliği `GET /api/v1/build` ve Diagnostics → Connection'da görünür.

## Güncel durum

| Milestone | Durum | Not |
|---|---|---|
| Faz 0–2 (0.2.0) | ✅ | veri doğruluğu, Live kokpit, Diagnostics; aşağıda "Geçmiş" |
| **M1** akış/config/segment + tanılama yakalaması + build kimliği | ✅ | D-1 ve D-2 **gerçek veriyle karara bağlandı** (0.3.1) |
| **M2** manuel kayıt + Sessions + kurtarma | ✅ | **gerçek oyunla doğrulandı**: 484 s, 25 703 örnek, 0 düşen, dosya kontrolü geçti |
| M3 istemciye özel replay | ⏳ başlanmadı | Sessions'ta replay düğmesi bilinçli olarak yok |
| M4–M6 | ⏳ | |

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

**Fail-first:** `tests/test_regressions_api_stable.py` yalnız 0.2.0'da da bulunan API'yi kullanır. Baseline commit `716cbc5` üzerinde (geçici git worktree) **6/6 başarısız**, 0.3.0'da **6/6 geçer**. F-01 için aynı backend'le iki UI bundle'ı gerçek Chromium'da karşılaştırıldı (yukarıdaki tablo). F-03'ün yeni testleri yeni test edilebilir arayüzü (enjekte edilen ortam) kullandığı için baseline'da çalıştırılamaz; o bulgunun "önce"si kod incelemesidir.

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
| `python -m pytest` | **195 passed** (decoder/şema 83, hub 15, segment/D-1/D-2/sınırlar 16, fail-first regresyonları 6, config 21, recorder 13, kayıt/build/capture API 8, UDP+API+WS 11, sentetik fixture 11, **gerçek oyun fixture'ları 11** — parametrik testler ayrı sayılır) |
| `cd frontend; npm test` (vitest) | **35 passed** (21 mantık + 14 akış istemcisi: F-01, F-03, boş paketler, yaşam döngüsü, akış kontrolü, doğrulama) |
| `npx tsc --noEmit`, `npm run build` | temiz; build `dist/build-manifest.json` üretir |
| `python tools/ui_smoke.py` | **65/65** gerçek tarayıcı kontrolü (0.3.1: boş paketlerde "No driving data", son değerlerin soluk kalması, açıklama banner'ı, veri dönünce Receiving) (headless Chromium, izole portlar 18100/15620), arka arkaya 2 kez; süreçler PID ile kapatıldı, portlar boş doğrulandı |
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

**M3 — istemciye özel replay:** Sessions'tan bir kaydı açıp oynatma/duraklatma/0.5–2× hız/sarma; zaman tabanı kayıttaki `el`, veri `Store.iter_lines` aralık okumasıyla sınırlı parçalar halinde; seek için generation id; canlı akış ve süren kayıt etkilenmez; boş paket aralıkları ve boşluklar görünür kalır.

## Açık işler

- Eşit zaman damgalı yeni içerik için gerçek byte fixture'ı (bir *Diagnostic capture* yeterli); araç kimliği eşiklerinin farklı araçlarla gözden geçirilmesi.
- Hangi oyun ekranının boş paket, hangisinin hiç paket göndermediği (menü/pause/fotoğraf modu/yükleme) eşlenmedi.
- WinError 10054 tetikleyicisi yeniden üretilemedi.
- RSS eğilimi için uzun soak.
- Masaüstü tarayıcıda gerçek simge durumu/arka plan testi (headless sınırı).
- Gerçek %200 tarayıcı yakınlaştırması ve farklı DPI (şimdiye kadar 960×540 viewport eşdeğeri kullanıldı; birebir aynı değil).
- Windows paketleme, katalog, replay, analiz (M3–M6).
- Kayıt silme/arşivleme arayüzü bilinçli olarak yok; kayıtlar `<data_dir>/recordings` altında ve yalnız elle silinir.

## Sürüm kontrolü

Proje klasöründe yerel bir git deposu oluşturuldu (uzak depo yok, hiçbir şey yayımlanmadı). `716cbc5` = 0.2.0 baseline (bu turun geri dönüş noktası). `legacy/forza_horizon_6_telemetry.original.py` ilk prototiptir; 0.2.x'in geri dönüş noktası değildir.

---

## Geçmiş — 0.2.0 (16 Kasım 2026, tarihsel kayıt)

Aşağıdaki sayılar 0.2.0 dönemine aittir ve olduğu gibi korunmuştur; güncel durum yukarıdadır.

### Faz özeti (0.2.0)

| Faz | Durum | Not |
|---|---|---|
| 0 — Baseline | ✅ | Orijinal dosya `legacy/` altında byte-identik (SHA-256 `f9e5cbf7…388a7`); `tools/legacy_baseline.py`; `docs/screenshots/00-legacy-baseline-1920.png`. |
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
