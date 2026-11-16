# Kayıt depolama biçimi

Sürüm: veritabanı şeması 1, chunk biçimi `pitwall-jsonl-v1`, decoder kimliği `fh6-dataout/v1`. Kod: [`backend/recording/store.py`](../backend/recording/store.py), [`recorder.py`](../backend/recording/recorder.py).

## Yerleşim

```
<data_dir>/recordings/
  pitwall.sqlite3                          metadata (WAL)
  20261116T184723Z-71f8f2/                 bir kayıt (UTC başlangıç + rastgele ek)
    chunk-00000.jsonl.gz                   tamamlanmış chunk (~30 s veya 1800 örnek)
    chunk-00001.jsonl.gz.partial           yazılmakta olan chunk; veritabanı buna hiç başvurmaz
<data_dir>/captures/                       tanılama yakalamaları (ayrı kota, en çok 10 dosya)
<data_dir>/logs/pitwall.log
```

`<data_dir>` varsayılanı `%LOCALAPPDATA%\HorizonPitwall`; `--data-dir` ile değişir. Kullanıcı kayıtları hiçbir zaman otomatik silinmez.

## Chunk satırı

Kabul edilen her datagram için bir JSON satırı:

| Alan | Anlam |
|---|---|
| `i` | kayıt içi örnek indeksi (0'dan, boşluksuz) |
| `seg` | kayıt içi segment indeksi (`segments` tablosu) |
| `sq` | stream segment içindeki `sequence` |
| `ts` | oyunun ham `TimestampMS` değeri |
| `el` | kayıt başından beri saniye (süreç monotonic saati) — kayıt/replay'in zaman tabanı |
| `w` | UTC duvar saati, ms (yalnız insanlar için) |
| `d` | orijinal 324 byte'lık datagram, base64 |

Yalnız ham datagram saklanır: kayıpsızdır ve ileride düzeltilmiş bir decoder eski kaydı yeniden çözebilir. Normalize değerler okuma sırasında yeniden üretilir; ayrıca saklanmaz (iki kopya tutmanın maliyeti ölçülmedi, gerekçesi yok). Ölçülen boyut: örnek başına ≈ 120–130 byte gzip (5400 örnek = 0.69 MB, 17 999 örnek = 2.18 MB), yani 60 Hz'de saatte ≈ 27 MB. Eşit zaman damgalı örnekler `i` ile ayrılır.

## Tablolar

- `recordings` — `status` (`recording` / `completed` / `failed` / `recovered` / `incomplete`), `mode` (live/demo), kullanıcı etiketleri (`title`, `car_label`, `setup_label`, `notes`), başlangıç/bitiş (UTC ISO), `duration_s`, `samples`, `dropped` (kuyruk doluydu), `lost_on_stop`, `segments`, `app_version`, `schema_version`, `decoder`, `chunk_format`, `stop_reason`, `error`, `summary_json`.
- `segments` — kayıttaki her stream segmentinin ilk örneği, kaynağı ve başlangıç süresi.
- `chunks` — dosya adı, ilk/son örnek, örnek sayısı, `t_start_s`/`t_end_s` (kaba zaman indeksi), byte, SHA-256, `status` (`ok` / `recovered` / `missing`).
- `events` — kayıt sırasında hub olayları (segment, kaynak değişimi, araç kimliği, zaman damgası reseti, resume…) örnek indeksi ve süreyle.
- `annotations` — M3/M4'teki işaretler/notlar için ayrıldı; henüz API'si yok.

Zaman aralığı sorgusu (`Store.iter_lines(t_from, t_to)`) yalnız aralıkla kesişen chunk'ları açar; iki saatlik bir kaydın küçük bir aralığı tüm dosya açılmadan okunur.

## Yazma sırası ve tutarlılık

1. Örnek `chunk-N.jsonl.gz.partial` dosyasına yazılır; ~1 s'de bir gzip sync-flush (çökme sonrası kurtarılabilir).
2. Chunk dolunca: gzip kapanır → `fsync` → atomik `rename` → **sonra** chunk satırı + kayıt sayaçları tek SQLite transaction'ında.
3. Stop: son chunk kapanır, `completed` + özet yazılır.

Dosya sistemi ile veritabanı tek transaction değildir. Başlangıçta `Recorder.reconcile()` her çökme noktasını ele alır:

| Durum | Davranış |
|---|---|
| `.partial` var, satırı yok | eksiksiz satırlar yeni bir chunk'a kurtarılır (`recovered`), orijinal `.partial.orig` olarak saklanır |
| Son dosya var, satırı yok (rename ile commit arası) | okunup doğrulanır, kaydedilir |
| Satır var, dosya yok | chunk `missing` |
| Kayıt hâlâ `recording` | veri varsa `recovered`, yoksa `incomplete`; `stop_reason = crash_recovered` |

Testler: `tests/test_recorder.py` (çift başlat/durdur, eşzamanlı başlatma, kaynak değişimi, demo/live ayrımı, kuyruk taşması, disk dolu, istemcisiz kayıt, çökme kurtarma, eksik dosya, checksum, yeniden başlatma sonrası metadata, zaman aralığı sorgusu).

## Sahiplik ve eşzamanlılık

Örnek ve chunk yazımları yalnız recorder'ın yazıcı thread'inden, kendi SQLite bağlantısıyla yapılır (`check_same_thread=False` kullanılmaz). HTTP tarafı kısa ömürlü salt okunur bağlantılar açar; kullanıcı etiketleri kısa bir yazma transaction'ı ile güncellenir (WAL + busy timeout bunları sıralar). Transaction'lar açıkça `with conn:` ile yönetilir; Python 3.12+'daki `autocommit` özniteliğine dayanılmaz, davranış 3.11 ve 3.13'te aynıdır.

## Ölçümler (16 Kasım 2026, bu PC, sentetik akış)

| Senaryo | Sonuç |
|---|---|
| 60 Hz, 90 s, kayıt açık + 4 istemci | 5400/5400 örnek, 0 düşen, kuyruk tepe 2, en yavaş chunk flush 10.8 ms, 0.69 MB |
| 120 Hz, 150 s, kayıt açık + 2 istemci | 17 999 örnek, 0 düşen, kuyruk tepe 2, en yavaş flush 11.1 ms, 2.18 MB |

| **Gerçek oyun**, 16 Kasım 2026, 484 s | 25 703 örnek, 0 düşen, kuyruk tepe 1, en yavaş flush 13.6 ms, kapsama %100, `verify` geçti |

0.3.1'den itibaren özet `empty_samples` (veri taşımayan boş paket sayısı) içerir; boş paketler hız istatistiğine girmez ama kayıtta saklanır (kayıpsız).
