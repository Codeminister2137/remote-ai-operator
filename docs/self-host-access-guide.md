# Opening the operator from your phone

This guide is for the owner of the PC. It uses everyday words.
Names you will see more than once are explained the first time.

- **Helper program**: the operator web page that runs on your PC.
- **Password**: a long secret text (32 letters and digits). Only you know it.
  The installer stores it in a file called `.env` inside the project folder.
- **Automatic start entry**: a Windows setting that starts the helper program
  every time you sign in. Its name is `RemoteAIOperatorServe`.
- **Private-network app (Tailscale)**: an app that connects only your own
  devices (your PC and your phone) so they can reach each other.
- **Phone web address**: the address you type into the phone browser to open
  the helper program. It starts with `https://`, which means the connection
  is locked (secure).
- **Project folder**: the folder with the operator files on your PC.

## What the installer did on your PC

Running `scripts\install.ps1` did the following, and nothing else:

1. Checked that Python (the language the helper program is written in) is
   new enough, and installed the helper program files into the project
   folder (`.venv` subfolder).
2. Created the password file (`.env`) with a fresh random password, if there
   was none yet. An existing password is kept and never shown on screen.
3. Offered to install the private-network app for phone access. You could
   say no; saying no does not stop the PC program from working.
4. Created the automatic start entry, so the helper program starts at every
   sign-in, and started it right away.
5. Printed the phone web address and the password steps at the end.

The installer never changes Windows itself except for that one automatic
start entry (plus the private-network app, only if you said yes).

## What to install on the phone

1. Install the **Tailscale** app from the phone app store.
2. Open it and sign in to the **same account** as on the PC.
3. Leave the app switched on (connected). No other phone app is needed;
   you open the helper program in the normal phone browser.

## How to open the operator

1. On the PC, keep the PC awake and signed in.
2. On the phone, open the browser and type the phone web address the
   installer printed (it looks like `https://something.ts.net`).
3. The page asks for the password. Type the long password and choose
   **Connect** (or **Connect and load execution mode**).
4. You can bookmark the page for next time. Never put the password itself
   into the bookmark or send it to anyone.

## If you skipped the private-network step

Without the private-network app, the helper program works only on the PC
itself (address `http://127.0.0.1:8765`, which means "this PC only").
Your phone cannot reach it, and there is no phone web address.

Any replacement must provide all of this at the same time:

- A **stable** web address: it stays the same after restarts.
- A **private** web address: only your own devices can open it, nobody
  else on the internet.
- A **secure** web address: it starts with `https://` (locked connection).
- It must **forward** to the helper program on this PC (port 8765).

Finding, checking, and keeping such a replacement working is your
responsibility. If you set one up, tell the helper program its address:

1. On the PC, open the `.env` file in the project folder with Notepad.
2. Find the line starting with `OPERATOR_PUBLIC_ORIGIN=`. If there is none,
   add it at the end. Set it to your address, for example:
   `OPERATOR_PUBLIC_ORIGIN=https://your-own-address.example.com`
3. Save the file.
4. Restart the helper: open PowerShell in the project folder and run:
   `Stop-ScheduledTask -TaskName RemoteAIOperatorServe`
   then `Start-ScheduledTask -TaskName RemoteAIOperatorServe`.

To use the private-network app after all, just re-run the installer and
say yes this time.

## If the page will not load

Try these in order:

1. **PC awake?** A sleeping PC cannot answer. Wake it and wait half a minute.
2. **Private-network app connected?** On both the PC and the phone, the app
   must show connected, with both devices signed in to the same account.
3. **Secure-address switch.** The first time ever, the private network may
   need its secure-address switch turned on in the network settings page
   (your Tailscale admin page, section "DNS", option "HTTPS"). Steps:
   https://tailscale.com/kb/1153/enabling-https
4. **Helper running?** On the PC, open PowerShell in the project folder and
   run `Get-ScheduledTask -TaskName RemoteAIOperatorServe`. The state should
   be `Running`. If not, run
   `Start-ScheduledTask -TaskName RemoteAIOperatorServe`.
5. **Read the log.** Open `.runtime\serve.log` in the project folder with
   Notepad. The last lines usually say what went wrong.
6. **Password rejected?** Passwords are exact: every letter and digit must
   match, large and small letters count as different. Retype it slowly.

## Changing the password

1. On the PC, stop the helper first. In PowerShell, in the project folder:
   `Stop-ScheduledTask -TaskName RemoteAIOperatorServe`
2. Open the `.env` file with Notepad and delete the whole line starting
   with `OPERATOR_ACCESS_TOKEN=`. Save the file.
3. Re-run the installer:
   `powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1`
   It prints a new password once. Save it.
4. The installer restarts the helper by itself. From now on, use the new
   password on the phone.

## Removing everything again

To stop the automatic start and remove the start entry (your files stay):

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1 -Uninstall
```

This keeps `.venv` (program files), `.env` (your password), and `.runtime`
(your tasks and logs) on purpose. To delete those too, remove the folders
by hand - but keep a copy of your password first if you may return.

If the private-network app serves (publishes) only this helper and you want
that gone as well, run `tailscale serve reset`. Skip this if you publish
other things through the same app.

## Good to know

- **Keep the password secret.** Anyone holding the password and the web
  address can control the helper program. Never send the password by
  e-mail or chat, and never put it into a shared file.
- **The PC must stay on.** The phone only shows the page; all work happens
  on the PC. If the PC sleeps or loses power, the phone page stops too.
- **About the secure-address switch (privacy note).** Turning on secure
  addresses publishes your PC name in a public certificate list that
  anyone can read. The helper program itself stays private - only your
  devices can open it - but the bare PC name becomes visible there. If
  the PC name is sensitive, rename the PC in the private-network app
  before switching secure addresses on.
- **Checking task results needs Docker.** If the helper page says Docker
  cannot be reached, install Docker Desktop on the PC
  (https://www.docker.com/products/docker-desktop/).
  The helper wakes it up by itself after sleep.
