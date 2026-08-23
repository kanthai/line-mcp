# 03 — Install LINE and log in (headless)

The `_64only` Redroid image ships no H.264/VP8 encoder and no VNC server, so **scrcpy and
VNC do not work**. Everything below is done with ADB screenshots + `uiautomator`. All
commands run inside the LXC; `ADB="adb -s 127.0.0.1:5555"`.

## 1. Install the LINE APK

Source the APK yourself (Play Store / APKMirror `.apkm`). This repo does not distribute LINE.
CT103 runs LINE **26.7.1** (x86_64 split APK set).

```bash
# split set (.apkm is a zip — unzip it first)
$ADB install-multiple base.apk split_config.x86_64.apk split_config.xxhdpi.apk split_config.en.apk
# or a single universal APK
$ADB install LINE.apk
$ADB shell pm list packages | grep jp.naver.line.android
```

## 2. Log in with a QR code (sub-device / "Log in with another device")

```bash
$ADB shell monkey -p jp.naver.line.android -c android.intent.category.LAUNCHER 1
sleep 5
$ADB exec-out screencap -p > /tmp/screen.png          # look at it (scp it out, or send it to yourself)
$ADB shell uiautomator dump /sdcard/ui.xml && $ADB shell cat /sdcard/ui.xml | tr '>' '>\n' | grep -o 'text="[^"]*"\|bounds="[^"]*"' | paste - - | head -40
```

Walk the UI with `$ADB shell input tap X Y` using the `bounds` from the dump until you reach
**Log in → QR code login**. Screenshot the QR, scan it with the phone that owns the LINE
account, then read the verification code LINE shows on the phone and type it on the Redroid
side with `$ADB shell input text 123456` if prompted. On CT103 the screenshot was sent to a
Telegram bot to scan it from the phone; any channel that shows you the PNG works.

After login LINE creates its databases:

```bash
ls -la /var/lib/docker/volumes/redroid-data/_data/data/jp.naver.line.android/databases/
#   naver_line   contact   (plus -wal/-shm)
```

The account is persisted in the `redroid-data` volume; subsequent boots are headless.

## 3. Keep LINE running

`line-watchdog.timer` (installed in the next step) relaunches LINE if it dies, and
`line-restart.timer` restarts the app nightly because LINE leaks ~3 GB of native heap over
two days of uptime and would otherwise be OOM-killed (which takes the DB reader down with it).
