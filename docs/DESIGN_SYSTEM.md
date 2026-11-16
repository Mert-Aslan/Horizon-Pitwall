# Tasarım sistemi

Yön: **A — kompakt motorsport enstrüman paneli.** Claude Design bu oturumda kullanılmadı (araç bu ortamdan erişilebilir değildi); tasarım doğrudan kodda prototiplenip gerçek tarayıcı render'larıyla (`docs/screenshots`) kontrol edildi. B yönünden (editoryal mühendislik çalışma alanı) yalnız tipografi kararları alındı: büyük sayılarda display kesimi, etiketlerde sentence-case, tabular rakamlar. A/B için ayrı mockup üretilmedi.

Gerekçe: Live ekranı sürüş sırasında göz ucuyla okunur; sabit bilgi hiyerarşisi (devir bandı → vites/hız → pedallar/direksiyon → dört teker → geçmiş) ve az sayıda ortak yüzey, eşit ağırlıklı KPI kartı ızgarasından daha hızlı okunur.

## Token'lar (`frontend/src/styles.css` `:root`)

| Token | Değer | Kullanım |
|---|---|---|
| `--bg` | `#111315` | sayfa |
| `--surface` | `#191C1F` | panel |
| `--elevated` | `#22262A` | bar izleri, teker kartı, buton |
| `--line` / `--line-strong` | `#2C3136` / `#3A4046` | ayırıcı / kontrol kenarı |
| `--text` | `#F2F1ED` | birincil metin, ana sayılar |
| `--text-2` | `#A6ADB4` | etiket, birim |
| `--text-3` | `#858D95` | ölçek notları |
| `--accent` | `#E8B35B` | yalnız aktif sekme, odak halkası, demo rozeti, shift-uyarı bölgesi, stale işareti |
| `--danger` | `#EF6A62` | hata, shift-now bölgesi, slip > 1 |
| `--success` | `#70BC91` | `receiving` işareti |
| `--data-speed/throttle/brake/steer` | `#DCDFE2` / `#70BC91` / `#EF6A62` / `#7FA7D1` | veri serileri (gaz/fren için yerleşik yeşil/kırmızı konvansiyonu) |
| Radius | 4 px (kontrol), 6 px (panel) | pill yalnız gerçek tag'lerde |
| Spacing | 4 / 8 / 12 / 16 / 24 / 32 px | `--s-1` … `--s-6` |
| Font | Segoe UI Variable (Windows yerleşik) → system-ui; Cascadia Mono → Consolas | harici/indirilen font yok, çevrimdışı çalışır |

Sayılar `font-variant-numeric: tabular-nums` (`.num`). Uppercase ve negatif letter-spacing kullanılmaz.

## Ölçülen kontrast (WCAG oranı, düz renk)

| Ön plan | bg | surface | elevated |
|---|---|---|---|
| text | 16.47 | 15.14 | 13.48 |
| text-2 | 8.21 | 7.54 | 6.71 |
| text-3 | 5.53 | 5.09 | 4.53 |
| accent | 9.78 | 8.99 | 8.00 |
| danger | 6.13 | 5.63 | 5.01 |
| success | 8.24 | 7.57 | 6.74 |

Demo rozeti (`#1B1408` / accent) 9.59. Hepsi düz renkte 4.5:1 üzerinde. `stale`/bağlantı kopuk durumda paneller artık opaklıkla soluklaştırılmaz: yalnız sayılar `text-3` rengine (surface üzerinde 5.09:1) iner ve barlar %35 opaklığa düşer; etiketler tam kontrastta kalır. (Önceki sürümde %50 panel opaklığı ikincil metni ~3:1'e düşürüyordu.) Bu bir WCAG uygunluk beyanı değildir; tam denetim yapılmadı.

## Bileşenler

| Bileşen | Kural |
|---|---|
| TopBar | marka, sekmeler (yalnız çalışan ekranlar: Live, Sessions — yalnız sunucu kaydı destekliyorsa —, Diagnostics), araç kimliği, kayıt kontrolü, Focus view düğmesi (yalnız Live'da), demo rozeti, bağlantı durumu. Durum şekil + metinle verilir (renk tek sinyal değil: dolu kare receiving, boş amber kare stale, kesikli kare waiting, kırmızı elmas error/offline). Hz ve paket yaşı küçük ve soluk (`text-3`, 12 px), ölçümlerin önüne geçmez. |
| Car identity | sunucunun `car_identity` durumuna göre: onaylı → `Car #<ordinal>` + `name not known yet`; geçici 0 → son onaylı kimlik + `id not sent right now` (canlı doğrulanmadığı tooltip'te); aday → `checking id`; uzun yokluk → `No car id`. Ham 0 hiçbir zaman araç adı gibi gösterilmez. Demo ordinal'i: `Demo car` + `synthetic`. `Class A`, `PI 700` gibi etiketli alanlar; nokta ile birleştirilmiş meta dizisi kullanılmaz. |
| Banner | tonlar: demo, info (sunucu daha yeni bir arayüz sunuyor + `Reload page`), warn (stale, ikinci gönderici), danger (sunucu kopuk, geçersiz paket, sürüm uyumsuzluğu, okunamayan mesajlar, kayıt başarısız). Ne olduğunu ve ne yapılacağını söyler. |
| Record control | üst şeritte, Focus düğmesinden önce. Kayıt geleneği: boş kırmızı halka `Record`; kayıtta dolu kırmızı nokta + `Stop`, yanında süre ve örnek sayısı, düşen örnek varsa kırmızı. Durum **sunucudan** gelir (WS `status.recorder`); tıklama kendini kayıtta saymaz, `Saving…` sırasında devre dışı. LAN'dan 403 anlaşılır metinle. Telefonda süre/sayı gizlenir, düğme kalır. |
| Sessions | solda kayıt listesi (başlık veya başlangıç zamanı, süre, örnek, tepe hız, kullanıcı araç etiketi; seçili satır amber sol kenar), sağda detay: durum etiketi (`saved`, `recovered after a crash`, `failed`…), tüm kayıt hız zarfı (bucket başına min–max, boşluk boş kalır, ResizeObserver ile yeniden çizim), somut sayaçlar (örnek, düşen, kayıp, segment, kapsama, 250 ms üstü boşluk, tepe/ortalama hız, onaylı araç kimlikleri), etiket formu (başlık, araç, setup/tune, not — "oyun tuning göndermez" notu), `Check files` bütünlük kontrolü, olay listesi. Uydurma kalite skoru yok; replay düğmesi M3'e kadar yok. Boş durum: Record'un nasıl kullanılacağı. |
| Diagnostic capture | Diagnostics'te: 30/60 s düğmesi, "gönderici adreslerini gizle" seçeneği, kalan süre, son yakalamanın sınıf özeti. |
| DemoButton | bekleme durumu (`Starting demo…`, disabled + `aria-busy`) ve hata metni (`role="alert"`; LAN'dan 403 gelirse "this device may only view"). |
| ShiftBand | 48 segment, rpm/max_rpm oranı; %85 üstü amber, %95 üstü kırmızı (genel görsel bölge, araç bazlı ideal vites noktası değildir). Sağda rpm (5ch sabit genişlik) ve alt alta `max` / `idle`. |
| Cluster | yalnız vites ve hız. Vites 2ch, ortalı; hız 3ch, **sağa yaslı**, birler basamağı yerinde kalır (smoke test sağ kenarın sabitliğini ölçer). Birim/etiket (`gear`, `km/h`) sayının altında, 15 px, `text-2`, ağırlık 500. Vites 0: `0` + "R/N code, unverified". |
| Inputs | gaz ve fren büyük dikey bar + yüzde (4ch sabit), merkez sıfırlı çift yönlü direksiyon (`L 17%` / `R 42%`, 6ch). Debriyaj/el freni varsayılan olarak alt kısımda iki ince yatay metre (**compact**); kullanıcı `Expand clutch & handbrake` ile dört dikey bara geçer. Tercih yerelde saklanır; değerlere göre otomatik açılıp kapanmaz. |
| Tyres | 2×2 ızgara; sütun başlıkları Left/Right, satır başlıkları Front/Rear. Her hücrede: ham sıcaklık (birim dönüşümü yok, `temp` etiketi), slip metresi (0–2 ölçek, 1.0'da işaret çizgisi, ≥2'de sağ uçta doygunluk çizgisi, sayısal değer), süspansiyon sıkışması %. Tutuş kaybı uyarısı **histerezisli**: |slip| > 1.0 ile açılır, < 0.9 ile kapanır; kırmızı kenar + `⚠ grip lost` metni. Uzun açıklama `About these values` açılır alanında. İdeal sıcaklık bandı gösterilmez. |
| Traces | son 20 s; şeritler: hız (km/h), gaz + fren (%), direksiyon (R yukarı, L aşağı). Sol kenarda kanal adları kendi renklerinde + ölçek metni; zaman ekseni `−20 s … now`, dikey kılavuz çizgileri. Ortak imleç (fare veya klavye) tüm kanallarda nokta + altta sayısal okuma satırı (At, Speed, RPM, Throttle, Brake, Steering). `Pause` ile pencere kopyalanıp dondurulur (veri alımı sürer; ring buffer üzerine yazsa da kopya kaybolmaz), `Back to live` / Escape ile dönülür. Hız ölçeği: `Auto (only expands)` (50 km/h adımlarla yalnız büyür, en az 100; yavaşlayan araç ölçek küçüldüğü için hızlanıyormuş gibi görünmez) veya sabit 0–200/300/400; tercih yerelde. Boşlukta (> 0.3 s) çizgi kesilir; eksik örnek çizilmez, imleç boşlukta "no data" der. |
| Car output | güç kW (+hp), tork, boost, yakıt. |
| Timing | Tur verisi yoksa (tüm tur süreleri 0.0 = not applicable, pozisyon 0, tur 0) satırlar yerine tek cümle; her zaman Driving time ve Distance. `IsRaceOn` ham bayrak olarak ("Game race flag (IsRaceOn): 1") gösterilir; yarış türü veya serbest sürüş çıkarımı yapılmaz. |
| Focus view | Üst şeritteki düğme (`aria-pressed`), tercih yerelde. Devir bandı, büyütülmüş vites/hız, gaz/fren/direksiyon ve tek satır kompakt lastik şeridi; trace, car output/timing ve debriyaj/el freni gizlenir. Backend/veri değişmez. |
| Empty / Error | sıfır yerine açıklama + oyun ayarı adımları + `Explore with demo data`. |
| Inspector | sabit şema sırası, etiketli filtre, alan/değer/birim/tip@offset/kalite; ~4 Hz. |

## Durumlar

waiting · starting · receiving · stale (soluk + yaş) · error (mesajla) · sunucu kopuk (donmuş değerler + yeniden deneme sayacı) · geçersiz paket · ikinci gönderici · demo · araç kimliği durumları · sürüm uyumsuzluğu · daha yeni UI · okunamayan mesaj · kayıt (idle / recording / saving / failed, düşen örnek) · kayıt listesi boş / yüklenemedi. Replay ve provider durumları özellikleri gelince eklenecek.

## Responsive

| Genişlik | Düzen |
|---|---|
| ≥ 2200 × 1250 px (2560×1440) | 3 kolon, tüm arayüz `zoom: 1.3` ile ölçeklenir; trace canvas'ı zoom katsayısını hesaba katar (net çizim, doğru imleç konumu) |
| > 1400 px (1920×1080) | 3 kolon: [vites/hız \| girişler \| lastikler], altta [trace ×2 \| car output + timing] |
| 901–1400 px (1366×768, %200 zoom) | 2 kolon: [vites/hız \| girişler], [lastikler \| car output], altta trace; vites/hız 12.5vw |
| 701–900 px (768 px tablet) | 2 kolon üstte, lastikler/car output/trace tam genişlik |
| 601–700 px | tek kolon |
| ≤ 600 px (telefon) | üst şerit 3 satır: sekmeler / araç / kayıt + bağlantı durumu. İlk ekranda devir, vites/hız, gaz/fren, direksiyon; ardından teker durumu. Güç satırı, trace ve yarış tablosu gizli. Sessions tek kolon (liste üstte, en çok %40 yükseklik). |
| ≤ 800 px | Sessions tek kolon; ≤ 1100 px'de detaydaki sayaçlar ve form alt alta. |

Gerçek tarayıcıda (Chromium) render edilip görüntüsü alınanlar: 1920×1080, 2560×1440, 1366×768, 768×1024, 390×844 ve 960×540 (= 1920×1080'de %200 zoom). Hepsinde yatay sayfa kaydırması yok (`scrollWidth == viewport`). 1366×768'de Live dikey kaydırma gerektirir (1324 px). Uzun araç adı testi katalog gelince yapılacak (şu an yalnız `Car #<ordinal>` var; `.car-name` ellipsis ile kesilir). Karşılaştırmalı görüntüler: `docs/screenshots/compare/` (`before-*` / `after-*`, aynı deterministik sentetik akış).

## Erişilebilirlik ve hareket

Görünür `:focus-visible` halkası, "Skip to content", `aria-current` sekmeler, `aria-pressed` geçiş düğmeleri (Focus view, Pause, Expand), etiketli filtre ve ölçek seçimi, `role="meter"` pedal/direksiyon/debriyaj/el freni, `role="group"` + adlı teker hücreleri. `role="status" aria-live="polite"` yalnız bağlantı durumu metninde (30 Hz değerler okunmaz). Kayıt süresi `aria-live="off"` (her saniye okunmaz); kayıt hatası ve kontrol hataları `role="alert"`; yakalama durumu, etiket kaydetme ve bütünlük sonucu `role="status"`. Sessions satırları düğmedir (`aria-current`), hız zarfı `role="img"` + açıklayıcı `aria-label`.

Trace canvas'ı klavyeyle odaklanabilir bir `role="slider"`: ←/→ 0.1 s, Shift ile 1 s, Home/End uçlar, Space/Enter duraklat/devam, Escape canlıya dön ve imleci kaldır. İmlecin değerleri hem görünür okuma satırında hem `aria-valuetext` içinde metin olarak bulunur ("−1.1 s: speed 116 km/h, throttle 100%, …"); bu metin yalnız kullanıcı imleci klavyeyle taşıdığında güncellenir, canlı akışla okunmaz.

Sayılara tween/geçiş animasyonu yok; `prefers-reduced-motion` tüm geçişleri kapatır. Tasarım geçişinde `frontend-design` skill'inin yönergeleri uygulandı (bkz. IMPLEMENTATION_STATUS.md).
