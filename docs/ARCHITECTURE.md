# Mimari

Tek süreç, tek komut: Python (UDP alıcı + FastAPI/uvicorn) derlenmiş React arayüzünü de servis eder. İnternet, Node, Redis, veritabanı gerekmez.

```
 FH6 ──UDP──▶ UdpReceiver (thread) ──bytes──▶ TelemetryHub.ingest()        (lock altında)
                                               ├─ decode()     ham byte → raw (88 alan + opak kuyruk)
                                               ├─ sıra/kaynak  duplicate · reorder · wrap · reset · foreign
                                               ├─ normalize()  raw → normalized + derived + quality
                                               ├─ latest_frame, 120 s history ring, sayaçlar, olaylar
                                               └─ on_frame() ──call_soon_threadsafe──▶ FrameSignal
 DemoGenerator (thread, yalnız demo modunda) ──▶ ingest(origin="demo")
                                                                                │
 Tarayıcı ◀──WS /api/v1/stream── yayın coroutine'i (istemci başına) ◀───────────┘
          ◀──HTTP /api/v1/*────
```

| Modül | Sorumluluk |
|---|---|
| `backend/telemetry/schema.py` | Alan sırası/tip/birim/aralık, offset türetimi, kabul edilen uzunluklar |
| `backend/telemetry/decode.py` | `decode` (uzunluk doğrulama + unpack), `normalize` (validasyon, birimler, enum'lar, türetilmiş metrikler, alan kalitesi) |
| `backend/telemetry/hub.py` | Thread-safe durum: sıralama, kaynak bağlama, bağlantı durum makinesi, sayaçlar, son frame, geçmiş, JSON önbelleği |
| `backend/telemetry/receiver.py` | UDP soketi; yalnız alır ve hub'a verir. Bind hatası senkron raporlanır, 0.25 s timeout ile temiz kapanış |
| `backend/telemetry/synth.py` | Demo üretici ve fixture paketleyici (sahte veri) |
| `backend/api/app.py` | REST + WebSocket, güvenlik başlıkları, statik UI |
| `backend/config.py`, `backend/main.py` | Ayar (varsayılan < JSON < CLI), loglama, başlatma/kapanış |
| `frontend/src` | React + TypeScript + Vite arayüzü |

Henüz yok (Faz 3+): session recorder, analysis, providers. Boş klasör/soyut sınıf olarak eklenmedi.

## Veri katmanları

`raw` (paketin söylediği, tam hassasiyet; NaN/Inf JSON'da `null`) → `normalized` (SI/fraksiyon/enum, geçersiz → `null` + neden) → `derived` (km/h, mph, kW, hp, rpm oranı, g) → görüntü (yuvarlama yalnız `frontend/src/format.ts`). Hiçbir katmanda `or 0` yoktur; `null` "bilinmiyor", `0` "ölçülmüş sıfır" demektir.

## Frame sözleşmesi (api_version 1)

```jsonc
{
  "type": "frame",
  "status": { "connection_state": "receiving", "packet_age_ms": 4, "traffic_warning": null,
              "observed_hz": 59.9, "mode": "live", "receiver_state": "listening", "receiver_error": null,
              "receiver_address": "127.0.0.1:5607", "session_id": "…", "source": "127.0.0.1:52311",
              "last_rejected_reason": null, "sequence": 1234 },
  "frame": {                      // hiç geçerli paket yoksa null
    "schema_version": 1, "api_version": 1,
    "session_id": "7ee450cf2cc3",
    "sequence": 1234,             // BU UYGULAMANIN sayacı; oyun sıra numarası göndermez
    "game_timestamp_ms": 98765,
    "received_at": 1794828149593, // sunucu duvar saati, ms (yalnız gösterim/ölçüm)
    "mode": "live", "source": "127.0.0.1:52311",
    "raw": { … },                 // yalnız raw aboneliği varsa, ≤ raw_rate_hz
    "normalized": { … }, "derived": { … },
    "quality": { "fields": { "Speed": { "status": "non_finite", "detail": "nan" } },
                 "invalid_field_count": 1, "flags": [], "tail_hex": "00", "packet_length": 324 }
  }
}
```

`GET /api/v1/telemetry/latest` aynı frame'i `connection_state` ve `packet_age_ms` alanları eklenmiş olarak döner. JSON `allow_nan=False` ile üretilir; NaN/Infinity çıkamaz (test edilir).

## API

| Endpoint | İşlev |
|---|---|
| `GET /api/v1/health` | süreç, receiver durumu/hatası, bağlantı durumu, son geçerli paket yaşı, mod, recorder yok bilgisi |
| `GET /api/v1/telemetry/latest` | son snapshot + tazelik |
| `GET /api/v1/telemetry/history?seconds=1..120` | ring buffer'dan hız/rpm/pedal/direksiyon dizileri |
| `GET /api/v1/capabilities` | şema, özellik bayrakları (recording/replay/analysis/car_catalog = false), doğrulama durumu |
| `GET /api/v1/diagnostics` | sayaçlar, uzunluk dağılımı, kuyruk byte'ı, olaylar, reddedilen örnekler, UI istemcileri |
| `POST /api/v1/demo {"enabled": bool}` | demo modu |
| `WS /api/v1/stream` | `hello` (sürümler) → `frame` mesajları; istemci `{"type":"subscribe","raw":true}` ve `{"type":"resync"}` gönderebilir |

Prompttaki `sessions`, `cars/{ordinal}` endpoint'leri Faz 3–4'e aittir ve henüz yoktur.

## Yayın modeli ve backpressure

- WebSocket seçildi (SSE değil): aynı bağlantı üzerinden raw aboneliği ve resync istemek gerekiyor; ileride replay kontrolü de buraya gelecek. SSE tek yönlü olduğu için bunlar ayrı HTTP çağrıları gerektirirdi. İkisi birlikte desteklenmez.
- Kuyruk yok: her istemci coroutine'i yalnız **son** frame'i gönderir (latest-wins). Yavaş istemci ara frame'leri kaybeder (`frames_skipped`), bellek büyümez. 2 s içinde gönderilemeyen istemci kapatılır.
- Gönderim olay tetiklidir: alıcı thread kabul ettiği her frame'de `call_soon_threadsafe` ile `FrameSignal`'ı uyandırır; istemci son gönderiminden ≥ 0.9 periyot geçmişse hemen gönderir. Sabit `sleep(1/30)` döngüsü Windows'ta ~15.6 ms zamanlayıcı çözünürlüğü yüzünden 30 Hz yerine ~21.6 Hz veriyordu (ölçüldü); olay tetiklemeyle 30.6 Hz ve p95 1.5 ms ölçüldü.
- Durum değişikliklerini taşımak için 2 Hz heartbeat (frame değişmese bile `status` gelir); istemci 3 s mesaj almazsa bağlantıyı kapatıp yeniden bağlanır (500 ms × 2ⁿ, en çok 10 s, %50 jitter).
- Tarayıcıda gelen mesajlar `requestAnimationFrame` başına en çok bir kez React'e işlenir; gizli sekmede render durur, görünür olunca `resync` istenir. Trace canvas yalnız `receiving` durumunda animasyon yapar.
- Ekran için frame atlamak kayıt kaybı değildir; kayıt (Faz 3) ayrı kuyruk ve sayaçla gelecektir.

## Güvenlik (yerel ürün ölçeğinde)

- Varsayılan dinleme `127.0.0.1`; LAN yalnız `--lan` ile.
- `TrustedHostMiddleware`: Host başlığı localhost/127.0.0.1/::1 (LAN'da ayrıca makine adı/IP'leri + `extra_allowed_hosts`) dışında 400 — DNS rebinding'e karşı.
- POST ve WebSocket için same-origin kontrolü (Origin varsa Host ile eşleşmeli). LAN modunda durum değiştiren istekler varsayılan olarak yalnız loopback'ten.
- HTML'de sıkı CSP (`default-src 'self'`, inline script yok), `nosniff`, `no-referrer`, API'de `no-store`. CORS açılmaz (aynı origin).
- Tüm metin React ile `textContent` olarak render edilir; `innerHTML`/`dangerouslySetInnerHTML` kullanılmaz.
- Tam path eşleşmesi; bilinmeyen yollar 404 (eski `startswith` eşleşmesi kaldırıldı).
- Loglar dönen dosyada (1 MB × 3); ham paket kaydı yok.

## Kapanış

uvicorn `Ctrl+C` → lifespan kapanışı → demo thread durur → UDP soketi kapanır, thread `join` edilir → süreç çıkar. Testlerde aynı port yeniden bağlanabiliyor (`test_shutdown_and_restart_releases_port`). Web portu doluysa başlamadan açık hatayla çıkılır (kod 3); UDP portu doluysa web arayüzü açılır ve hatayı gösterir (oyunsuz demo/diagnostics için).
