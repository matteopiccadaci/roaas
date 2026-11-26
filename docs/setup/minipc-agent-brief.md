# Brief for the coding agent on the lab mini PC (Windows 10)

You are helping a PhD student prepare the lab's robot PC for ROaaS. ROaaS sells recorded robot runs
through Crow, on a **test network with test tokens worth nothing**.

Today's goal:
- check the PC (read-only);
- support the Crow pilot from Rafa's setup page;
- install the ROaaS lab agent and rehearse it **on the simulator only**.

**The real robot never moves in this brief.**

## The lab, as it is

| Item | Value |
|---|---|
| Robot | DOBOT CR3A, controller firmware 4.5.1.1 |
| Robot link | Controller LAN1 `192.168.5.1`, over an Ethernet cable |
| This PC | Ethernet `192.168.5.100/24` (no gateway); internet over Wi-Fi |
| Python | 3.14.x |
| DobotStudio Pro | Installed. It is used only to select TCP mode, then **closed** |
| Lab kit | v0.4.5, in a folder that holds `run_order.py`, `keys\recorder_key.pem` and `robot_info_cr3a.json`. Below it is called `<KIT>` (probably `C:\dobot_kit`; find the real path) |
| Expected key fingerprint | `0e811c503e02aa59faa288365e0f80023603ee9cfcd64f3ac4e67200960289af` |

## Hard rules

- **Never move the robot.** Never run anything with `--live`: not `run_order.py`, not `roaas run`, not
  `roaas agent`. Never send `EnableRobot()`, `MovJ`, `SpeedFactor` or any other command except the read-only
  ones below. Never type `ENABLE`, and never send keystrokes to a window that shows an ENABLE prompt: only
  the person at the robot does that.
- **Do not modify `<KIT>`.** Do not create, edit or delete any file in it. Crow's pilot depends on it as it
  is.
- **Never print, copy, paste or send** `keys\recorder_key.pem`, the serial salt, the serial numbers in
  `robot_info_cr3a.json`, or any token. Do not write tokens to files.
- **Ask the person before installing anything** (Node.js, Python packages, Git), and at every **STOP**.
- **If an output differs from "Expect",** stop, show it to the person, and do not work around it.

## Part 1: read-only checks (PowerShell)

1. **Tools.** Run `python --version` and `node --version`.
   - Expect Python 3.9 or later.
   - Crow's pilot needs Node.js **22.18 or later**. If it is missing or older: **STOP**, and ask the person
     before installing Node.js LTS from nodejs.org.
2. **The kit.** Find `<KIT>`. Confirm with `Test-Path` that `<KIT>\keys\recorder_key.pem` and
   `<KIT>\robot_info_cr3a.json` exist. Do not open either file.
   - **Only if the key file exists,** print its public fingerprint with the kit's own code. Use the kit's
     Python environment if it has one (`<KIT>\.venv\Scripts\python.exe`). If the key file were missing, this
     code would create a new key in the kit, so never run it then.

     ```powershell
     python -c "import sys; sys.path.insert(0, r'<KIT>'); import signers; print(signers.fingerprint(signers.make_signer('software', r'<KIT>\keys').public_key()))"
     ```

   - Expect `0e811c50…289af`, the full value in the table above. Any other value: **STOP**.
3. **Network.** Run the checks below.
   - `Test-NetConnection 192.168.5.1 -Port 29999` and `-Port 30004`: expect `TcpTestSucceeded : True`.
   - `Test-NetConnection crow.direct -Port 443` and `Test-NetConnection workers.cloudflare.com -Port 443`:
     expect `True` (internet over Wi-Fi).
   - `route print -4`: the `0.0.0.0` route must go through the Wi-Fi gateway, not `192.168.5.x`.
4. **Robot state (read-only).**
   - From `<KIT>`, run `python probe.py --host 192.168.5.1`. It sends only `RobotMode()`, `GetErrorID()` and
     `GetAngle()`.
   - Expect no alarm, and the robot disabled (mode 4).
   - If the dashboard does not answer, DobotStudio Pro is probably connected or not in TCP mode: tell the
     person.

**STOP:** report the four results to the person in a short table.

## Part 2: Crow's pilot (only if the person gives you Rafa's brief)

Rafa's setup page has its own brief for this PC. Follow it exactly, with its own STOPs and rules. These lab
facts apply to it:

- **`kit_dir`:** `<KIT>`.
- **`robot_host`:** **`192.168.5.1`**, not the Wi-Fi example `192.168.201.1`. The deal's checks reject a run
  recorded against a local or simulator address.
- **Signer:** `signer: "software"`. The fingerprint must be the one above.
- **Timing:** do the preparation and the simulator safety check (Rafa's steps 1 to 7) whenever the person
  asks. Do run-day steps 8 and later only when the person says that Rafa gave the go.

## Part 3: install the ROaaS lab agent and rehearse on the simulator

1. **Get the repository into `C:\roaas`,** asking the person which way:
   - `git clone https://github.com/matteopiccadaci/roaas C:\roaas`. The repository is public: no login
     needed.
   - Or unzip the "Download ZIP" of that repository, which the person provides, into `C:\roaas`.

   Expect `C:\roaas\provider\pyproject.toml` and `C:\roaas\kits\dobot_kit\fake_dobot.py`.
2. **Environment** (ask before installing):

   ```powershell
   cd C:\roaas\provider
   python -m venv .venv
   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
   .venv\Scripts\Activate.ps1
   pip install -e ".[agent,test]"
   ```

3. **Tests.** Run `pytest -q`.
   - They start the kit's fake controller on `127.0.0.1` only; the real robot is not involved.
   - Expect `… passed, … skipped` and no `failed`. The cloud and Crow tests are skipped here.
   - If anything fails: **STOP** and show the failure. This is the first run of the suite on Windows.
4. **Robot status as ROaaS sees it (read-only).** Run `roaas probe --host 192.168.5.1`.
   - Expect `"ready": true`: dashboard answering, mode 4 (disabled), no alarm.
   - `"ready": false` with `RobotMode 5` means the robot was left enabled: tell the person.
5. **Rehearsal on the fake robot.** Use two PowerShell windows, both with the virtual environment active.
   - **Window A:** `python C:\roaas\kits\dobot_kit\fake_dobot.py` (leave it running).
   - **Window B:**

     ```powershell
     mkdir C:\roaas\rehearsal -Force; cd C:\roaas\rehearsal
     roaas run C:\roaas\kits\dobot_kit\example_order.json --robot-info C:\roaas\kits\dobot_kit\lab_limits_cr3a.json --crow-delivery --telemetry-url https://example.invalid/t/rehearsal.enc
     $doc = (Get-ChildItem deliveries\*.roaas-episode.json).FullName
     $enc = (Get-ChildItem deliveries\*.telemetry.enc).FullName
     roaas verify-delivery $doc --order C:\roaas\kits\dobot_kit\example_order.json --telemetry $enc
     ```

   - Expect the last line `RESULT: delivery VALID`.
   - Without `--live` this command accepts only a robot on `127.0.0.1`.
   - It creates a **test** key in `C:\roaas\rehearsal\keys`, which is not the lab key and must never be
     used for deals.
   - Stop window A with Ctrl+C.

**STOP:** report the results. **Do not run `roaas agent` yet.** It needs the cloud address and the lab token,
which the person will provide after the Cloudflare deploy.

## Part 4: later, when the person provides the cloud URL and the lab token

These commands contain secrets, and the last one `--live`, so **the person runs them, not you**. You may prepare
the command lines with the real `<KIT>` path and the URL, and check the output with them. The agent reads each
deal's terms from Crow's operators: `Test-NetConnection api.crow.direct -Port 443` must succeed.

**4a. First with the fake robot (rehearsal deals only).** Window A: `python C:\roaas\kits\dobot_kit\fake_dobot.py`.
Window B:

```powershell
Set-PSReadLineOption -HistorySaveStyle SaveNothing
$env:ROAAS_LAB_TOKEN = "<the person types it>"
roaas agent --cloud <URL> --robot-info C:\roaas\kits\dobot_kit\lab_limits_cr3a.json --keys-dir <KIT>\keys --out C:\roaas\rehearsal\episodes
```

- **Expect** `connected to wss://…`. In a third window, with the same `$env:ROAAS_LAB_TOKEN`, run
  `roaas ops status --cloud <URL>`: agent online, robot ready.
- Without `--live` the agent drives only `127.0.0.1` and declines any deal with production terms.

**4b. Then the real robot, off duty** (Ctrl+C the rehearsal agent and the fake robot first):

```powershell
$env:DOBOT_SERIAL_SALT = "<the person types it>"
roaas agent --cloud <URL> --robot-info <KIT>\robot_info_cr3a.json --host 192.168.5.1 --live --off-duty --keys-dir <KIT>\keys --out C:\roaas\episodes
```

- **Expect** `connected to wss://…`, and in `roaas ops status`: agent online, robot ready, `on duty: False`.
- **Hand both windows to the person.** From here the operator follows `C:\roaas\docs\OPERATOR.md`; only the
  person runs `roaas ops open` and types `ENABLE <code>`.
- `--keys-dir <KIT>\keys` **reads** the lab key so that the fingerprint stays the same. The agent writes
  nothing in `<KIT>`.
