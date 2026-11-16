# Uygulama durumu

Son güncelleme: 16 Kasım 2026 · Sürüm 0.2.0 + UI geçişi (frontend 0.2.1)

## Faz özeti

| Faz | Durum | Not |
|---|---|---|
| 0 — Baseline | ✅ | Orijinal dosya `legacy/` altında byte-identik (SHA-256 `f9e5cbf7…388a7`); `tools/legacy_baseline.py` onu izole portlarda sentetik veriyle çalıştırır; ekran görüntüsü `docs/screenshots/00-legacy-baseline-1920.png`. Resmi FH6 belgesi okundu, kararlar/bilinmeyenler `PROTOCOL.md`'de; sentetik fixture üretici `tools/make_fixtures.py`. |
| 1 — Veri doğruluğu | ✅ | Modüler decoder/validator/normalizer, alan kalitesi, sıra/kaynak politikası, sunucu tarafı durum makinesi, görünür bind hatası, yapılandırma, temiz kapanış, sabit hassasiyet (yuvarlama yalnız UI'de). |
| 2 — Live ürün | ✅ | React/TS/Vite arayüzü, WebSocket akışı, tüm bağlantı durumları, açıkça etiketli demo, Diagnostics + ham inspector, responsive. Replay adapter'ı yok: frame sözleşmesindeki `mode` alanı ve tek `ingest` girişi kayıt/replay'in bağlanacağı nokta; gerçek replay Faz 3. |
| 3 — Kayıt ve analiz | ⏳ başlanmadı | Arayüzde kayıt düğmesi **bilinçli olarak yok** (çalışmayan kontrol gösterilmez); `capabilities.features.recording = false`. |
| 4–6 | ⏳ | |

## Eski bulguların karşılığı

| Bulgu | Çözüm |
|---|---|
| P0 her uzunluk kabul | yalnız 324 byte; diğerleri nedeniyle reddedilir, canlı snapshot'ı ve paket yaşını değiştirmez (`test_unsupported_lengths_are_rejected`, `test_invalid_spam_does_not_refresh_age`) |
| P0 `Speed=None` → `SpeedKmh=0` | `null` ≠ `0`; `or 0` yok (`test_measured_zero_speed…`, `test_non_finite_speed…`) |
| P0 NaN rpm → %100 | finite kontrolü önce; `rpm_fraction` `null` (`test_nan_rpm_invalidates_rpm_fraction`) |
| P0 koşulsuz `connected=True` | receiver durumu (starting/listening/error/stopped) ile paket sağlığı (waiting/receiving/stale) ayrı |
| P0 bind/recv try dışında | bind hatası senkron, UI'de mesajla; recv hataları sayılır/raporlanır (`test_port_in_use_is_visible_error`) |
| P1 eskimeyen durum, duvar saati karşılaştırması | monotonic paket yaşı sunucuda; UI aynı modeli kullanır |
| P1 steer −128 → −100.787 | ham korunur, normalize −1, `out_of_range` |
| P1 üst üste fetch | polling kaldırıldı; tek WebSocket, yeniden bağlanma + watchdog |
| P1 raw tablo her frame `innerHTML` | ayrı Diagnostics sekmesi, sabit şema sırası, ~4 Hz raw aboneliği, React text render |
| P1 snapshot'ı 3 haneye yuvarlama | tam hassasiyet; yuvarlama yalnız `format.ts` |
| P1 `gearLabel(0)` = R/N | `0` + "R/N code, unverified" |
| P1 lastik sıcaklığı birimi | birimsiz gösterim + açık not |
| P1 `abs` direksiyon | merkez sıfırlı çift yönlü bar |
| P1 kapanış/yaşam döngüsü | lifespan ile soket kapanışı, port yeniden kullanılabilir; kalıcı kayıt/replay Faz 3 |
| P2 iki hız alanı, sabit 420 ölçek, eşit kartlar | tek ana hız; hiyerarşik düzen; trace ölçeği otomatik |
| P2 `outline:none`, placeholder-label, TR/EN karışık | focus halkası, `<label>`, arayüz tamamen İngilizce |
| P2 `startswith` route | tam path; bilinmeyen 404 |

## Çalıştırılan testler

| Komut | Sonuç |
|---|---|
| `python -m pytest` | **116 passed** (decoder/şema 81 — çoğu parametrik, hub/sıra/durum 13, UDP yaşam döngüsü + API + WebSocket 11, fixture 11) |
| `cd frontend; npm test` (vitest) | **15 passed** (format, steer geometrisi, ring/gap kırılması, backoff, durum türetme) |
| `npm run build` (tsc strict + vite) | temiz |

UDP testleri yalnız işletim sisteminin verdiği geçici portları kullanır; 5607'ye hiçbir şey gönderilmez.

Testlerde **olmayan**: gerçek yavaş istemci/backpressure (TestClient bellek içi; latest-wins mantığı yük ölçümüyle gözlendi), tarayıcıda otomatik UI testi (render'lar elle playwright-cli ile kontrol edildi), LAN modunun loopback-dışı istemciyle denenmesi.

## Gerçek tarayıcı render'ları (playwright-cli, Chromium)

`docs/screenshots/`: 00 eski arayüz · 01 waiting · 02 live demo 1920 · 03 bilinmeyen araç · 04 stale · 05 telefon 390 · 06 diagnostics + geçersiz paket · 07 receiver hatası (port çakışması) · 08 sunucu koptu (donmuş değerler; sunucu yeniden başlatılınca otomatik bağlandı, doğrulandı) · 09 2560/1366/768 · 10 %200 zoom eşdeğeri. Tarayıcı konsolunda hata yok.

## Performans ölçümü

Yöntem: `tools/loadtest.py`; aynı makinede çalışan Pitwall örneğine izole UDP portundan sentetik paket, 2 Python WebSocket istemcisi (+ açık 1 tarayıcı sekmesi). Gecikme = istemci alım anı − `frame.received_at` (aynı makinenin duvar saati); sunucu alımından istemci alımına kadardır, **tarayıcı render'ı dahil değildir** (rAF en çok bir ekran karesi ekler). CPU/RSS psutil ile 1 s örnekleme. Donanım: bu geliştirme PC'si (Windows 11, Python 3.13.3). Oyunun FPS'ine etkisi oyun olmadan ölçülemez; iddia edilmez.

| Senaryo | Sonuç |
|---|---|
| 60 Hz, 60 s, ilk sürüm (sabit `sleep(1/30)` döngüsü) | istemci başına 21.6 frame/s (Windows zamanlayıcı çözünürlüğü), p95 30.8 ms → olay tetiklemeli yayına geçildi |
| 60 Hz, 30 s, olay tetiklemeli | 30.6 frame/s, gecikme p50 0.7 ms, p95 1.5–1.7 ms, p99 ~31 ms; CPU ort. %4.0 (tek çekirdeğin), RSS 51 → 52 MiB |
| 60 Hz, 30 dk (108 000 paket) | 30.7 frame/s; gecikme p50 0.7 ms, p95 1.6 / 2.4 ms, p99 ~31 ms, max 55 ms; CPU ort. %4.0, max %21.7; RSS 52.1 → 55.4 MiB |
| 120 Hz, 10 dk (72 000 paket) | 32.2 frame/s; gecikme p50 0.5 ms, p95 1.0 ms, p99 ~15 ms, max 46 ms; CPU ort. %4.5, max %28; RSS 50.5 → 55.2 MiB |

Hedef p95 150 ms; ölçülen değerler bunun çok altında. 120 Hz'te frame/s'nin 30'u biraz aşması, gönderim eşiğinin periyodun 0.9'u olmasından.

**Bellek, açık soru:** Geçmiş tamponu (120 s) ve sayaçlar sınırlı tutuluyor, yine de RSS iki ölçümde de 3–5 MiB arttı ve en yüksek değer bitiş değerine eşitti. Ölçüm yalnız başlangıç/bitiş/maksimum tuttuğu için artışın tamponun dolmasıyla durduğu mu, yoksa yavaş bir sızıntı mı olduğu ayırt edilemiyor. Sıradaki adım: `loadtest.py`'ye dakikalık RSS serisi eklemek ve tekrar ölçmek.

## UI/UX geçişi (16 Kasım 2026)

Skill'ler: `frontend-design` yüklendi ve tasarım kararlarında yönergeleri uygulandı (önce plan + varsayılanlara karşı gözden geçirme, tek cesur öğe = vites/hız/devir enstrümanı, yapısal öğelerin bilgi taşıması, nokta ile birleştirilmiş meta dizilerinin kaldırılması, metin yazımı, ekran görüntüsüyle öz-eleştiri). `webapp-testing` yüklendi; yönergesindeki Python Playwright yaklaşımı kullanıldı. Yardımcı `with_server.py` Windows'ta sunucuyu kapatmadığı için yerine `tools/isolated_server.py` yazıldı (doğrudan süreç, PID + alt süreç ağacı, 5607/8080 reddedilir, sonunda port kontrolü). Claude Design kullanılmadı: bu ortamda yalnız tasarım sistemi eşitleme aracı (`DesignSync`) var, tasarım üretmek için değil.

Değişenler:
- **Cluster** yalnız vites + hız; sabit genişlikli, hız sağa yaslı; etiketler sayının altında. Güç/tork/boost/yakıt ayrı "Car output" paneline indi.
- **Inputs**: debriyaj/el freni kullanıcı kontrollü compact/expanded.
- **Tyres**: Front/Rear × Left/Right ızgarası, slip ölçeği (0–2, 1.0 işareti, doygunluk), histerezisli tutuş kaybı uyarısı (1.0 aç / 0.9 kapat), kısa başlık + açılır açıklama.
- **Grafik**: zaman ekseni ve kılavuzlar, kanal adları/birimleri, ortak imleç + sayısal okuma, klavye (`role="slider"`), duraklat/canlıya dön (pencere kopyalanır, veri alımı sürer), yalnız genişleyen veya sabit hız ölçeği, boşlukta kesilen çizgi, çizim alanına kırpma, CSS zoom'a uyum. Çizim React render'ından ayrıldı: store aboneliği + ResizeObserver; renkler mount'ta bir kez okunur; veri değişmediyse (stale/paused) çizim atlanır; pencere başlangıcı ikili arama ile bulunur.
- **Timing**: tur verisi yoksa tek cümle; `IsRaceOn` ham bayrak olarak.
- **Focus view** (üst şerit, tercih yerelde).
- **Stale**: panel opaklığı yerine yalnız değerler `text-3`'e, barlar %35'e (kontrast korunur).
- **Geri bildirim**: demo düğmelerinde bekleme/hata durumu, 403 için anlaşılır mesaj.
- Üst şerit: Hz/paket yaşı küçültüldü; bilinmeyen araç nötr `name not known yet` etiketiyle.

Bulgu sınıflandırması (geçiş öncesi): tarayıcıda gözlenen — cluster boşluğu ve kopuk etiketler, eşit ağırlıklı debriyaj/el freni, lastik konum başlıkları yok, yarış panelinde "—" satırları, trace'te eksen/imleç yok ve hız ölçeği sıçraması (0–200 ↔ 0–250). Koddan doğrulanan — her karede `getComputedStyle` + geçmiş kopyası + `clientWidth` okuması, stale'de yeniden boyutlanınca çizim yenilenmemesi, %50 opaklıkta ~3:1 kontrast, demo düğmesinde geri bildirim yokluğu, eşik çevresinde titreyebilen kırmızı kenar. Ölçümle sınanan — çizim/commit maliyeti ve heap (aşağıda).

### Testler

| Komut | Sonuç |
|---|---|
| `python tools/ui_smoke.py` | **40/40** (eski 11 kontrol + Focus ve kalıcılığı, dört ayrı lastik hücresi, klavyeyle inceleme ve `aria-valuetext`, duraklatma sırasında veri alımının sürmesi ve resmin sabit kalması, canlıya dönüş, compact/expanded, demo durdurma, waiting→receiving→stale ve soluk değer rengi, stale'de yeniden boyutlanma, sunucu durdurma → "Server unreachable" → yeniden başlatma → otomatik bağlantı, 390 ve 960 px taşma, telefonda gaz ilk ekranda, konsol hatası yok). Her çalıştırmada başlatılan süreçler PID ile kapatıldı, test portları boş doğrulandı. |
| `npm test` (vitest) | **20/20** (5 yeni: en yakın örnek, boşlukta "no data", ölçek kuralı, ring ikili arama/snapshot/sürüm/peak, histerezis) |
| `npx tsc --noEmit` | temiz |
| `python -m pytest` | **118/118** (`PITWALL_UI_DIST` ortam değişkeni; tekrar analizi ve IsRaceOn/ordinal-0 sayaçları için 2 yeni test) |

### Ölçüm (önce → sonra)

`tools/ui_capture.py`, profiling build (`npm run build:profile`), 1920×1080 headless Chromium, 60 Hz deterministik sentetik akış, 5 s ısınma sonrası 60 s, aynı makine:

| Ölçü | Önce | Sonra |
|---|---|---|
| requestAnimationFrame çağrısı / s | 90.4 | 59.3 |
| rAF callback süresi (ms / s) — çoğu trace çizimi | 17.9 | 9.7 |
| CDP ScriptDuration (ms / s) | 30.8 | 24.4 |
| CDP Layout (ms / s) | 9.4 | 9.9 |
| React commit ort. / p95 / max (ms) | 0.18 / 0.3 / 1.7 | 0.20 / 0.3 / 1.3 |
| Long task (> 50 ms) | 0 | 0 |
| JS heap (CDP, 5 s örnekler) | 4.8 → 9.1 (max 9.9) MiB | 4.3 → 5.8 (max 7.2) MiB |

Heap testere dişi (GC) gösteriyor; 60 s'lik ölçüm uzun vadeli eğilim için yetersiz. Layout maliyeti değişmedi (stil genişliği güncellemeleri); hedeflenmedi. Headless ve profiling build ölçümleri gerçek GPU'lu tarayıcıdan farklı olabilir. Oyun FPS'ine etkisi ölçülmedi.

Karşılaştırma görüntüleri: `docs/screenshots/compare/` — `before-live-*` / `after-live-*` (1920×1080, 2560×1440, 1366×768, 768×1024, 390×844, 960×540), `*-stale-1920x1080`, ayrıca 8080'deki gerçek oturumun geçiş öncesi görüntüleri `user-8080-*`. Etkileşim görüntüleri: `docs/screenshots/smoke/`.

## Doğrulanamayanlar / açık işler

- **real-game validation pending**: gerçek FH6 akışı hiç alınmadı. Bekleyenler: 324. byte (P-1), byte sırası (P-2), vites kodları (P-3), lastik sıcaklık birimi (P-4), steer uçları (P-6), gerçek gönderim hızı ve menü/rewind davranışı. Kontrol listesi: [PROTOCOL.md](PROTOCOL.md#gerçek-oyunla-doğrulama-listesi).
- Claude Design kullanılmadı; A/B mockup karşılaştırması yapılmadı (bkz. DESIGN_SYSTEM.md).
- Windows paketleme/exe yok (Faz 6).
- Git deposu yok; geri dönüş noktası `legacy/` kopyasıdır.

- **D-1 (gerçek veri):** aynı `TimestampMS` taşıyan paketler reddediliyor (ilk okumada %12, oturum boyunca %5.4; tekrarlar belli anlarda yoğunlaşıyor). İçerik karşılaştırma sayaçları eklendi ve sentetik tekrarlarla uçtan uca doğrulandı (6 aynı / 4 farklı birebir sayıldı). Gerçek veri için Pitwall'ın yeniden başlatılması gerekiyor; karar kuralı PROTOCOL.md'de.
- **D-2 (gerçek veri):** `CarOrdinal = 0` taşıyan paketler `car_change` olaylarını şişiriyor; sayaç eklendi, yorum gerçek veriyi bekliyor.

## Sıradaki tek somut iş

Faz 3'ün ilk dikey dilimi: **manuel kayıt** — `POST /api/v1/sessions/start|stop`, receiver'dan ayrı sınırlı kuyruklu recorder thread'i, oturum başına SQLite metadata + gzip'li JSONL blokları (normalize frame + ham 324 byte), kuyruk/disk hatası sayaçları ve UI'de kayıt düğmesi/durumu; ardından aynı `ingest` sözleşmesini kullanan replay.
