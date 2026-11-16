# Mimari

Sürüm 0.4.0. Tek süreç, tek komut: Python (UDP alıcı + FastAPI/uvicorn + kayıt yazıcısı) derlenmiş React arayüzünü de servis eder. İnternet, Node, Redis veya ayrı bir veritabanı sunucusu gerekmez; kayıt metadata'sı Python'un yerleşik SQLite'ındadır.

```
 FH6 ──UDP──▶ UdpReceiver (thread) ──bytes──▶ TelemetryHub.ingest()          (lock altında, disk/SQL yok)
                                               ├─ decode()      ham byte → raw (88 alan + opak kuyruk)
                                               ├─ sıra/kaynak   duplicate · reorder · wrap · reset · foreign
                                               ├─ segment       kaynak/mod değişince yeni stream segment
                                               ├─ normalize()   raw → normalized + derived + quality
                                               ├─ CarIdentity   ordinal histerezisi (D-2)
                                               ├─ latest_frame, segment geçmişi (zaman + adet sınırlı), sayaçlar, olaylar
                                               ├─ sample_sinks ──put_nowait──▶ Recorder kuyruğu (sınırlı)
                                               └─ on_frame() ──call_soon_threadsafe──▶ FrameSignal
             DiagnosticCapture.tap()  ◀── ingest() sonucu (lock dışında, yalnız capture açıkken)
 DemoGenerator (thread, yalnız demo modunda) ──▶ ingest(origin="demo")

 Recorder writer (thread) ── tek sahip ──▶ SQLite metadata + chunk-NNNNN.jsonl.gz
 Tarayıcı ◀──WS /api/v1/stream── yayın coroutine'i (istemci başına, ack penceresi)
          ◀──HTTP /api/v1/*────
```

| Modül | Sorumluluk |
|---|---|
| `backend/telemetry/schema.py` | Alan sırası/tip/birim/aralık, offset türetimi, kabul edilen uzunluklar |
| `backend/telemetry/decode.py` | `decode` (uzunluk doğrulama + unpack), `normalize` (validasyon, birimler, enum'lar, türetilmiş metrikler, alan kalitesi) |
| `backend/telemetry/hub.py` | Thread-safe durum: sıralama, kaynak bağlama, segmentler, bağlantı durum makinesi, sayaçlar, son frame, geçmiş, JSON önbelleği, sink'ler |
| `backend/telemetry/caridentity.py` | Araç kimliği: unknown / candidate / confirmed / temporarily_unavailable / unavailable |
| `backend/telemetry/capture.py` | Sınırlı tanılama yakalaması, eşit zaman damgası sınıflandırması (identical / tail_only / non_finite / changed) |
| `backend/telemetry/receiver.py` | UDP soketi; yalnız alır ve hub'a verir |
| `backend/telemetry/synth.py` | Demo üretici ve fixture paketleyici (sahte veri) |
| `backend/recording/store.py`, `recorder.py` | Kayıt depolama ve durum makinesi, kurtarma — [STORAGE_FORMAT.md](STORAGE_FORMAT.md) |
| `backend/buildinfo.py` | Build kimliği (içerik özeti, UI manifesti, süreç kimliği) |
| `backend/api/app.py` | REST + WebSocket, akış kontrolü, güvenlik başlıkları, statik UI |
| `backend/config.py`, `backend/main.py` | Sıkı ayar doğrulama (varsayılan < JSON < CLI), UTC loglama, başlatma/kapanış |
| `frontend/src` | React + TypeScript + Vite arayüzü |

Henüz yok: Telemetry Lab/analiz (M4), araç kataloğu (M6). Boş klasör veya çalışmayan düğme olarak eklenmedi.

## Kimlik ve zaman

| Kavram | Kapsam | Değişir |
|---|---|---|
| `server_instance_id` | süreç | her başlatmada |
| `stream_segment_id` | bir kaynaktan, bir modda kesintisiz akış | ilk pakette, kaynak değişiminde (3 s sessizlik sonrası), mod (live/demo) değişiminde |
| `sequence` | segment içi 1, 2, 3 … (oyunun değil, bu uygulamanın sayacı) | segment başında 1'e döner |
| `recording_id` | kullanıcının başlattığı kayıt | Record/Stop |
| `session_id` | **eskimiş takma ad** = `stream_segment_id` (API v1 uyumu için korunur) | — |

`sequence` yalnız `(server_instance_id, stream_segment_id)` ile birlikte karşılaştırılır. Tarayıcı bu çifti değiştiğinde iz geçmişini, sıra hafızasını ve ham örneği sıfırlar (F-03); sunucu yeni segmentte geçmişi, hız tahminini, araç kimliğini ve sıra durumunu sıfırlar (F-02). Bir zaman damgası resetinde segment değişmez; olay olarak kaydedilir. Yayın coroutine'leri ayrı, hiç sıfırlanmayan `publish_seq` kullanır.

Her frame üç saat taşır:
- `game_timestamp_ms` — oyunun ham U32 değeri; eşit/wrap/reset politikası buna uygulanır.
- `segment_elapsed_ms` — segment başından beri süreç monotonic saati; aralık ölçümleri buna dayanır.
- `received_at` — UTC duvar saati (ms); yalnız insanlar için, aralık ölçümünde kullanılmaz.

Tarayıcının kendi alım zamanı yalnız canlı görüntüleme içindir (son 20 s grafiği). Kayıtlarda zaman tabanı kayıt başından beri monotonic süredir (`el`); iki cihazın monotonic saatleri birbirinden çıkarılmaz.

## Veri katmanları

Boş paketler (`IsRaceOn = 0`, diğer her şey 0; P-9) kabul edilir ama normalize/türetilmiş değerleri `null`, kalite bayrağı `empty_packet`; tarayıcı bu durumda son gerçek frame'i soluk gösterir ve durumu "No driving data" yapar.

`raw` (paketin söylediği, tam hassasiyet; NaN/Inf JSON'da `null`) → `normalized` (SI/fraksiyon/enum, geçersiz → `null` + neden) → `derived` (km/h, mph, kW, hp, rpm oranı, g) → görüntü (yuvarlama yalnız `frontend/src/format.ts`). Hiçbir katmanda `or 0` yoktur; `null` "bilinmiyor", `0` "ölçülmüş sıfır" demektir. Kayıtlar yalnız ham datagram'ı saklar; normalize değerler okuma sırasında decoder ile yeniden üretilir.

## Frame sözleşmesi (api_version 1, geriye uyumlu genişletildi)

```jsonc
{
  "type": "frame",
  "status": { "connection_state": "receiving", "packet_age_ms": 4, "traffic_warning": null,
              "observed_hz": 59.9, "mode": "live", "receiver_state": "listening", "receiver_error": null,
              "receiver_address": "127.0.0.1:5607", "server_instance_id": "…", "stream_segment_id": "…",
              "session_id": "…", "source": "127.0.0.1:5200", "last_rejected_reason": null, "sequence": 1234,
              "recorder": { "state": "recording", "recording_id": "…", "elapsed_s": 12.5, "samples": 750,
                            "dropped": 0, "queue": 0, "queue_high_watermark": 3, "mode": "live", "error": null } },
  "frame": {                      // bu modda hiç geçerli paket yoksa null
    "schema_version": 1, "api_version": 1,
    "server_instance_id": "…", "stream_segment_id": "7ee450cf2cc3", "session_id": "7ee450cf2cc3",
    "sequence": 1234,
    "game_timestamp_ms": 98765, "segment_elapsed_ms": 20563.2, "received_at": 1794828149593,
    "mode": "live", "source": "127.0.0.1:5200",
    "car_identity": { "status": "confirmed", "confirmed_ordinal": 4156, "candidate_ordinal": null,
                      "raw_ordinal": 4156, "live_verified": true },
    "raw": { … },                 // yalnız raw aboneliği varsa, ≤ raw_rate_hz
    "normalized": { … }, "derived": { … },
    "quality": { "fields": { … }, "invalid_field_count": 0, "flags": [], "tail_hex": "00", "packet_length": 324 }
  }
}
```

`hello` mesajı ayrıca `server_instance_id`, `build_id`, `ui_build_id` ve `flow` ({`window`, `ack_every`}) taşır. Tarayıcı mesaj şeklini çalışma anında sınırlı biçimde doğrular (`parseMessage`); art arda 5 okunamayan mesaj görünür bir uyarıya dönüşür, akış çökmez.

## API

| Endpoint | İşlev |
|---|---|
| `GET /api/v1/health` | receiver, bağlantı durumu, mod, recorder durumu, build özeti |
| `GET /api/v1/build` | app/api/schema sürümü, `build_id` (= sürüm + backend içerik özeti), UI manifesti, `server_instance_id`, başlangıç zamanı; commit iddia edilmez |
| `GET /api/v1/telemetry/latest` | son snapshot + tazelik |
| `GET /api/v1/telemetry/history?seconds=1..120` | **geçerli segmentin** hız/rpm/pedal/direksiyon dizileri |
| `GET /api/v1/capabilities` | şema, özellik bayrakları (`recording`, `diagnostic_capture` true; replay/analysis/car_catalog false), doğrulama durumu, eşit zaman damgası politikası (varsayılan `accept_changed`) |
| `GET /api/v1/diagnostics` | sayaçlar, eşit zaman damgası sınıfları, sınırlar, araç kimliği, olaylar, akış/recorder metrikleri, build |
| `POST /api/v1/demo {"enabled": bool}` | demo modu |
| `GET /api/v1/recordings` | kayıt listesi + recorder durumu + başlangıç kurtarma raporu |
| `POST /api/v1/recordings/start {"title"?}` / `POST …/stop` | 201 / 200; zaten aynı durumdaysa **409** |
| `GET /api/v1/recordings/{id}` | metadata, segmentler, chunk manifesti, olaylar (≤ 500) |
| `PATCH /api/v1/recordings/{id}` | başlık, araç etiketi, setup etiketi, not |
| `GET /api/v1/recordings/{id}/verify` | checksum + satır sayısı + sürekli örnek indeksi kontrolü |
| `GET /api/v1/recordings/{id}/overview?points=` | tüm kayıt için hız min/max zarfı (boşluklar `null`) |
| `GET /api/v1/recordings/{id}/frames?from=&to=&max=` | replay penceresi: decode edilmiş frame'ler (`i`, `el`, canlıyla aynı `normalized/derived/quality` + `recording_id`, `sample_index`, `recording_elapsed_s`); en çok 60 s / 8000 satır, `truncated` bayrağı |
| `GET/POST /api/v1/diagnostics/capture`, `POST …/capture/stop` | tanılama yakalaması (5–120 s, boyut sınırlı) |
| `WS /api/v1/stream` | `hello` → `frame`; istemci `subscribe`, `resync`, `flow`, `ack` gönderebilir |

Durum değiştiren tüm istekler same-origin kontrolünden ve LAN modunda loopback kısıtından geçer.

## Yayın modeli, akış kontrolü ve ölçüler

- WebSocket (SSE değil): raw aboneliği, resync ve onay mesajları aynı bağlantıda.
- Kuyruk yok, latest-wins: her istemci coroutine'i yalnız **son** frame'i gönderir. UI hız sınırı yüzünden gönderilmeyen frame'ler `ui_rate_limited_frames` olarak sayılır; bu normal örneklemedir, paket kaybı veya yavaş istemci değildir.
- **Akış kontrolü (0.3.0):** işletim sisteminin TCP tamponları MB'larca veri tutabildiği için yavaş okuyan bir istemci 0.2.0'da 30–50 s eski veri görüyordu (ölçüldü). Artık istemci `hello`'daki `flow` teklifini kabul ederse her 2 mesajda bir `ack` gönderir; en çok 6 mesaj onaysız kalabilir. Pencere doluyken frame'ler bekletilir (`flow_withheld_frames`). Ölçülen: 5 mesaj/s okuyan istemcide gecikme p50 1.3 s (önce 29.6 s); normal istemciler etkilenmedi. Onay göndermeyen eski istemciler eskisi gibi çalışır.
- Gönderim olay tetiklidir (`FrameSignal`); 2 Hz heartbeat. 2 s içinde gönderilemeyen istemci kapatılır (`ws_send_timeouts`).
- Tarayıcıda: gelen her mesaj **anında** taşıma canlılığını (`lastRxAt`) günceller ve onaylanır; React'e işleme `requestAnimationFrame` başına en çok bir kez. Watchdog yalnız `lastRxAt`'e bakar, bu yüzden rAF'in durduğu arka plan sekmesi yanlışlıkla yeniden bağlanmaz (F-01). `start/stop/dispose` idempotent; zamanlayıcılar, görünürlük dinleyicisi, bekleyen yeniden bağlanma ve eski soket callback'leri temizlenir.
- Ayrı sayaç havuzları: `invalid_datagrams` / `foreign_datagrams` (hub), `ui_rate_limited_frames` / `flow_withheld_frames` / `slow_sends` (istemci başına), `ws_send_timeouts` / `ws_closed` / `ws_errors` (akış), `dropped` / `queue_high_watermark` / `flush_ms_max` (recorder). Ekran örneklemesi ağ kaybı olarak sunulmaz.

## Replay (M3)

Replay tamamen istemcidedir (`frontend/src/replay.ts` → `ReplayPlayer`). Sunucu yalnız kayıt dosyalarını pencere pencere okur ve decode eder; hub'ın modu, canlı akış, WebSocket ve süren bir kayıt etkilenmez. Birden çok tarayıcı aynı anda farklı kayıtları oynatabilir.

- Saat: oynatma başı = kayıt saniyesi (`el`). `position = basePos + (now − baseNow) × rate`; duraklatma/hız değişiminde taban yeniden kurulur. Oyun `TimestampMS`'i yalnız gösterilir.
- Tampon: oynatma başının 22 s gerisi + 12 s ilerisi; ileriki pencere 5 s kala istenir, geride kalanlar atılır. Grafik için ayrı bir `Ring` (t = `el`).
- Sarma: `generation` sayacı + `AbortController`; geç gelen eski cevap yok sayılır. Art arda sarmalar 150 ms durulunca tek pencere ister.
- Boşluk (oynatma başına 250 ms içinde örnek yok) ve boş paket (P-9) durumları görünür; son gerçek değerler soluk gösterilir, zaman sıkıştırılmaz.
- `Traces` bileşeni bir `TraceSource` alır (`history` + `now()` + `subscribe`): canlıda WebSocket store'u ve tarayıcı alım saati, replay'de oynatıcı ve kayıt saati. Çizim, imleç ve okuma satırı ortaktır; replay'de "Pause/Back to live" yerine replay kontrolleri vardır.
- Gzip (Starlette `GZipMiddleware`, ≥ 4 KiB) replay pencerelerini ~7× küçültür; WebSocket etkilenmez.

## Sınırlar

| Yapı | Sınır |
|---|---|
| Segment geçmişi | 120 s **ve** en çok 30 000 örnek |
| Yok sayılan göndericiler | en çok 16, 10 dk TTL; fazlası `other_sources_overflow` |
| Olaylar / reddedilen örnekler | 50 / 5 |
| Uzunluk ve kuyruk byte histogramları | 64 anahtar üstünde ilk 32'ye kırpılır |
| Recorder kuyruğu | `recorder_queue_size` (varsayılan 20 000); dolunca `dropped` |
| Tanılama yakalaması | 5–120 s, `diagnostic_capture_max_mb` (16 MB); en çok 10 dosya |
| Replay penceresi (sunucu) / tampon (tarayıcı) | 60 s ve 8000 satır / ~34 s |
| Log | 1 MB × 3 dönen dosya |

## Güvenlik (yerel ürün ölçeğinde)

- Varsayılan dinleme `127.0.0.1`; LAN yalnız `--lan` ile (yalnız web arayüzü; UDP alımı `udp_host` ile ayrı ayarlanır).
- `TrustedHostMiddleware` (DNS rebinding'e karşı), POST/WS same-origin kontrolü; LAN'da durum değiştiren istekler yalnız loopback'ten (`lan_allow_control` ile açılır).
- Sıkı CSP, `nosniff`, `no-referrer`, API'de `no-store`; CORS yok; `innerHTML` yok.
- Yalnız yerel dosyalar; hiçbir şey dış servise gönderilmez. Tanılama yakalamasında gönderici adresleri isteğe bağlı maskelenir.

## Loglama

Biçim: `2026-11-16T18:47:23.120Z INFO pitwall.hub: event=segment_start … segment=… mode=live`. Zaman UTC, bileşen logger adıdır; olaylar `event=<tür>` anahtarıyla. Her frame loglanmaz; hub olayları tür başına 5 s'de en çok bir satır (bastırılanlar sayılıp bir sonraki satırda yazılır). Başlangıç satırı build ve instance kimliğini içerir.

Windows'ta tarayıcı kapanan bir bağlantıyı sıfırladığında asyncio'nun `_call_connection_lost` içinde `WinError 10054` traceback'i loglanabiliyor (16 Kasım 2026'da kullanıcının logunda bir kez görüldü). Yalnız bu tam durum tanınır: tek satır `event=client_reset_on_close` INFO kaydı + `asyncio_close_resets` sayacı. Diğer tüm hatalar varsayılan işleyiciye gider. Yeniden üretme denemeleri: `tools/repro_close_reset.py` ve 4 farklı RST deseni × 5 — hem 0.2.0 hem 0.3.0 backend'de traceback üretilemedi; tetikleyici hâlâ açık.

## Kapanış

uvicorn `Ctrl+C` → lifespan kapanışı → recorder durdurulur (5 s içinde kuyruk boşaltılır, yazılamayan örnekler `lost_on_stop` olarak kaydedilir) → demo thread durur → UDP soketi kapanır → süreç çıkar. Web portu doluysa başlamadan açık hatayla çıkılır (kod 3); geçersiz ayar kod 2; UDP portu doluysa web arayüzü açılır ve hatayı gösterir.
