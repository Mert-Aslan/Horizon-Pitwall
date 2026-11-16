# Veri kaynakları

## Bugün kullanılan

| Kaynak | Durum |
|---|---|
| FH6 Data Out UDP | Tek canlı kaynak. Şema: [PROTOCOL.md](PROTOCOL.md). |
| Sentetik demo / fixture | Yerel üretilir, her zaman `mode: "demo"` ya da negatif `CarOrdinal` ile işaretli. |

Bu sürüm **hiçbir dış servise istek yapmaz**; internet olmadan tamamen çalışır. API anahtarı istenmez ve saklanmaz.

## Planlanan (Faz 4) — entegrasyon yapılmadı, yalnız doküman incelemesi

| Kaynak | Katkı | Sınır | Öncelik |
|---|---|---|---|
| Sürümlü yerel `CarOrdinal` kataloğu + kullanıcı düzeltmeleri (CSV/JSON içe aktarma) | araç adı/yıl/varyant | Ordinal eşlemesi bu oyun sürümü için doğrulanmalı; başka Forza oyunundan kör kopya yok | P1 |
| Resmi FH6 araç listesi (forza.net/fh6cars) | model adları/kapsam kontrolü | Web sayfası; ordinal lookup API'si olduğu kanıtlanmadı | P1 referans |
| NHTSA vPIC | marka/model/yıl doğrulama (`GetModelsForMakeYear`) | Oyun VIN göndermez; ABD odaklı; beygir/tork/fiyat kataloğu değildir | P2 |
| Wikimedia Commons (MediaWiki `prop=imageinfo&iiprop=url|extmetadata`) | araç görseli + lisans | Doğru nesil/varyant ve lisans her görsel için kontrol edilmeli; metadata HTML'i ham render edilmez | P2 |
| Steam Web API | opsiyonel oyuncu paneli | Fizik/garaj/tuning kaynağı değil; credentials olmadan yalnız mock + sözleşme testi | P3 |
| CarQuery | stok araç özellikleri adayı | Dokümantasyon erişimi/güncellik/lisans doğrulanmadı | Beklemede |

Kimlik zinciri: `(oyun, şema/katalog sürümü, CarOrdinal)` → doğrulanmış yerel model → opsiyonel harici ID. İsim benzerliği yalnız aday üretir. Eşleşme yoksa bugünkü gibi `Car #<ordinal> · unidentified`.

Her zenginleştirilmiş alan `source`, `source_id`, `source_url`, `fetched_at`, `license`, `match_status` (gerekirse `confidence`) taşıyacak. "Stock reference" canlı `Power`/`Torque`/`DrivetrainType`/`NumCylinders` değerlerinin yerine geçmez (modifiye ve motor değişimi).

Provider kuralları: backend adapter; timeout, sınırlı retry + jitter, rate limit, TTL + negatif cache, araç değişiminde tekil lookup (frame başına HTTP yok), anahtar istemci paketine girmez ve loglanmaz, provider düşerse canlı telemetri etkilenmez.

## Vaat edilmeyenler

Rakip koordinatları, garaj envanteri, tuning ayarları/takılı parçalar, açık artırma fiyatları, ghost'lar, kariyer verisi, oyun içi hava durumu için doğrulanmış erişim yok. World X/Z enlem/boylam değildir ve harita servisine gönderilmez. Bellek okuma/enjeksiyon veya anti-cheat atlatma bu ürünün yaklaşımı değildir.
