# TrueNAS Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move Waydroid (LINE automation), OpenClaw, and Hermes off DGX Spark to TrueNAS Scale 25.10, freeing all unified memory for vLLM inference.

**Architecture:** Waydroid runs in a KVM VM (Ubuntu 24.04, 6 GB RAM, 64 GB disk) because TrueNAS kernel lacks binder. line-mcp serves via FastMCP SSE on port 8765. OpenClaw and Hermes run in a Docker Compose stack on TrueNAS, with their data dirs mounted as volumes. vllm-think-proxy stays on DGX Spark but binds to LAN.

**Tech Stack:** TrueNAS Scale 25.10 KVM, Ubuntu 24.04, Waydroid LineageOS 18.1 ARM64, FastMCP SSE, Node.js 22, Python 3.12, Docker Compose, Xvfb, Weston, x11vnc, systemd

---

## Prerequisites — Fill These In First

Before starting any task, record these values:

```
SPARK_IP=<DGX Spark LAN IP, e.g. 192.168.1.10>
VM_IP=<assigned after VM creation, e.g. 192.168.1.50>
LAN_SUBNET=<e.g. 192.168.1.0/24>
VNC_PASSWORD=<choose a password for x11vnc>
TRUENAS_IP=<TrueNAS LAN IP>
TRUENAS_POOL=<pool name, e.g. tank>
```

---

## Phase 1: Waydroid KVM VM

### Task 1: Create VM on TrueNAS

**Files:** None (TrueNAS web UI)

- [ ] **Step 1: Upload Ubuntu 24.04 server ISO to TrueNAS**

  In TrueNAS web UI: Storage > choose pool (`$TRUENAS_POOL`) > dataset `isos` (create if needed).
  Upload `ubuntu-24.04.2-live-server-amd64.iso` (download from https://ubuntu.com/download/server).

- [ ] **Step 2: Create the VM**

  Virtualization > Virtual Machines > Add:
  - Name: `waydroid-vm`
  - vCPUs: 4
  - Memory: 6144 MB
  - Bootloader: UEFI
  - Boot device: Disk

  Add devices:
  - **Disk:** zvol, 64 GB, on `$TRUENAS_POOL`, VirtIO
  - **NIC:** VirtIO, bridge `br0`
  - **CDROM:** ISO uploaded in Step 1
  - **Display:** VNC (for initial install only)

- [ ] **Step 3: Install Ubuntu 24.04 minimal**

  Start the VM, connect via VNC from TrueNAS web UI.
  Choose: minimal install, no extras, OpenSSH server enabled.
  User: `kanthai`, hostname: `waydroid-vm`.
  After install: eject ISO, reboot.

- [ ] **Step 4: Record VM IP and verify SSH**

  ```bash
  # From DGX Spark
  ssh kanthai@<vm-ip> "echo OK"
  # Expected: OK
  ```

  Record `VM_IP` from the VM's `ip addr` output.

- [ ] **Step 5: Commit placeholder**

  ```bash
  # On DGX Spark — record the VM IP for future steps
  echo "VM_IP=<recorded-ip>" >> ~/migration-vars.sh
  ```

---

### Task 2: Bootstrap Ubuntu VM

**Files:** (inside VM) `/etc/apt/`

- [ ] **Step 1: Update and install base packages**

  ```bash
  ssh kanthai@$VM_IP "sudo apt-get update && sudo apt-get upgrade -y && \
    sudo apt-get install -y curl wget git rsync ufw python3 python3-pip python3-venv \
      python3-full xvfb weston x11vnc sqlite3"
  ```

  Expected: packages install without errors.

- [ ] **Step 2: Configure ufw**

  ```bash
  ssh kanthai@$VM_IP "sudo ufw default deny incoming && \
    sudo ufw allow from $LAN_SUBNET to any port 22 && \
    sudo ufw allow from $LAN_SUBNET to any port 5900 && \
    sudo ufw allow from $LAN_SUBNET to any port 8765 && \
    sudo ufw --force enable && sudo ufw status"
  ```

  Expected: firewall active with three rules.

- [ ] **Step 3: Copy SSH authorized keys**

  ```bash
  # Allows passwordless SSH from DGX Spark
  ssh-copy-id kanthai@$VM_IP
  ```

---

### Task 3: Install Waydroid

**Files:** (inside VM) `/etc/apt/sources.list.d/waydroid.list`

- [ ] **Step 1: Add Waydroid repo and install**

  ```bash
  ssh kanthai@$VM_IP "curl -s https://repo.waydro.id | sudo bash && \
    sudo apt-get install -y waydroid"
  ```

  Expected: waydroid package installed.

- [ ] **Step 2: Verify waydroid binary exists**

  ```bash
  ssh kanthai@$VM_IP "which waydroid && waydroid --version"
  ```

  Expected: prints waydroid version.

- [ ] **Step 3: Install sudoers rule**

  ```bash
  scp ~/setup-waydroid-sudo.sh kanthai@$VM_IP:~/
  ssh kanthai@$VM_IP "sudo bash ~/setup-waydroid-sudo.sh"
  ```

  Expected: `done`

---

### Task 4: Migrate Waydroid Data

**Files:** (inside VM) `/var/lib/waydroid/`, `~/.local/share/waydroid/`

- [ ] **Step 1: Stop Waydroid on DGX Spark**

  ```bash
  # On DGX Spark
  sudo waydroid session stop
  sudo systemctl stop waydroid-container 2>/dev/null || true
  ```

- [ ] **Step 2: rsync images to VM**

  ```bash
  # On DGX Spark
  ssh kanthai@$VM_IP "sudo mkdir -p /var/lib/waydroid/images"
  rsync -avz --progress ~/waydroid-a11-images/system.img \
    ~/waydroid-a11-images/vendor.img \
    kanthai@$VM_IP:/tmp/waydroid-images/
  ssh kanthai@$VM_IP "sudo cp /tmp/waydroid-images/*.img /var/lib/waydroid/images/"
  ```

  Expected: both .img files transferred.

- [ ] **Step 3: rsync /var/lib/waydroid (config + overlay)**

  ```bash
  # On DGX Spark — exclude images/ since already copied
  sudo rsync -avz --progress --exclude='images/' \
    /var/lib/waydroid/ kanthai@$VM_IP:/tmp/waydroid-lib/
  ssh kanthai@$VM_IP "sudo rsync -a /tmp/waydroid-lib/ /var/lib/waydroid/"
  ```

- [ ] **Step 4: rsync ~/.local/share/waydroid (LINE app data + E2EE keys)**

  ```bash
  # On DGX Spark
  rsync -avz --progress ~/.local/share/waydroid/ \
    kanthai@$VM_IP:~/.local/share/waydroid/
  ```

- [ ] **Step 5: Fix ownership inside VM**

  ```bash
  ssh kanthai@$VM_IP "sudo chown -R root:root /var/lib/waydroid && \
    chown -R kanthai:kanthai ~/.local/share/waydroid"
  ```

---

### Task 5: Initialize Waydroid and Verify LINE

**Files:** (inside VM) — waydroid config

- [ ] **Step 1: Initialize Waydroid with existing images**

  ```bash
  ssh kanthai@$VM_IP "sudo waydroid init --images-path /var/lib/waydroid/images"
  ```

  Expected: `[OK] Waydroid is ready` (or similar — no download triggered).

- [ ] **Step 2: Start Waydroid container (headless test)**

  ```bash
  ssh kanthai@$VM_IP "sudo waydroid container start"
  sleep 10
  ssh kanthai@$VM_IP "waydroid status"
  ```

  Expected: `Session: STOPPED` / `Container: RUNNING` (session needs Wayland to start).

- [ ] **Step 3: Verify LINE database is accessible**

  ```bash
  ssh kanthai@$VM_IP "sudo waydroid shell -- ls /data/data/jp.naver.line.android/databases/"
  ```

  Expected: lists `naver_line`, `contact`, etc. (LINE is still logged in from migrated data).

- [ ] **Step 4: Stop container**

  ```bash
  ssh kanthai@$VM_IP "sudo waydroid container stop"
  ```

---

### Task 6: Set Up Display Stack (systemd units)

**Files:**
- Create: (VM) `/etc/systemd/system/xvfb.service`
- Create: (VM) `/etc/systemd/system/weston-xvfb.service`
- Create: (VM) `/etc/systemd/system/waydroid-session-xvfb.service`
- Create: (VM) `/etc/systemd/system/x11vnc.service`

- [ ] **Step 1: Write xvfb.service**

  ```bash
  ssh kanthai@$VM_IP "sudo tee /etc/systemd/system/xvfb.service" << 'EOF'
  [Unit]
  Description=Virtual X11 framebuffer
  After=network.target

  [Service]
  Type=simple
  ExecStart=/usr/bin/Xvfb :0 -screen 0 1920x1080x24 -nolisten tcp
  Restart=always
  RestartSec=3

  [Install]
  WantedBy=multi-user.target
  EOF
  ```

- [ ] **Step 2: Write weston-xvfb.service**

  ```bash
  ssh kanthai@$VM_IP "sudo tee /etc/systemd/system/weston-xvfb.service" << 'EOF'
  [Unit]
  Description=Weston compositor on Xvfb
  After=xvfb.service
  Requires=xvfb.service

  [Service]
  Type=simple
  User=kanthai
  Environment=DISPLAY=:0
  Environment=XAUTHORITY=/home/kanthai/.Xauthority
  ExecStart=/usr/bin/weston --backend=x11-backend.so --socket=wayland-0 --idle-time=0
  Restart=always
  RestartSec=5

  [Install]
  WantedBy=multi-user.target
  EOF
  ```

- [ ] **Step 3: Write waydroid-session-xvfb.service**

  ```bash
  ssh kanthai@$VM_IP "sudo tee /etc/systemd/system/waydroid-session-xvfb.service" << 'EOF'
  [Unit]
  Description=Waydroid session on Xvfb/Weston
  After=weston-xvfb.service
  Requires=weston-xvfb.service

  [Service]
  Type=simple
  User=kanthai
  Environment=WAYLAND_DISPLAY=wayland-0
  ExecStart=/usr/bin/waydroid session start
  ExecStop=/usr/bin/waydroid session stop
  Restart=always
  RestartSec=10

  [Install]
  WantedBy=multi-user.target
  EOF
  ```

- [ ] **Step 4: Write x11vnc.service**

  Replace `$VNC_PASSWORD` with your chosen password before running.

  ```bash
  VNC_PASSWORD=<your-password>
  ssh kanthai@$VM_IP "sudo tee /etc/systemd/system/x11vnc.service" << EOF
  [Unit]
  Description=VNC server for Xvfb display :0
  After=xvfb.service
  Requires=xvfb.service

  [Service]
  Type=simple
  User=kanthai
  ExecStart=/usr/bin/x11vnc -display :0 -forever -noxdamage -passwd ${VNC_PASSWORD} -rfbport 5900 -listen 0.0.0.0
  Restart=always
  RestartSec=5

  [Install]
  WantedBy=multi-user.target
  EOF
  ```

- [ ] **Step 5: Enable and start the stack**

  ```bash
  ssh kanthai@$VM_IP "sudo systemctl daemon-reload && \
    sudo systemctl enable xvfb weston-xvfb waydroid-session-xvfb x11vnc && \
    sudo systemctl start xvfb && sleep 3 && \
    sudo systemctl start weston-xvfb && sleep 3 && \
    sudo systemctl start waydroid-session-xvfb && sleep 15 && \
    sudo systemctl start x11vnc"
  ```

- [ ] **Step 6: Verify all units running**

  ```bash
  ssh kanthai@$VM_IP "systemctl status xvfb weston-xvfb waydroid-session-xvfb x11vnc --no-pager"
  ```

  Expected: all four units `active (running)`.

- [ ] **Step 7: Verify Waydroid session is up**

  ```bash
  ssh kanthai@$VM_IP "waydroid status"
  ```

  Expected: `Session: RUNNING`, `Container: RUNNING`.

---

### Task 7: Set Up Watchdogs

**Files:**
- Create: (VM) `/usr/local/bin/waydroid-watchdog.sh`
- Create: (VM) `/usr/local/bin/line-watchdog.sh`
- Create: (VM) `/usr/local/bin/line-foreground-pulse.sh`
- Modify: (VM) `/etc/systemd/system/` — copy timer units

- [ ] **Step 1: Copy watchdog scripts to VM**

  ```bash
  # On DGX Spark
  scp ~/line-mcp/tools/waydroid-watchdog.sh kanthai@$VM_IP:/tmp/
  scp ~/line-mcp/tools/line-watchdog.sh kanthai@$VM_IP:/tmp/
  scp ~/line-mcp/tools/line-foreground-pulse.sh kanthai@$VM_IP:/tmp/

  ssh kanthai@$VM_IP "sudo cp /tmp/waydroid-watchdog.sh /usr/local/bin/ && \
    sudo cp /tmp/line-watchdog.sh /usr/local/bin/ && \
    sudo cp /tmp/line-foreground-pulse.sh /usr/local/bin/ && \
    sudo chmod +x /usr/local/bin/waydroid-watchdog.sh \
                  /usr/local/bin/line-watchdog.sh \
                  /usr/local/bin/line-foreground-pulse.sh"
  ```

- [ ] **Step 2: Update waydroid-watchdog.sh for new service names**

  The watchdog restarts waydroid. Edit `/usr/local/bin/waydroid-watchdog.sh` on the VM to restart `waydroid-session-xvfb` instead of whatever service it currently references:

  ```bash
  ssh kanthai@$VM_IP "sudo sed -i \
    's/waydroid-container/waydroid-session-xvfb/g' \
    /usr/local/bin/waydroid-watchdog.sh"
  ```

- [ ] **Step 3: Install systemd timer units**

  ```bash
  # On DGX Spark
  for unit in waydroid-watchdog line-watchdog line-token-refresh; do
    scp ~/line-mcp/systemd/${unit}.service kanthai@$VM_IP:/tmp/
    scp ~/line-mcp/systemd/${unit}.timer kanthai@$VM_IP:/tmp/ 2>/dev/null || true
  done
  ssh kanthai@$VM_IP "sudo cp /tmp/waydroid-watchdog.* /etc/systemd/system/ && \
    sudo cp /tmp/line-watchdog.* /etc/systemd/system/ && \
    sudo cp /tmp/line-token-refresh.* /etc/systemd/system/ 2>/dev/null || true"
  ```

- [ ] **Step 4: Enable watchdog timers**

  ```bash
  ssh kanthai@$VM_IP "sudo systemctl daemon-reload && \
    sudo systemctl enable --now waydroid-watchdog.timer line-watchdog.timer"
  ```

- [ ] **Step 5: Verify LINE app is running**

  ```bash
  ssh kanthai@$VM_IP "sudo waydroid shell -- ps -A | grep jp.naver.line"
  ```

  Expected: LINE process appears in output.

---

## Phase 2: line-mcp SSE Server

### Task 8: Migrate line-mcp Repo to VM

**Files:**
- Create: (VM) `~/line-mcp/` (full repo)
- Create: (VM) `~/line-mcp/venv/`

- [ ] **Step 1: rsync line-mcp repo to VM**

  ```bash
  # On DGX Spark
  rsync -avz --progress \
    --exclude='__pycache__' --exclude='*.pyc' --exclude='.pytest_cache' \
    ~/line-mcp/ kanthai@$VM_IP:~/line-mcp/
  ```

- [ ] **Step 2: Create Python venv and install deps**

  ```bash
  ssh kanthai@$VM_IP "cd ~/line-mcp && \
    python3 -m venv venv && \
    venv/bin/pip install --upgrade pip && \
    venv/bin/pip install -r requirements.txt"
  ```

  Expected: all packages install without errors.

- [ ] **Step 3: Verify DB read works**

  ```bash
  ssh kanthai@$VM_IP "cd ~/line-mcp && \
    PYTHONPATH=tools venv/bin/python3 -c \
    'from line_db import list_chats; chats = list_chats(); print(f\"{len(chats)} chats found\")'"
  ```

  Expected: prints a positive number of chats.

---

### Task 9: Switch line-mcp to FastMCP SSE

**Files:**
- Modify: `~/line-mcp/mcp/server.py` (last line)
- Create: (VM) `/etc/systemd/system/line-mcp.service`

- [ ] **Step 1: Write a connectivity test**

  ```bash
  # On DGX Spark — create this test file
  cat > /tmp/test_line_mcp_sse.sh << 'EOF'
  #!/bin/bash
  # Test that SSE endpoint is reachable
  VM_IP=$1
  RESPONSE=$(curl -s -o /dev/null -w "%{http_code}" \
    --max-time 5 \
    -H "Accept: text/event-stream" \
    "http://${VM_IP}:8765/sse")
  if [ "$RESPONSE" = "200" ]; then
    echo "PASS: SSE endpoint reachable (HTTP 200)"
    exit 0
  else
    echo "FAIL: got HTTP $RESPONSE"
    exit 1
  fi
  EOF
  chmod +x /tmp/test_line_mcp_sse.sh
  ```

- [ ] **Step 2: Run test to verify it fails (server not up yet)**

  ```bash
  /tmp/test_line_mcp_sse.sh $VM_IP
  ```

  Expected: `FAIL: got HTTP 000` (connection refused).

- [ ] **Step 3: Change server.py to SSE transport**

  On VM, edit `~/line-mcp/mcp/server.py`. Find the last `server.run()` call and replace it:

  ```bash
  ssh kanthai@$VM_IP "grep -n 'server.run' ~/line-mcp/mcp/server.py"
  ```

  Note the line number, then replace:

  ```bash
  ssh kanthai@$VM_IP "sed -i 's/server\.run()/server.run(transport=\"sse\", host=\"0.0.0.0\", port=8765)/' \
    ~/line-mcp/mcp/server.py"
  ```

  Verify:

  ```bash
  ssh kanthai@$VM_IP "grep 'server.run' ~/line-mcp/mcp/server.py"
  ```

  Expected: `server.run(transport="sse", host="0.0.0.0", port=8765)`

- [ ] **Step 4: Create line-mcp.service systemd unit**

  ```bash
  ssh kanthai@$VM_IP "sudo tee /etc/systemd/system/line-mcp.service" << 'EOF'
  [Unit]
  Description=line-mcp FastMCP SSE server
  After=waydroid-session-xvfb.service
  Requires=waydroid-session-xvfb.service

  [Service]
  Type=simple
  User=kanthai
  WorkingDirectory=/home/kanthai/line-mcp
  Environment=WAYLAND_DISPLAY=wayland-0
  ExecStart=/home/kanthai/line-mcp/venv/bin/python3 /home/kanthai/line-mcp/mcp/server.py
  Restart=always
  RestartSec=10
  StandardOutput=journal
  StandardError=journal

  [Install]
  WantedBy=multi-user.target
  EOF
  ```

- [ ] **Step 5: Enable and start line-mcp**

  ```bash
  ssh kanthai@$VM_IP "sudo systemctl daemon-reload && \
    sudo systemctl enable --now line-mcp"
  sleep 5
  ssh kanthai@$VM_IP "systemctl status line-mcp --no-pager"
  ```

  Expected: `active (running)`.

- [ ] **Step 6: Run connectivity test from DGX Spark**

  ```bash
  /tmp/test_line_mcp_sse.sh $VM_IP
  ```

  Expected: `PASS: SSE endpoint reachable (HTTP 200)`

- [ ] **Step 7: Commit server.py change to line-mcp repo (on VM)**

  ```bash
  ssh kanthai@$VM_IP "cd ~/line-mcp && \
    git add mcp/server.py && \
    git commit -m 'feat: switch MCP transport from stdio to FastMCP SSE on port 8765'"
  ```

- [ ] **Step 8: Pull the commit back to DGX Spark**

  ```bash
  cd ~/line-mcp && git pull
  ```

---

## Phase 3: OpenClaw + Hermes Docker Stack

### Task 10: Create TrueNAS Dataset and Docker Files

**Files:**
- Create: `/mnt/$TRUENAS_POOL/agent-data/` (TrueNAS dataset)
- Create: `/opt/agent-stack/docker-compose.yml` (on TrueNAS)
- Create: `/opt/agent-stack/openclaw/Dockerfile`
- Create: `/opt/agent-stack/hermes/Dockerfile`

These steps run **on TrueNAS** (ssh into TrueNAS shell or use the web Shell).

- [ ] **Step 1: Create TrueNAS dataset for agent data**

  In TrueNAS web UI: Storage > Datasets > Add Dataset.
  - Parent: `$TRUENAS_POOL`
  - Name: `agent-data`
  - Leave defaults (no special settings needed)

  Or via TrueNAS shell:
  ```bash
  zfs create ${TRUENAS_POOL}/agent-data
  ```

- [ ] **Step 2: Create directory structure on TrueNAS**

  ```bash
  # On TrueNAS shell
  mkdir -p /opt/agent-stack/openclaw
  mkdir -p /opt/agent-stack/hermes
  mkdir -p /mnt/${TRUENAS_POOL}/agent-data/openclaw
  mkdir -p /mnt/${TRUENAS_POOL}/agent-data/hermes
  mkdir -p /mnt/${TRUENAS_POOL}/agent-data/config
  mkdir -p /mnt/${TRUENAS_POOL}/agent-data/vault
  mkdir -p /mnt/${TRUENAS_POOL}/agent-data/npm-global
  ```

- [ ] **Step 3: Write openclaw/Dockerfile**

  ```bash
  # On TrueNAS shell
  cat > /opt/agent-stack/openclaw/Dockerfile << 'EOF'
  FROM node:22-slim
  RUN apt-get update && apt-get install -y python3 python3-pip curl wget git \
      libssl-dev ca-certificates && rm -rf /var/lib/apt/lists/*
  RUN useradd -m -s /bin/bash -u 1000 kanthai
  USER kanthai
  WORKDIR /home/kanthai
  ENV HOME=/home/kanthai
  CMD ["node", "/home/kanthai/.npm-global/lib/node_modules/openclaw/openclaw.mjs"]
  EOF
  ```

- [ ] **Step 4: Write hermes/Dockerfile**

  ```bash
  # On TrueNAS shell
  cat > /opt/agent-stack/hermes/Dockerfile << 'EOF'
  FROM ubuntu:24.04
  RUN apt-get update && apt-get install -y python3 python3-venv python3-pip \
      libssl-dev ca-certificates curl wget git sqlite3 && rm -rf /var/lib/apt/lists/*
  RUN useradd -m -s /bin/bash -u 1000 kanthai
  USER kanthai
  WORKDIR /home/kanthai
  ENV HOME=/home/kanthai
  CMD ["/home/kanthai/.hermes/hermes-agent/venv/bin/hermes"]
  EOF
  ```

- [ ] **Step 5: Write docker-compose.yml**

  Replace `$TRUENAS_POOL` with your actual pool name before running.

  ```bash
  # On TrueNAS shell
  POOL=<your-pool-name>
  cat > /opt/agent-stack/docker-compose.yml << EOF
  services:
    openclaw:
      build: ./openclaw
      container_name: openclaw
      restart: unless-stopped
      network_mode: host
      environment:
        - HOME=/home/kanthai
        - NODE_PATH=/home/kanthai/.npm-global/lib/node_modules
      volumes:
        - /mnt/${POOL}/agent-data/openclaw:/home/kanthai/.openclaw
        - /mnt/${POOL}/agent-data/npm-global:/home/kanthai/.npm-global:ro
        - /mnt/${POOL}/agent-data/config:/home/kanthai/.config:ro
        - /mnt/${POOL}/agent-data/vault:/home/kanthai/vault:ro

    hermes:
      build: ./hermes
      container_name: hermes
      restart: unless-stopped
      network_mode: host
      environment:
        - HOME=/home/kanthai
      volumes:
        - /mnt/${POOL}/agent-data/hermes:/home/kanthai/.hermes
        - /mnt/${POOL}/agent-data/config:/home/kanthai/.config:ro
  EOF
  ```

---

### Task 11: Migrate OpenClaw Data to TrueNAS

**Files:** TrueNAS `/mnt/$TRUENAS_POOL/agent-data/openclaw/`, `npm-global/`, `config/`

- [ ] **Step 1: rsync ~/.openclaw to TrueNAS**

  ```bash
  # On DGX Spark
  rsync -avz --progress \
    --exclude='logs/' --exclude='*.log' \
    ~/.openclaw/ kanthai@$TRUENAS_IP:/mnt/$TRUENAS_POOL/agent-data/openclaw/
  ```

  Note: exclude logs to save time; they're not needed.

- [ ] **Step 2: rsync ~/.npm-global to TrueNAS**

  ```bash
  # On DGX Spark
  rsync -avz --progress \
    ~/.npm-global/ kanthai@$TRUENAS_IP:/mnt/$TRUENAS_POOL/agent-data/npm-global/
  ```

- [ ] **Step 3: rsync ~/.config/openclaw.env to TrueNAS**

  ```bash
  # On DGX Spark
  rsync -avz --progress \
    ~/.config/openclaw.env \
    kanthai@$TRUENAS_IP:/mnt/$TRUENAS_POOL/agent-data/config/
  ```

- [ ] **Step 4: Fix ownership on TrueNAS**

  ```bash
  # On TrueNAS shell
  POOL=<your-pool-name>
  chown -R 1000:1000 /mnt/${POOL}/agent-data/openclaw
  chown -R 1000:1000 /mnt/${POOL}/agent-data/npm-global
  chmod 600 /mnt/${POOL}/agent-data/config/openclaw.env
  ```

---

### Task 12: Migrate Hermes Data to TrueNAS

**Files:** TrueNAS `/mnt/$TRUENAS_POOL/agent-data/hermes/`

- [ ] **Step 1: rsync ~/.hermes to TrueNAS**

  ```bash
  # On DGX Spark
  rsync -avz --progress \
    --exclude='cron/output/' \
    --exclude='browser_screenshots/' \
    ~/.hermes/ kanthai@$TRUENAS_IP:/mnt/$TRUENAS_POOL/agent-data/hermes/
  ```

- [ ] **Step 2: Fix ownership on TrueNAS**

  ```bash
  # On TrueNAS shell
  POOL=<your-pool-name>
  chown -R 1000:1000 /mnt/${POOL}/agent-data/hermes
  ```

---

### Task 13: Update Config Files (localhost → spark-ip)

**Files:**
- Modify: `/mnt/$TRUENAS_POOL/agent-data/hermes/config.yaml`
- Modify: `/mnt/$TRUENAS_POOL/agent-data/openclaw/openclaw.json`

- [ ] **Step 1: Update all localhost URLs in hermes/config.yaml**

  ```bash
  # On TrueNAS shell
  POOL=<your-pool-name>
  SPARK_IP=<dgx-spark-lan-ip>
  VM_IP=<waydroid-vm-lan-ip>

  sed -i "s|http://localhost:|http://${SPARK_IP}:|g" \
    /mnt/${POOL}/agent-data/hermes/config.yaml
  sed -i "s|http://127\.0\.0\.1:|http://${SPARK_IP}:|g" \
    /mnt/${POOL}/agent-data/hermes/config.yaml
  ```

  Verify:
  ```bash
  grep "base_url" /mnt/${POOL}/agent-data/hermes/config.yaml | head -5
  ```

  Expected: all `base_url` values show `$SPARK_IP`, not `localhost`.

- [ ] **Step 2: Update localhost URLs in openclaw.json**

  ```bash
  # On TrueNAS shell
  POOL=<your-pool-name>
  SPARK_IP=<dgx-spark-lan-ip>

  sed -i "s|http://127\.0\.0\.1:|http://${SPARK_IP}:|g" \
    /mnt/${POOL}/agent-data/openclaw/openclaw.json
  sed -i "s|http://localhost:|http://${SPARK_IP}:|g" \
    /mnt/${POOL}/agent-data/openclaw/openclaw.json
  ```

  Verify:
  ```bash
  grep -E "baseUrl|base_url" /mnt/${POOL}/agent-data/openclaw/openclaw.json | head -10
  ```

  Expected: all URLs show `$SPARK_IP`.

- [ ] **Step 3: Update line-mcp MCP entry in openclaw.json from stdio to SSE**

  The current `mcp_servers.line-mcp` entry uses `command/args` (stdio). Replace it with SSE URL.

  ```bash
  # On TrueNAS shell
  POOL=<your-pool-name>
  VM_IP=<waydroid-vm-lan-ip>
  python3 << PYEOF
  import json, sys

  path = f"/mnt/${POOL}/agent-data/openclaw/openclaw.json"
  with open(path) as f:
      cfg = json.load(f)

  # Replace stdio entry with SSE URL
  cfg["mcp_servers"]["line-mcp"] = {
      "url": f"http://${VM_IP}:8765/sse",
      "enabled": True
  }

  with open(path, "w") as f:
      json.dump(cfg, f, indent=2)

  print("Done")
  PYEOF
  ```

  Verify:
  ```bash
  python3 -c "import json; cfg=json.load(open('/mnt/${POOL}/agent-data/openclaw/openclaw.json')); print(cfg['mcp_servers']['line-mcp'])"
  ```

  Expected: `{'url': 'http://<VM_IP>:8765/sse', 'enabled': True}`

- [ ] **Step 4: Migrate vault to TrueNAS (for obsidian MCP)**

  ```bash
  # On DGX Spark
  rsync -avz --progress \
    ~/vault/ kanthai@$TRUENAS_IP:/mnt/$TRUENAS_POOL/agent-data/vault/
  ```

---

## Phase 4: Expose vllm-think-proxy to LAN

### Task 14: Rebind Think Proxy on DGX Spark

**Files:**
- Modify: `~/vllm-think-proxy.py` (bind address)
- Modify: `~/openclaw-spark/services/vllm-think-proxy.service` (or wherever the service file lives)

- [ ] **Step 1: Write a test that the proxy is currently LAN-unreachable**

  ```bash
  # On DGX Spark — test from another machine's perspective using curl with the LAN IP
  SPARK_IP=<dgx-spark-lan-ip>
  curl -s -o /dev/null -w "%{http_code}" --max-time 3 "http://${SPARK_IP}:8017/v1/models"
  ```

  Expected: `000` (connection refused — proxy only listens on 127.0.0.1).

- [ ] **Step 2: Find and update the bind address in vllm-think-proxy.py**

  ```bash
  grep -n "host\|0\.0\.0\.0\|127\.0\.0\.1\|localhost" ~/vllm-think-proxy.py | head -10
  ```

  Find the `web.run_app` or `runner.setup()` call and change the host:

  ```bash
  # The proxy uses aiohttp. Find the run call:
  grep -n "run_app\|web.run\|host=" ~/vllm-think-proxy.py
  ```

  Edit `~/vllm-think-proxy.py` — change `host='127.0.0.1'` to `host='0.0.0.0'`:

  ```bash
  sed -i "s/host='127\.0\.0\.1'/host='0.0.0.0'/" ~/vllm-think-proxy.py
  sed -i 's/host="127\.0\.0\.1"/host="0.0.0.0"/' ~/vllm-think-proxy.py
  ```

  Verify:
  ```bash
  grep "host=" ~/vllm-think-proxy.py
  ```

- [ ] **Step 3: Restart the think proxy**

  ```bash
  # Kill existing proxy
  pkill -f vllm-think-proxy || true
  sleep 2
  # Start with new bind address (as systemd service or manually)
  python3 ~/vllm-think-proxy.py --port 8017 --backend http://localhost:8007 &
  sleep 3
  ```

- [ ] **Step 4: Verify LAN reachability**

  ```bash
  SPARK_IP=<dgx-spark-lan-ip>
  curl -s -o /dev/null -w "%{http_code}" --max-time 5 "http://${SPARK_IP}:8017/v1/models"
  ```

  Expected: `200`

- [ ] **Step 5: Commit vllm-think-proxy change**

  ```bash
  cd ~/openclaw-spark
  git add ../vllm-think-proxy.py
  git commit -m "fix: bind think proxy to 0.0.0.0 for LAN access from TrueNAS"
  ```

---

## Phase 5: Start Docker Stack and Cutover

### Task 15: Build and Start Docker Stack

- [ ] **Step 1: Build Docker images**

  ```bash
  # On TrueNAS shell
  cd /opt/agent-stack
  docker compose build
  ```

  Expected: both images build without errors (~2-5 min each on first build).

- [ ] **Step 2: Start hermes first (simpler, no Telegram bot)**

  ```bash
  # On TrueNAS shell
  cd /opt/agent-stack
  docker compose up -d hermes
  sleep 5
  docker compose logs hermes --tail=30
  ```

  Expected: Hermes starts, connects to vLLM at `$SPARK_IP`.

- [ ] **Step 3: Verify Hermes can reach vLLM**

  ```bash
  # On TrueNAS shell
  docker exec hermes curl -s http://<SPARK_IP>:8000/v1/models | python3 -m json.tool
  ```

  Expected: JSON listing of available models.

- [ ] **Step 4: Start OpenClaw**

  ```bash
  # On TrueNAS shell
  cd /opt/agent-stack
  docker compose up -d openclaw
  sleep 10
  docker compose logs openclaw --tail=50
  ```

  Expected: OpenClaw starts, Telegram bot comes online (check Telegram — bot should respond).

- [ ] **Step 5: Verify line-mcp MCP tool is reachable from OpenClaw**

  ```bash
  # On TrueNAS shell
  docker exec openclaw curl -s -o /dev/null -w "%{http_code}" \
    --max-time 5 \
    -H "Accept: text/event-stream" \
    "http://<VM_IP>:8765/sse"
  ```

  Expected: `200`

- [ ] **Step 6: Test LINE message read via Telegram**

  In Telegram, send Nyx (OpenClaw): `list my LINE chats`
  Expected: Nyx responds with LINE chats (pulls via line-mcp SSE → Waydroid on VM).

---

### Task 16: Stop Services on DGX Spark

Only do this after Task 15 is fully verified.

- [ ] **Step 1: Stop OpenClaw and Hermes on DGX Spark**

  ```bash
  # On DGX Spark — stop any running openclaw/hermes processes
  pkill -f "openclaw" || true
  pkill -f "hermes" || true
  ```

- [ ] **Step 2: Stop line-mcp stdio server on DGX Spark (if running)**

  ```bash
  pkill -f "line-mcp/mcp/server.py" || true
  ```

- [ ] **Step 3: Stop Waydroid on DGX Spark**

  ```bash
  sudo waydroid session stop
  sudo systemctl stop waydroid-container 2>/dev/null || true
  sudo systemctl disable waydroid-container 2>/dev/null || true
  ```

- [ ] **Step 4: Verify DGX Spark GPU memory is fully available**

  ```bash
  nvidia-smi
  ```

  Expected: only vLLM processes consuming GPU memory — no openclaw/hermes/waydroid processes.

- [ ] **Step 5: Final smoke test from Telegram**

  Send Nyx: `what are my recent LINE messages?`
  Expected: recent messages listed from LINE (full stack working: Telegram → OpenClaw on TrueNAS → line-mcp SSE → Waydroid VM → LINE DB).

---

## Self-Review Notes

- **Spec section: vllm-think-proxy bind change** → Task 14 ✓
- **Spec section: FastMCP SSE change** → Task 9 ✓
- **Spec section: openclaw.json line-mcp stdio → SSE** → Task 13 Step 3 ✓
- **Spec section: hermes config.yaml localhost → spark-ip** → Task 13 Step 1 ✓
- **Spec section: openclaw.json localhost → spark-ip** → Task 13 Step 2 ✓
- **Spec section: Waydroid data migration** → Tasks 4-5 ✓
- **Spec section: line-mcp watchdogs** → Task 7 ✓
- **Spec section: display stack 1920×1080 landscape** → Task 6 (Xvfb args: `1920x1080x24`) ✓
- **Spec section: VNC port 5900** → Task 6 Step 4 ✓
- **Spec section: vault mounted for obsidian MCP** → Task 13 Step 4 ✓
- **Dependency: VM_IP must be known before Tasks 13, 14** → Prerequisites section notes this ✓
