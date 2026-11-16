# Horizon Pitwall

**Forza Horizon 6 Telemetry & Driving Analysis** — ikinci monitör için yerel, çevrimdışı çalışan canlı telemetri kokpiti.

> Bağımsız bir hobi projesidir; Forza, Playground Games, Turn 10 veya Microsoft ile resmi bir bağı yoktur.

Sürüm 0.2.0 — Faz 0–2 (veri doğruluğu + Live kokpit). Kayıt/replay/analiz henüz yok; ayrıntı: [docs/IMPLEMENTATION_STATUS.md](docs/IMPLEMENTATION_STATUS.md).

## Kurulum

Gereken: Windows, Python 3.11+ (3.13.3 ile test edildi). Son kullanıcı için Node **gerekmez**; derlenmiş arayüz `frontend/dist` içinde gelir.

```powershell
python -m pip install -r requirements.txt
```

## Oyun ayarı

Forza Horizon 6 → **Settings › HUD and Gameplay**:

| Ayar | Değer |
|---|---|
| Data Out | On |
| Data Out IP Address | `127.0.0.1` (aynı PC) |
| Data Out IP Port | `5607` (uygulamanın `--udp-port` değeriyle aynı) |

5200–5300 portlarını kullanmayın; oyun kendi soketini bu aralıkta açar. Oyun yalnız sürüş sırasında veri gönderir; menü, pause, rewind, replay ve yarış sonrası akış durur (uygulama bunu "Data stale" olarak gösterir, bağlantı hatası saymaz).

## Başlatma

```powershell
python .\forza_horizon_6_telemetry.py
```

Arayüz: <http://127.0.0.1:8080/>. Kapatmak için terminalde `Ctrl+C` (UDP soketi ve web sunucusu temiz kapanır).

Sık kullanılan seçenekler (`--help` tümünü listeler):

| Seçenek | Açıklama |
|---|---|
| `--udp-port 5607` / `--web-port 8080` | Portlar |
| `--demo` | Açıkça etiketli sentetik veriyle başla |
| `--open` | Varsayılan tarayıcıda aç |
| `--lan` | Arayüzü yerel ağda yayınla (opt-in; aşağıya bakın) |
| `--config dosya.json` | Ayar dosyası (`pitwall.config.example.json` örnek; `./pitwall.config.json` varsa otomatik okunur) |

`python -m backend` aynı uygulamayı başlatır.

## Demo

Canlı sinyal yokken uygulama **asla** kendiliğinden sahte veri göstermez. Demo yalnız kullanıcı seçimiyle açılır: boş ekrandaki *Explore with demo data* düğmesi, Diagnostics'teki *Start demo* veya `--demo`. Demo açıkken üstte kalıcı "Demo" rozeti ve banner görünür, araç "Demo car · synthetic" olarak etiketlenir ve gerçek UDP paketleri yok sayılır (sayaçta görünür).

Oyunsuz test için ayrı bir porta sentetik paket göndermek:

```powershell
python .\forza_horizon_6_telemetry.py --udp-port 15607 --web-port 18080
python .\tools\send_synthetic.py --port 15607 --seconds 30
```

`send_synthetic.py` oyunun varsayılan portu 5607'ye `--allow-default-port` olmadan göndermez.

## Ekranlar

- **Live** — devir bandı, vites + hız, gaz/fren ve merkezi sıfırlı direksiyon (debriyaj/el freni kompakt ya da genişletilmiş), dört teker (ham lastik sıcaklığı, combined slip, süspansiyon), son 20 s grafik (zaman ekseni, imleç, duraklatma, klavyeyle inceleme), güç/tork/boost/yakıt ve zamanlama. Üst şeritteki **Focus view** yalnız ana ölçümleri ve girişleri bırakır; tercih tarayıcıda saklanır.
- **Diagnostics** — bağlantı durumu, paket sayaçları, uzunluk dağılımı, 324. byte değerleri, olaylar, reddedilen paket örnekleri, protokol doğrulama durumu ve ham alan inspector'ı (~4 Hz).

Durumlar: `starting`, `waiting`, `receiving`, `stale`, `error`, sunucuya ulaşılamıyor, geçersiz paket, ikinci gönderici, demo. Ekran görüntüleri: [docs/screenshots](docs/screenshots).

## LAN (telefon/tablet)

`--lan` ile web arayüzü `0.0.0.0` üzerinde dinler; kabul edilen `Host` başlıkları localhost + bu PC'nin adı/IP'leridir (`extra_allowed_hosts` ile genişletilebilir). LAN istemcileri **yalnız izler**; demo aç/kapa gibi durum değiştiren istekler yalnız bu PC'den kabul edilir (`lan_allow_control: true` ile açılabilir). Uygulama firewall ayarını değiştirmez ve internete port açmaz; Windows ilk seferde izin sorabilir.

## Test

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest
cd frontend; npm ci; npm test
```

Tarayıcı smoke testi (Python Playwright; bir kez `python -m playwright install chromium` gerekir). Kendi izole sunucusunu 18100/15620 portlarında başlatır, yeniden bağlanma için bir kez yeniden başlatır, yalnız kendi başlattığı süreç ağacını kapatır ve portların boşaldığını yazar:

```powershell
python tools/ui_smoke.py
```

Aynı veriyle önce/sonra ekran görüntüsü ve tarayıcı performans ölçümü: `tools/ui_capture.py` (kullanım dosyanın başında; `tools/isolated_server.py` içinde çalıştırılır).

Sentetik fixture'ları yeniden üretmek: `python tools/make_fixtures.py`. Yük/gecikme ölçümü: `tools/loadtest.py` (çalışan bir örneğe karşı; ayrıntı: [docs/IMPLEMENTATION_STATUS.md](docs/IMPLEMENTATION_STATUS.md)).

## Arayüzü geliştirmek

```powershell
python .\forza_horizon_6_telemetry.py          # backend :8080
cd frontend; npm ci; npm run dev               # http://localhost:5173, API proxy -> :8080
npm run build                                  # frontend/dist (backend bunu servis eder)
```

`frontend/dist` eksikse ana sayfa derleme komutunu gösteren bir mesaj döner; API yine çalışır.

## Sorun giderme

| Belirti | Olası neden / çözüm |
|---|---|
| "Waiting for data" | Data Out kapalı, IP/port uyuşmuyor ya da henüz sürüşte değilsiniz. Diagnostics'te *Received* sayacı artıyor mu bakın. |
| "Receiver error … Only one usage of each socket address" | Port başka programda (ikinci Pitwall, SimHub vb.). Onu kapatın veya `--udp-port` + oyundaki portu değiştirin. |
| "Packets … do not match the FH6 format" | Gönderen FH6 değil (ör. Forza Motorsport "sled/dash"), ya da başka bir uygulama aynı porta yazıyor. Diagnostics → *Lengths seen*. |
| "A second sender is transmitting" | Aynı porta iki kaynak gönderiyor; ilk kaynak susana kadar (3 s) ikincisi yok sayılır. |
| "Data stale" | Oyun menüde/pause/rewind/replay'de. Son değerler soluk ve yaşıyla gösterilir. |
| Web portu dolu | Başlangıçta açık hata verir; `--web-port 8081`. |

Loglar: `%LOCALAPPDATA%\HorizonPitwall\logs\pitwall.log` (1 MB × 3 dönen dosya).

## Belgeler

- [docs/PROTOCOL.md](docs/PROTOCOL.md) — paket şeması, kararlar, açık sorular, fixture kökeni
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — katmanlar, frame sözleşmesi, API, tradeoff'lar
- [docs/DESIGN_SYSTEM.md](docs/DESIGN_SYSTEM.md) — token'lar, bileşenler, durumlar, kontrast ölçümleri
- [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md) — dış kaynak stratejisi (henüz entegrasyon yok)
- [docs/IMPLEMENTATION_STATUS.md](docs/IMPLEMENTATION_STATUS.md) — durum, testler, ölçümler, sıradaki iş

Eski tek dosyalık prototip değiştirilmeden `legacy/forza_horizon_6_telemetry.original.py` altında durur (SHA-256 `f9e5cbf7…388a7`); `tools/legacy_baseline.py` onu izole portlarda çalıştırır.
