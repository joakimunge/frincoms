# Frincoms

A small shared voice room for Windows, macOS, and Linux. Open the app and click **Connect to Frincoms**. The first person waits; the second starts the call, and additional people can join. The relay mixes everyone's audio so each person hears the others. The window shows your connection status, current room count, and live microphone input level. Mute, deafen, and disconnect are available too.

## Use

1. Install [Python 3.11+](https://www.python.org/downloads/). On Windows, enable **Add Python to PATH**. From this folder, run `python -m pip install -r requirements.txt` and then `python app.py` (use `python3` on macOS). Or use a standalone build below.
2. Everyone clicks **Connect to Frincoms**. No account, room code, VPN, or router setup is needed.

Choose a **Microphone** and **Speakers / headphones** from the menus, or leave them at **System default**. Your selections are saved for next time. The input-level meter moves when the selected microphone hears sound, even before connecting. Click **Refresh devices** after plugging in a headset. If you switch devices during a call, disconnect and reconnect to apply the new selection. If a saved device is missing, select another one; the app will not silently switch microphones.

The app connects to the relay at `2.29.39.53:38452` by default. To change its address, enter an IP or hostname under **Relay address** and click **Save address**. The setting persists across restarts and takes effect on the next connection. Only change it if the **same relay** moves: the app pins the relay's TLS certificate, so a different relay certificate requires a client update. The port is fixed at `38452`.

Anyone who can reach the relay with a compatible client can enter the shared room; there is no membership or authentication. There is no fixed room-size limit. Each person stays connected when someone else leaves; a lone participant stays connected and sees one person in the room. The relay can hear the audio (TLS encrypts each connection to the relay); it does not save voice or messages. Everyone must update to the latest app to use the live participant count.

If the connection drops unexpectedly, Frincoms reconnects automatically (retrying after 1, 2, 4, 8, then 10 seconds). **Disconnect** stops further attempts immediately. If an audio device is unavailable, select a working one; Frincoms will keep trying to reconnect until you press **Disconnect**.

On macOS, allow microphone access when prompted. If using Homebrew Python, install its matching Tk package first (e.g. `brew install python-tk@3.14` for Python 3.14). Python from python.org includes Tk.

## Diagnosing disconnects

Click **Open diagnostic log** in Frincoms after a disconnect. The local log records connection attempts, TLS verification, audio-device errors, and whether the audio send or receive loop stopped first. It does not record voice. Logs rotate at 1 MB with two backups. The file is at `%APPDATA%\Frincoms\frincoms.log` on Windows or `~/Library/Application Support/Frincoms/frincoms.log` on macOS. If you share a log, review it first: it can include your relay address and audio device names. The relay's service log is separate (`ssh root@2.29.39.53 'journalctl -u frincoms-relay.service -f'`).

## Relay operator

The VPS runs `relay.py` as `frincoms-relay.service`. Check its status with `ssh root@2.29.39.53 'systemctl status frincoms-relay'`. Replacing the relay's certificate requires updating the fingerprint in `relay_config.py` and rebuilding both clients.

## Standalone builds

### Windows: download the GitHub Actions build

1. Open the private repo's [Actions → Build Windows app](https://github.com/joakimunge/frincoms/actions/workflows/windows-build.yml) page while signed in to GitHub. Select the latest successful run on `main` (or click **Run workflow** to make a new one).
2. Under **Artifacts**, download `Frincoms-Windows-x64` and extract it. Run `Frincoms.exe` on 64-bit Windows. No Python installation is needed on the machine running it.

Artifacts are retained for 30 days. The build runs the tests on Windows before uploading the executable. GitHub may show a SmartScreen warning because the executable is unsigned.

### Windows: build locally instead

1. Sign in to GitHub and download [build-windows.ps1](build-windows.ps1) from this private repository (open the file and click **Download raw file**). Save it to Downloads.
2. Open **PowerShell** in Downloads and run `powershell -NoProfile -ExecutionPolicy Bypass -File .\build-windows.ps1`. The script installs Git, GitHub CLI, and Python 3.12 with `winget` if missing; GitHub sign-in opens in a browser on first use. It then clones/updates the repo, installs dependencies in a local environment, runs tests, and builds the executable.
3. Open `%USERPROFILE%\frincoms\dist\Frincoms.exe`. Rerun the same script to update and rebuild. To choose a different checkout folder, append `-Destination "C:\path\to\frincoms"`.

Windows may ask you to confirm installations or allow the app through its security prompt. `winget` (Microsoft App Installer) and an internet connection are needed. GitHub access to the private repository is required.

### Linux: Docker build

From this repository, with Docker running:

```sh
bash build-linux.sh
```

This builds an **x86-64 Linux** app in `dist-linux/Frincoms/`. Copy the *whole folder* to a Linux desktop, then run `./Frincoms/Frincoms` from its parent directory. The recipient does not need Python, but needs a graphical desktop (X11 or Wayland), working sound devices, and standard desktop audio libraries. Tk and PortAudio are included in the bundle. For ARM Linux, build natively on that architecture with `python3 -m PyInstaller --noconfirm frincoms.spec` after installing Python Tk, PortAudio, and binutils. The macOS and Windows binaries cannot run on Linux.

### Manual build / macOS

Build separately on each operating system:

```sh
python -m pip install -r requirements.txt pyinstaller
python -m PyInstaller --noconfirm frincoms.spec
```

Use `python3` on macOS. Windows produces `dist/Frincoms.exe`; macOS produces `dist/Frincoms.app`; a native Linux build produces `dist/Frincoms/`. Standalone users do not need Python.
