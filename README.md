# Horizon Pitwall

**Forza Horizon 6 Telemetry & Driving Analysis** — ikinci monitör için yerel, çevrimdışı çalışan canlı telemetri kokpiti.

> Bağımsız bir hobi projesidir; Forza, Playground Games, Turn 10 veya Microsoft ile resmi bir bağı yoktur.

Sürüm 0.4.0: veri doğruluğu, Live kokpit, **manuel sürüş kaydı** ve **replay** (Sessions); gerçek FH6 oturumuyla doğrulandı. Analiz (Telemetry Lab) henüz yok. Ayrıntı: [docs/IMPLEMENTATION_STATUS.md](docs/IMPLEMENTATION_STATUS.md).

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
| `--data-dir KLASÖR` | Log, kayıt ve tanılama yakalamalarının klasörü (varsayılan `%LOCALAPPDATA%\HorizonPitwall`) |
| `--config dosya.json` | Ayar dosyası (`pitwall.config.example.json` örnek; `./pitwall.config.json` varsa otomatik okunur) |

Ayar dosyası ve komut satırı aynı sıkı doğrulamadan geçer: `"lan": "false"` gibi tırnaklı değerler, `true` port, `NaN` veya bilinmeyen anahtarlar reddedilir; hata mesajı hangi alanın neden geçersiz olduğunu (JSON'da satır/sütun) söyler ve uygulama kod 2 ile çıkar.

`udp_host` uygulamanın **dinlediği** adrestir (`127.0.0.1` = yalnız bu PC, `0.0.0.0` = ağdaki bir konsol/PC de gönderebilir). Oyunda yazılan Data Out IP ise **hedeftir**: Pitwall'ın çalıştığı PC'nin adresi (aynı PC'de `127.0.0.1`); `0.0.0.0` hedef olarak yazılmaz. `--lan` yalnız web arayüzünü ağa açar, UDP alımını değiştirmez.

`python -m backend` aynı uygulamayı başlatır.

## Demo

Canlı sinyal yokken uygulama **asla** kendiliğinden sahte veri göstermez. Demo yalnız kullanıcı seçimiyle açılır: boş ekrandaki *Explore with demo data* düğmesi, Diagnostics'teki *Start demo* veya `--demo`. Demo açıkken üstte kalıcı "Demo" rozeti ve banner görünür, araç "Demo car · synthetic" olarak etiketlenir ve gerçek UDP paketleri yok sayılır (sayaçta görünür).

Oyunsuz test için ayrı bir porta sentetik paket göndermek:

```powershell
python .\forza_horizon_6_telemetry.py --udp-port 15607 --web-port 18080
python .\tools\send_synthetic.py --port 15607 --seconds 30
```

`send_synthetic.py` oyunun varsayılan portu 5607'ye `--allow-default-port` olmadan göndermez.

## Kayıt

Üst şeritteki **Record** düğmesi kabul edilen her paketi bu PC'ye kaydeder; **Stop** ile biter. Kayıt sunucuda çalışır: sayfa kapansa da sürer, düğme sunucunun gerçek durumunu gösterir. Demo kaydı "demo" olarak etiketlenir; demo ve canlı veri aynı kayda karışmaz. Kayıtlar **Sessions** sekmesindedir: süre, örnek sayısı, düşen örnek, kapsama, tepe/ortalama hız, onaylı araç kimlikleri, tüm kaydın hız özeti, olaylar; başlık, araç, setup/tune ve not etiketleri eklenebilir (oyun tuning ayarlarını göndermez, bunlar sizin yazdıklarınızdır); **Check files** dosyaları checksum ile doğrular.

**Replay:** Sessions'ta bir kaydı seçip **Replay**'e basın. Kayıt aynı kokpit panellerinde oynatılır: Play/Pause, 0.5× / 1× / 2×, zaman çizelgesine tıklayarak/sürükleyerek veya klavyeyle (←/→ 5 s, Shift ile 1 s, Home/End, Space) sarma, olaya atlama. Replay canlı akışı durdurmaz; arka planda veri alımı ve süren bir kayıt devam eder. Kayıtta veri olmayan anlar ve oyunun boş paket gönderdiği anlar açıkça belirtilir.

Dosyalar `<data dir>\recordings` altında (SQLite + sıkıştırılmış JSONL; 60 Hz'de saatte ≈ 27 MB). Uygulama çökerse bir sonraki başlangıçta yarım kalan kayıt kurtarılır ve "recovered after a crash" olarak işaretlenir. Kayıtlar otomatik silinmez. Biçim: [docs/STORAGE_FORMAT.md](docs/STORAGE_FORMAT.md).

## Ekranlar

- **Live** — devir bandı, vites + hız, gaz/fren ve merkezi sıfırlı direksiyon (debriyaj/el freni kompakt ya da genişletilmiş), dört teker (ham lastik sıcaklığı, combined slip, süspansiyon), son 20 s grafik (zaman ekseni, imleç, duraklatma, klavyeyle inceleme), güç/tork/boost/yakıt ve zamanlama. Üst şeritteki **Focus view** yalnız ana ölçümleri ve girişleri bırakır; tercih tarayıcıda saklanır.
- **Sessions** — kayıtlar (yukarıda).
- **Diagnostics** — bağlantı durumu ve build kimliği, paket sayaçları (aynı zaman damgalı paketlerin sınıfları dahil), uzunluk dağılımı, 324. byte değerleri, olaylar, reddedilen paket örnekleri, akış/kayıt sayaçları, protokol doğrulama durumu, **tanılama yakalaması** (30/60 s, her datagram yerel dosyaya; gönderici adresleri isteğe bağlı gizlenir; hiçbir şey yüklenmez) ve ham alan inspector'ı (~4 Hz).

Durumlar: `starting`, `waiting`, `receiving`, `stale`, `error`, sunucuya ulaşılamıyor, geçersiz paket, ikinci gönderici, demo. Ekran görüntüleri: [docs/screenshots](docs/screenshots).

## LAN (telefon/tablet)

`--lan` ile web arayüzü `0.0.0.0` üzerinde dinler; kabul edilen `Host` başlıkları localhost + bu PC'nin adı/IP'leridir (`extra_allowed_hosts` ile genişletilebilir). LAN istemcileri **yalnız izler**; demo aç/kapa gibi durum değiştiren istekler yalnız bu PC'den kabul edilir (`lan_allow_control: true` ile açılabilir). Uygulama firewall ayarını değiştirmez ve internete port açmaz; Windows ilk seferde izin sorabilir.

## Test

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest
cd frontend; npm ci; npm test
```

Tarayıcı smoke testi (Python Playwright; bir kez `python -m playwright install chromium` gerekir). Kendi izole sunucusunu 18100/15620 portlarında ve `%TEMP%\pitwall-isolated-18100` veri klasörüyle başlatır, yeniden bağlanma için bir kez yeniden başlatır, yalnız kendi başlattığı süreç ağacını kapatır ve portların boşaldığını yazar:

```powershell
python tools/ui_smoke.py
```

Aynı veriyle önce/sonra ekran görüntüsü ve tarayıcı performans ölçümü: `tools/ui_capture.py` (kullanım dosyanın başında; `tools/isolated_server.py` içinde çalıştırılır).

Sentetik fixture'ları yeniden üretmek: `python tools/make_fixtures.py`. Yük/gecikme ölçümü: `tools/loadtest.py` (izole bir örneğe karşı; yavaş/yeniden bağlanan istemci, kayıt, ısınma süresi, `--json` ile saniyelik CPU/RSS; 5607/8080'i reddeder). Windows bağlantı sıfırlama denemesi: `python tools/repro_close_reset.py` (kendi izole sunucusunu açar ve kapatır).

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
| "No driving data" | Oyun paket gönderiyor ama içleri boş (IsRaceOn = 0, tüm değerler 0) — sürüş dışındaki bazı anlarda olur. Son gerçek değerler soluk gösterilir; sürüşe dönünce kendiliğinden düzelir. |
| "Recording stopped: …" | Kayıt diske yazılamadı (ör. disk dolu). Canlı görünüm çalışmaya devam eder; o ana kadar kaydedilen kısım Sessions'ta. |
| "The Pitwall server now serves a newer version of this page" | Arayüz yeniden derlendi; sayfayı yenileyin. |
| Web portu dolu | Başlangıçta açık hata verir; `--web-port 8081`. |

Loglar: `%LOCALAPPDATA%\HorizonPitwall\logs\pitwall.log` (1 MB × 3 dönen dosya; zaman UTC, olaylar `event=…` biçiminde, başlangıç satırında build ve instance kimliği).

## Belgeler

- [docs/PROTOCOL.md](docs/PROTOCOL.md) — paket şeması, kararlar, açık sorular, fixture kökeni
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — katmanlar, kimlik ve zaman modeli, frame sözleşmesi, API, akış kontrolü, sınırlar
- [docs/STORAGE_FORMAT.md](docs/STORAGE_FORMAT.md) — kayıt dosyaları, tablolar, tutarlılık ve kurtarma
- [docs/DESIGN_SYSTEM.md](docs/DESIGN_SYSTEM.md) — token'lar, bileşenler, durumlar, kontrast ölçümleri
- [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md) — dış kaynak stratejisi (henüz entegrasyon yok)
- [docs/IMPLEMENTATION_STATUS.md](docs/IMPLEMENTATION_STATUS.md) — durum, testler, ölçümler, sıradaki iş

Eski tek dosyalık prototip değiştirilmeden `legacy/forza_horizon_6_telemetry.original.py` altında durur (SHA-256 `f9e5cbf7…388a7`); `tools/legacy_baseline.py` onu izole portlarda çalıştırır.
