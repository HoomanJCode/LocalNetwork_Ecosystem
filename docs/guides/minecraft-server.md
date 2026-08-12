# Host a Minecraft Server at Home — Friends Connect from Anywhere

> **Goal:** Run a Minecraft server on your home computer or a Raspberry Pi.
> Friends connect as if you're on the same LAN — no port forwarding, no public IP,
> no router config. Everything is encrypted.

This guide covers **two approaches**:

| Approach | What you get | Requirements | Best for |
|----------|-------------|-------------|----------|
| **Service Exposure** (recommended) | Friends connect to `localhost:25565` through their client tunnel | Nothing special | Most users |
| **TUN Mode** (full virtual LAN) | Friends see your server at `25.1.0.2:25565` like a real LAN | Root/admin for TUN | Advanced users who want full LAN emulation |

---

## Prerequisites

- **One cloud server or VPS** (any cheap $5/mo VPS works — see [providers](#appendix-cheap-vps-options))
- **Python 3.10+** on both your home server and the VPS
- **Minecraft Java Edition server** (vanilla, Paper, Spigot, Fabric, Forge — any)

---

## Overview

```
┌─────────────────┐         ┌──────────────────┐         ┌─────────────────┐
│  Your Home PC   │ ◄─────► │  Mediation       │ ◄─────► │  Friend's PC    │
│  (Minecraft     │  P2P    │  Server (VPS)     │  P2P    │  (Minecraft     │
│   server        │ tunnel  │  port 54000       │ tunnel  │   client)       │
│   port 25565)   │         │                   │         │                 │
└─────────────────┘         └──────────────────┘         └─────────────────┘

  1. Both sides connect to the mediation server on the VPS
  2. The server helps them find each other, then gets out of the way
  3. All Minecraft traffic flows directly between friends, fully encrypted
  4. No port forwarding, no public IP needed on your home router
```

### What you and your friends need to install

- **You** (home server): `localnetwork-client` — connects to the VPS, exposes port 25565
- **Your friends**: `localnetwork-client` — connects to the VPS, maps your server to their localhost
- **VPS**: `localnetwork-server` — the lightweight mediation server that introduces everyone

All three are installed with `pip install localnetwork-ecosystem`.

---

## Approach 1: Service Exposure (Simplest)

Friends connect to `localhost:25565` in their Minecraft client. No root needed.

### Step 1: Set up the mediation server (on your VPS)

SSH into your VPS:

```bash
ssh user@your-vps-ip
```

Install and start the server:

```bash
pip install localnetwork-ecosystem
localnetwork-server --host 0.0.0.0 --port 54000
```

> ✅ Leave this running. You should see: `mediation server listening on 0.0.0.0:54000`
>
> Make sure TCP port `54000` is open on the VPS firewall.

### Step 2: Start your Minecraft server at home

On your home computer, start the Minecraft server:

```bash
# Vanilla server
java -Xmx2G -jar server.jar nogui

# Or PaperMC (recommended for better performance)
java -Xmx2G -jar paper.jar nogui
```

You should see: `Done! For help, type "help"` — your server is running on `localhost:25565`.

### Step 3: Connect your home computer to the VPS and expose the server

In a new terminal on your home computer:

```bash
# Install LocalNetwork
pip install localnetwork-ecosystem

# Connect to the VPS
localnetwork-client --server your-vps-ip:54000
```

You should see:

```
identity ready (fingerprint a1b2c3…)
connected to server your-vps-ip:54000
client daemon running (tun=False)
```

### Step 4: Create a network and expose Minecraft

Open a third terminal on your home computer:

```bash
# Create a network for your Minecraft group
localnetwork-cli --host your-vps-ip create minecraft-world --password mcSecret123
```

Output:

```
Created network 'minecraft-world'
  Network ID:  7f9c2b14-a1b2-4c3d-...
  Invite code: a3x9k2bc
Share the invite code (or network ID) and password with friends.
```

Now expose the Minecraft port:

```bash
# Expose Minecraft (TCP port 25565) to your network
localnetwork-cli expose minecraft-server tcp 25565 --network minecraft-world
```

### Step 5: Friends connect

Your friends run these commands on their computers:

```bash
# Install
pip install localnetwork-ecosystem

# Connect to the VPS
localnetwork-client --server your-vps-ip:54000

# Join your network using the invite code
localnetwork-cli --host your-vps-ip join a3x9k2bc --password mcSecret123

# Map your Minecraft server to their localhost
localnetwork-cli map minecraft-server --local-port 25565
```

Now they open Minecraft, click **Multiplayer → Direct Connection**, and type:

```
localhost:25565
```

That's it! They're connected directly to your home server, fully encrypted.

### Step 6: Using the Web Panel (optional)

Open `http://localhost:54002` in your browser. The **Feature Launcher** dashboard shows:

- 🟢 **VPN Connection**: Running
- 📦 **Service Exposure**: Running — your Minecraft port is listed

Click any feature to expand its config and see live status. You can start/stop services right from the browser.

---

## Approach 2: TUN Mode (Full Virtual LAN)

With TUN Mode, your Minecraft server gets a **real virtual IP** like `25.1.0.2`.
Friends see it on their virtual LAN — they can ping it, connect via any IP-based tool,
and it works with any game, not just Minecraft.

> **Requires:** root/admin for the `--tun` flag (creates a virtual network interface).

### Step 1: Set up the mediation server (same as above)

```bash
# On your VPS
pip install localnetwork-ecosystem
localnetwork-server --host 0.0.0.0 --port 54000
```

### Step 2: Connect your home PC with TUN enabled

```bash
# On your home PC — needs root for TUN
sudo localnetwork-client --server your-vps-ip:54000 --tun --virtual-ip 25.1.0.1
```

> macOS: no `sudo` needed for `utun`.
> Windows: run as Administrator.

### Step 3: Friends connect with TUN enabled

```bash
# On friend's PC
sudo localnetwork-client --server your-vps-ip:54000 --tun --virtual-ip 25.1.0.2
```

### Step 4: Create and join the network

**On your home PC:**

```bash
localnetwork-cli --host your-vps-ip create minecraft-lan --password mcSecret123
```

Output includes an **invite code** like `a3x9k2bc`. Share it.

**On friend's PC:**

```bash
localnetwork-cli --host your-vps-ip join a3x9k2bc --password mcSecret123
```

### Step 5: Verify the virtual LAN

On your friend's PC:

```bash
ping 25.1.0.1
```

```
64 bytes from 25.1.0.1: icmp_seq=1 ttl=64 time=12.3 ms
```

The virtual LAN is working. Now open Minecraft and connect to `25.1.0.1:25565` — just like a real LAN party.

### What you get with TUN mode

| Capability | Without TUN | With TUN |
|-----------|:----------:|:--------:|
| Minecraft (port 25565) | ✅ (service exposure) | ✅ |
| Ping between peers | ❌ | ✅ |
| SSH into friend's machine | ❌ | ✅ |
| Any IP-based app/game | ❌ | ✅ |
| File sharing (SMB/NFS) | ❌ | ✅ |
| LAN server browser discovery | ❌ | ✅ |
| Multiple ports (no extra config) | ❌ | ✅ |

---

## Troubleshooting

| Problem | Likely fix |
|---------|-----------|
| `ConnectionRefusedError` | Is the VPS server running? Check `localnetwork-server` is listening on port 54000. Firewall blocking TCP 54000? |
| `WRONG_PASSWORD` on join | Passwords are case-sensitive. Ask the network owner to double-check. |
| "Can't connect to server" in Minecraft | Make sure the friend ran `localnetwork-cli map minecraft-server --local-port 25565` and is trying `localhost:25565`. The service mapping creates a local tunnel — it won't work if the Minecraft client tries a different address. |
| Friends see "Connection timed out" | If both friends are behind restrictive NATs, the relay fallback kicks in automatically. Check the client logs for `relay fallback` messages. Slightly higher latency is expected. |
| High ping / rubberbanding | You're likely on relay fallback. Both sides should try enabling UPnP on their routers, or one side should have a more open NAT (e.g., direct connection, DMZ, or port forward UDP for the client). |
| `Permission denied` on `--tun` | TUN mode requires root. On Linux: use `sudo`. On macOS: no root needed for `utun`. On Windows: run terminal as Administrator. |
| Server not showing in multiplayer list | Minecraft's LAN server browser uses multicast which doesn't route over TUN by default. Use **Direct Connection** and type the virtual IP. |
| Can't install on the VPS | Any Linux VPS with Python 3.10+ works. Ubuntu 22.04, Debian 12, Rocky Linux 9 — all fine. Use `python3 -m pip install` if `pip` isn't found. |

---

## Performance Tips

1. **Use PaperMC** instead of vanilla Minecraft server — significantly better performance
2. **Pre-generate chunks** with the Chunky plugin before inviting friends
3. **Allocate enough RAM** — `-Xmx4G` for 5-10 players, `-Xmx8G` for modded servers
4. **Enable UPnP** on your home router for best P2P performance (avoids relay fallback)
5. **Keep the VPS lightweight** — the mediation server uses <50 MB RAM, so even a $4/mo VPS is plenty

---

## Security

Your Minecraft traffic is **encrypted end-to-end**:

| Layer | Protection |
|-------|-----------|
| Control channel | AES-256-GCM authenticated encryption |
| P2P tunnels | AES-256-GCM with monotonic sequence numbers (replay protection) |
| Identity | RSA-2048 key pair, never transmitted |
| Network access | bcrypt-hashed password, server-enforced membership |
| Zero trust | The VPS never sees your private keys or session keys. It can't decrypt your traffic — even when relaying. |

The VPS only knows **who** is in the network (IP addresses), not **what** they're doing (game traffic is encrypted).

---

## Quick Reference Card

```bash
# === YOU (home server) ===
java -Xmx4G -jar paper.jar nogui &                    # Start Minecraft
pip install localnetwork-ecosystem                    # Install (once)
localnetwork-client --server VPS_IP:54000 &           # Connect to VPS
localnetwork-cli create minecraft --password SECRET   # Create network
# → Invite code: a3x9k2bc ← share this!

# === FRIEND ===
pip install localnetwork-ecosystem                    # Install (once)
localnetwork-client --server VPS_IP:54000 &           # Connect to VPS
localnetwork-cli join a3x9k2bc --password SECRET      # Join by invite code
# → Open Minecraft → Direct Connection → localhost:25565
```

---

## Appendix: Cheap VPS Options

Any provider works. Pick one with a public IPv4:

| Provider | Cheapest plan | Notes |
|----------|:------------:|-------|
| Hetzner Cloud | ~$4/mo | 1 vCPU, 2 GB RAM, 20 TB traffic |
| Vultr | $6/mo | 1 vCPU, 1 GB RAM |
| DigitalOcean | $6/mo | 1 vCPU, 1 GB RAM |
| Linode | $5/mo | 1 vCPU, 1 GB RAM |
| OVHcloud | ~$4/mo | 1 vCPU, 2 GB RAM |
| Oracle Cloud Free Tier | Free | 4 ARM cores, 24 GB RAM (always-free) |
| AWS Lightsail | $3.50/mo | 1 vCPU, 512 MB RAM |

The mediation server uses ~30-50 MB RAM and negligible CPU. Even the cheapest plan handles hundreds of players.

Run the server in a `screen` or `tmux` session, or use systemd:

```ini
# /etc/systemd/system/localnetwork-server.service
[Unit]
Description=LocalNetwork Mediation Server
After=network-online.target

[Service]
ExecStart=/usr/local/bin/localnetwork-server --host 0.0.0.0 --port 54000
Restart=always
User=nobody

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now localnetwork-server
```
