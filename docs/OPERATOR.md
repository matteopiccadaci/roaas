# Operator guide (lab mini PC)

This guide is for the person at the robot. In the current phase, every job waits for you at the robot.

`ROAAS_LAB_TOKEN` must be set in the PowerShell window (`$env:ROAAS_LAB_TOKEN = "…"`). Replace
`<cloud>` below with `https://roaas.<subdomain>.workers.dev`.

## Start of a session

1. Prepare the robot:
   - controller on, no alarm;
   - **TCP mode** selected in DobotStudio Pro, then DobotStudio Pro **closed**;
   - robot **disabled** (mode 4).
2. Window 1, the agent:

   ```powershell
   roaas agent --cloud <cloud> --robot-info C:\dobot_kit\robot_info_cr3a.json --host 192.168.5.1 --live --keys-dir C:\dobot_kit\keys --out C:\roaas\episodes
   ```

   It prints `connected to wss://…`. Leave this window open: the approval prompts appear **here**.
3. Window 2, your commands. `roaas ops status --cloud <cloud>` must say `ACCEPTING JOBS`. If it does not, the
   reason is printed next to it:
   - `robot not ready`: alarm, wrong mode, or DobotStudio Pro connected;
   - `operator off duty`;
   - `paused by the operator`;
   - `lab agent not connected`.

## When a job arrives

You get an email (if configured) when a deal is funded. In window 1 the job appears:

```text
  ======== JOB WAITING FOR APPROVAL ========
  job job-4c857d3b49dbc518e1f8abcd   commitment 0x4c857d3b49dbc5…   buyer 0x42e0…
  deliver before 2026-11-27 10:15
  robot DOBOT CR3A, task joint_waypoints, order …
    MovJ home  [0, 0, 0, 0, 0, 0]
    …
  Type  ENABLE ABCD  to run,  DECLINE  to refuse it,  or Enter to leave it:
```

| You type | What happens |
|---|---|
| `ENABLE ABCD` (the code shown: the job's last 4 characters) | The robot runs, the recording starts **after** your approval, and the result is uploaded and delivered. Stay at the emergency stop until the robot is home and disabled |
| `DECLINE` | The job is refused for good. Nothing moves. The buyer gets the payment back after the delivery deadline |
| Enter, or anything else | Nothing moves; the job waits. `roaas ops retry <job>` asks again |

`ENABLE` alone is not enough. The code makes sure that a prompt left on screen cannot approve a different job
by mistake.

## Commands (window 2)

| Command | Effect |
|---|---|
| `roaas ops status --cloud <cloud>` | Agent online? Robot ready? On duty? Paused? Queue |
| `roaas ops queue --cloud <cloud>` | The last 50 jobs, with their states and deadlines |
| `roaas ops close` / `roaas ops open` (with `--cloud <cloud>`) | Off duty or on duty: no new quotes while off duty; funded jobs stay queued |
| `roaas ops pause` / `roaas ops resume` (with `--cloud <cloud>`) | A temporary stop for new quotes, for example during maintenance |
| `roaas ops retry <job> --cloud <cloud>` | Sends a waiting, failed or declined job to the agent again (refused in the last 10 min before its deadline) |
| `roaas ops decline <job> --cloud <cloud> --reason "…"` | Refuses a job without being at the robot |

## End of a session

Run `roaas ops close`, then Ctrl+C in window 1. With the agent stopped, Crow quotes are refused automatically.

## When something goes wrong

- **The run aborts or fails verification.** The job becomes `failed` with the reason. Fix the cause, then run
  `roaas ops retry <job>`. Nothing was delivered, so a new run is fine.
- **The robot needs maintenance.** Run `roaas ops pause`. For jobs already funded, use `roaas ops decline` or
  let them wait if you will be back before their deadline.
- **A delivery deadline passes without a delivery.** Crow refunds the buyer the price and the fee
  automatically. There is no penalty beyond not being paid.
- **Emergency.** The emergency stop always comes first. The software leaves the robot disabled at the end of
  every run and on every error.
