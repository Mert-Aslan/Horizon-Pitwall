# Kurulum, güncelleme ve geri dönüş (Windows)

Horizon Pitwall bilgisayarınızda yerel çalışır; internete yalnız kurulum sırasında (paketler) ihtiyaç duyabilir. Hiçbir veri dışarı gönderilmez.

## Gereken

- Windows 10/11, 64 bit
- Python 3.11 veya üstü (<https://www.python.org/downloads/>; kurulumda **Add python.exe to PATH** işaretli). 3.13 ile test edildi.
- Forza Horizon 6, *Settings › HUD and Gameplay*: **Data Out = On**, **IP = 127.0.0.1**, **Port = 5607**

## Kurulum

1. `HorizonPitwall-<sürüm>.zip` dosyasını istediğiniz bir klasöre açın (ör. `C:\Games\HorizonPitwall`). Yolda boşluk olabilir.
2. **`Install Pitwall.cmd`**'yi çift tıklayın. Klasörün içinde özel bir Python ortamı (`.venv`) oluşturur ve gerekli paketleri kurar:
   - zip içinde `wheels` klasörü varsa **internetsiz** kurar. Bu paketteki derlenmiş parçalar tek bir Python sürümü içindir (`wheels\PYTHON_VERSION.txt`, şu an 3.13); kurulum betiği `py -3.13` ile o sürümü seçer, yoksa ne kurmanız gerektiğini söyleyerek durur;
   - yoksa paketleri pypi.org'dan indirir (~10 MB).
   Klasör dışında hiçbir şey değişmez; sistem Python'una paket kurulmaz.
3. **`Start Pitwall.cmd`** ile başlatın. Tarayıcıda `http://127.0.0.1:8080/` açılır. Kapatmak için pencereyi kapatın veya `Ctrl+C`.

Kaynak koddan çalıştırmak (geliştirici): `python -m pip install -r requirements.txt` ve `python .\forza_horizon_6_telemetry.py`.

## Veri nerede?

Uygulama klasöründe değil, kullanıcı klasöründe:

```bash
python .\forza_horizon_6_telemetry.py --paths
```

| | Varsayılan konum |
|---|---|
| Veri klasörü | `%LOCALAPPDATA%\HorizonPitwall` |
| Log | `…\logs\pitwall.log` (1 MB × 3) |
| Kayıtlar | `…\recordings` (SQLite + sıkıştırılmış JSONL; 60 Hz'de ~27 MB/saat) |
| Tanılama yakalamaları | `…\captures` (en çok 10 dosya, kendiliğinden temizlenir) |
| Araç adları | `…\catalog.sqlite3` |

Aynı bilgi çalışırken `GET /api/v1/paths` ve Diagnostics'te de görünür. Başka bir yer için `--data-dir KLASÖR`.

**Yedek:** uygulamayı kapatın ve veri klasörünü kopyalayın. Kayıtlar otomatik silinmez.

## Güncelleme

1. Pitwall'ı kapatın.
2. Yeni zip'i **yeni bir klasöre** açın (eskisini silmeyin; geri dönüş için gerekir).
3. Yeni klasörde `Install Pitwall.cmd`, sonra `Start Pitwall.cmd`.

Veri klasörü değişmez; yeni sürüm aynı kayıtları ve adları kullanır. Diagnostics → *Server build* yeni sürümü göstermelidir. Açık kalan bir tarayıcı sekmesi eski arayüzü çalıştırıyorsa "newer version of this page" uyarısı çıkar; sayfayı yenileyin.

## Geri dönüş (eski sürüme)

1. Pitwall'ı kapatın, eski klasördeki `Start Pitwall.cmd`'yi çalıştırın.
2. Veri biçimleri sürümlüdür. Kayıt veritabanı (şema 1), chunk biçimi (`pitwall-jsonl-v1`) ve araç kataloğu (şema 1) 0.3.0'dan beri değişmedi; eski sürümler yeni sürümün eklediği tabloları (ör. `runs`) yok sayar.
3. İleride bir sürüm şemayı yükseltirse, eski sürüm o veriyi **değiştirmez**: kayıt ve/veya araç adları kapatılır, canlı görünüm çalışmaya devam eder ve log'a nedeni yazılır (`event=recorder_unavailable … created by a newer Pitwall`). Yeni sürüme dönünce her şey yerindedir.

Yerel geliştirme kopyasında ayrıca git geçmişi vardır (her sürüm bir commit).

## Kaldırma

Uygulama klasörünü silin. Kayıtlarınızı da silmek istiyorsanız veri klasörünü ayrıca silin (geri alınamaz).

## Sorun giderme

| Belirti | Çözüm |
|---|---|
| "Python 3.11 or newer was not found" | Python'u kurun (PATH işaretli), komut penceresini yeniden açın. |
| "Pitwall is not installed yet" | Önce `Install Pitwall.cmd`. |
| Kurulum indirirken hata | İnternet/proxy; ya da `wheels` klasörlü (offline) zip'i kullanın. |
| "web port 8080 is not available" | Başka bir Pitwall açık olabilir; kapatın veya `Start Pitwall.cmd --web-port 8081`. |
| "Waiting for data" | Oyunda Data Out ayarlarını ve portu kontrol edin; Diagnostics → *Received*. |
| Kayıt düğmesi yok, log'da "newer Pitwall" | Geri dönüş sonrası; yukarıdaki *Geri dönüş* 3. madde. |

## Sürüm paketi oluşturma (geliştirici)

```bash
cd frontend
npm run build
cd ..
python tools/build_release.py
```

`release/HorizonPitwall-<sürüm>/` ve `.zip` oluşur: yalnız `backend/`, derlenmiş arayüz, başlatıcılar, `requirements.txt`, README ve bu rehber (test, araç, kayıt, log yok). `MANIFEST.json` her dosyanın SHA-256'sını ve çalışan kopyada `/api/v1/build`'in göstereceği backend özetini içerir. `--wheels` offline kurulum için paketleri pypi.org'dan indirip ekler (internet gerekir; çalıştıran Python sürümü için, Windows 64 bit) ve `wheels\PYTHON_VERSION.txt` yazar.

**Doğrulama (17 Kasım 2026):** 0.7.0 için `--wheels` ile 14 paket indirildi (3.4 MB; zip 3.5 MiB). Zip, yolunda boşluk olan yeni bir klasöre açıldı ve `Install Pitwall.cmd` ağ erişimi bilerek kesilmişken (geçersiz proxy + geçersiz paket dizini) çalıştırıldı: 12.1 s'de kuruldu, ortam sistem paketlerini görmüyor (`include-system-site-packages = false`). `Start Pitwall.cmd` bu ortamı kullandı; uygulama izole portlarda başladı, `/api/v1/build` kimliği `MANIFEST.json` ile eşleşti, UDP verisi alındı, arayüz açıldı. Geliştirme kopyasında bu zip `release/HorizonPitwall-0.7.0-offline-py3.13.zip` olarak durur (git'e girmez).
