# Lab mini PC: robot over Ethernet, internet over Wi-Fi

**Goal.** The Windows 10 mini PC (Lenovo ThinkCentre M93p Tiny) talks to the DOBOT CR3A over an
**Ethernet cable**, and reaches the **internet over Wi-Fi** at the same time.

**Why.** Today the PC joins the robot's own Wi-Fi and loses the internet. The ROaaS lab agent needs
outbound HTTPS. Separately, the kit recommends a cable for recordings, because over Wi-Fi the controller
sometimes sends truncated packets.

**Status.** This is a plan from documentation, not tested on this robot. Values marked "verify" must be
read on the real controller.

## Facts from the documentation

- **Controller Ethernet IP:** the default for CR-series controllers is **192.168.5.1** (DobotStudio Pro
  guide; Trossen Robotics CR docs). *Verify* on the CR3A, in DobotStudio Pro, under the communication or IP
  settings.
- **Controller Wi-Fi:** SSID `Dobot_WIFI_…`, controller IP 192.168.1.6 on the CR docs. *Verify*: the kit's
  examples use other addresses.
- **Ports used by the kit:** 29999 (dashboard commands) and 30004 (real-time feedback, 125 Hz). Both are
  TCP connections made **from** the PC to the controller. Dobot's FAQ also lists 502 (Modbus), 8172
  (DobotStudio real-time data) and 30003 as reserved.
- **Different products differ.** The MG400's LAN1 and LAN2 defaults (192.168.1.6 / 192.168.2.6) are **not**
  the CR3A's.
- Nothing in the Dobot docs covers using Ethernet and Wi-Fi at the same time. The approach below is
  standard Windows networking.

## Steps

1. **Cable.** Connect the controller's LAN port to the mini PC's Ethernet port. If the controller has
   two LAN ports, use the one the CR3A manual designates for the PC.
2. **Wired adapter, Windows.** Go to *Settings → Network → Ethernet → Edit IP*, or *ncpa.cpl → Ethernet →
   IPv4 → Properties*, and set:
   - IP `192.168.5.10`
   - mask `255.255.255.0`
   - **default gateway: empty**
   - DNS: empty

   With no gateway on this adapter, Windows keeps internet traffic on Wi-Fi.
3. **Wi-Fi.** Join the network that has internet: the lab or university network, not `Dobot_WIFI_…`.
   Untick "Connect automatically" for `Dobot_WIFI_…` so that Windows does not switch back to it.
4. **Check.**
   ```powershell
   ping 192.168.5.1                       # the robot answers over Ethernet
   Test-NetConnection 192.168.5.1 -Port 29999
   Test-NetConnection 192.168.5.1 -Port 30004
   Test-NetConnection vercel.com -Port 443  # internet over Wi-Fi
   route print -4                         # the 0.0.0.0 route must point at the Wi-Fi gateway only
   ```
   If the default route still goes through Ethernet, open *IPv4 → Advanced*, untick *Automatic metric* on
   the Ethernet adapter and set the interface metric to 500.
5. **DobotStudio Pro.** Connect to the robot at `192.168.5.1`, select **TCP mode**, then disconnect
   DobotStudio Pro, as the kit's README requires.
6. **Kit.** Run a read-only check first: `python probe.py --host 192.168.5.1`. Then use `--host 192.168.5.1`
   for runs. Do not move the robot without an operator present.

## Fallbacks

- **The mini PC has only one free network port, or no Ethernet port is usable:** add a USB-Ethernet
  adapter for the robot.
- **The robot must stay on Wi-Fi:** add a USB Wi-Fi adapter, so that one Wi-Fi interface joins
  `Dobot_WIFI_…` and the other joins the internet network. The same "no gateway on the robot interface"
  rule applies. This is less robust for recordings than a cable.
- **The university Wi-Fi uses 802.1X (e.g. eduroam):** configure it once with the IT instructions. A
  captive portal would block the unattended lab agent; ask IT for a device registration if needed.

## Sources

- https://a.storyblok.com/f/298593/x/aee4a96ab1/software-user-guide.pdf (DobotStudio Pro guide: default
  controller IP 192.168.5.1, PC on the same subnet)
- https://docs.trossenrobotics.com/dobot_cr_cobots_docs/getting_started/networking.html
- https://docs.trossenrobotics.com/dobot_cr_cobots_docs/dobotscstudio/toolconfig_settings/networksetting.html
- https://www.dobot-robots.com/service/faq/459.html (CR series ports)
