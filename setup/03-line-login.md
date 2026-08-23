# 03 — Install LINE and log in (headless, QR code)

Everything below runs inside the LXC; `ADB="adb -s 127.0.0.1:5555"`. Verified end-to-end on a
fresh CT on 2026-08-23 (screens and coordinates are from that run — 720×1280 display).

## 0. Seeing the screen: `tools/redroid-screen.py`

The `_64only` Redroid image has **no VNC server and no H.264 encoder**, so noVNC and scrcpy
cannot attach. `tools/redroid-screen.py` is a stdlib-only web viewer/controller: it serves
`adb exec-out screencap -p` as a self-refreshing image and forwards clicks (tap), drags
(swipe), keys and typed text through `adb shell input`.

```bash
python3 /home/line/line-mcp/tools/redroid-screen.py --port 6080 [--token s3cret]
# → open http://<lxc-ip>:6080/   (add ?token=s3cret if set)
```

Buttons: Back / Home / Recents / Enter / Backspace / Power, a text box, refresh interval,
"save png". Endpoints if you prefer curl: `GET /screen.png`, `GET /ui.xml`, `GET /size`,
`POST /tap?x=&y=`, `/swipe?x1=&y1=&x2=&y2=&ms=`, `/key?code=`, `/text?s=`. Only expose it on a
trusted LAN — whoever reaches it controls the Android screen. Ctrl-C it when done (or run it
under `nohup`/a transient `systemd-run` unit while you log in).

Fallback without the viewer: `$ADB exec-out screencap -p > /tmp/screen.png` and look at the
PNG however you like (scp, Telegram bot, …); read on-screen text with
`$ADB shell uiautomator dump /sdcard/ui.xml && $ADB exec-out cat /sdcard/ui.xml`.

## 1. Install the LINE APK

Source the APK yourself (Play Store / APKMirror `.apkm` = zip of splits). This repo does not
distribute LINE. CT103 runs LINE **26.7.1 — the arm64-v8a build**; Redroid `_64only` runs it
through its native bridge (`ro.dalvik.vm.native.bridge=libnb.so`, abilist `x86_64,arm64-v8a`).
The cheapest source for a second host is the existing one:

```bash
# on the existing host: pull the installed splits
for p in $($ADB shell pm path jp.naver.line.android | sed 's/^package://' | tr -d '\r'); do $ADB pull "$p" .; done
#   → base.apk split_config.arm64_v8a.apk split_config.xxhdpi.apk split_config.xxxhdpi.apk
# on the new host:
$ADB install-multiple -r base.apk split_config.arm64_v8a.apk split_config.xxhdpi.apk split_config.xxxhdpi.apk   # "Success" in ~3 s
$ADB shell pm list packages | grep jp.naver.line.android
```

## 2. Log in as a **sub device** with a QR code

Launch LINE (use `am start`, not `monkey` — monkey silently no-ops on Redroid):

```bash
$ADB shell am start -n jp.naver.line.android/jp.naver.line.android.activity.SplashActivity
```

Then, in the viewer (or with `$ADB shell input tap X Y`, coordinates for 720×1280):

| # | Screen | Action |
|---|---|---|
| 1 | **Welcome to LINE** — `Log in` / `Sign up` | tap **Log in** (360, 983) |
| 2 | **Use this as your main device?** — *Main device* is pre-selected ⚠️ | tap **Sub device** (360, 930) — *Main device would start an account transfer and log your phone out* — then **OK** (360, 1110) |
| 3 | **Log in with QR code** — QR + countdown + `Regenerate` + `Log in with email` | scan the QR with the phone that owns the account (LINE app → Home → Add friends (QR icon) → scan, or Settings → Account → "Log in with QR code") |
| 4 | phone: confirm the login on the phone; the sub device may show a **verification code** | if a code is shown on the Redroid screen, enter it on the phone (read it from the viewer or `uiautomator dump`) |
| 5 | LINE main UI appears on Redroid (chat list, possibly some permission prompts) | dismiss prompts with Back or `Don't allow`; nothing else is needed |

The QR expires quickly (the countdown under it) — click **Regenerate** in the viewer if you
missed it. Account type matters: CT103 runs as a sub device (`ANDROIDSECONDARY`); LINE allows a
limited number of sub devices per account, so logging in a second Redroid with the same account
can sign the first one out.

After login LINE creates its databases (all the read paths depend on this):

```bash
ls -la /var/lib/docker/volumes/redroid-data/_data/data/jp.naver.line.android/databases/
#   naver_line   contact   (plus -wal/-shm)
```

The account is persisted in the `redroid-data` volume; subsequent boots are headless.
Now run `setup/04-line-mcp-install.sh` (it also sets up the `line` user's read access) and
`setup/05-verify.sh`.

## 3. Keep LINE running

`line-watchdog.timer` (installed in step 4) relaunches LINE if it dies, and
`line-restart.timer` restarts the app nightly because LINE leaks ~3 GB of native heap over
two days of uptime and would otherwise be OOM-killed (which takes the DB reader down with it).
