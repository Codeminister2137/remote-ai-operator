# Recording the demo video with OBS (beginner guide)

You record on your PC. OBS captures your screen and your voice while you
click through the demo plan. No editing is needed if the take is clean.

## 1. One-time OBS setup (10 minutes)

1. Install OBS Studio from obsproject.com (Windows installer, defaults).
2. Open OBS. If the auto-configuration wizard appears, choose
   "Optimize for recording" and accept the defaults.
3. Menu Settings → Output. Set Output Mode to "Simple" (top dropdown).
   - Recording Quality: "High Quality, Medium File Size".
   - Recording Format: "MP4".
   - Recording Path: a folder you can find (e.g. `Videos`).
4. Menu Settings → Video. Base and Output resolution `1920x1080`,
   Common FPS Values `30`.
5. Menu Settings → Audio. Set one Desktop Audio device to "Disabled"
   (avoids double sound) and your Mic/Auxiliary device to your headset
   or microphone. Close Settings with OK.
6. In the main window, "Sources" box at the bottom, click `+`:
   - Add "Display Capture", name it `Screen`, OK. Pick the monitor
     with your browser. OK. (Display Capture is more forgiving than
     Window Capture: dialogs and tooltips stay visible.)
   - Click `+` again, add "Audio Input Capture", name it `Mic`, OK,
     select your microphone. OK.
7. Speak normally. The `Mic` row in "Audio Mixer" (bottom middle) must
   bounce green/yellow. If it hits red, drag its volume slider down.
   If it never moves, the wrong mic is selected (step 6 again).

## 2. Test recording (2 minutes, do not skip)

1. Click "Start Recording" (bottom right). Record 10 seconds: move the
   mouse, say "test one two".
2. Click "Stop Recording". Menu File → "Show Recordings" opens the file.
3. Play it: picture sharp, mouse visible, voice clearly audible.
   If anything is off, fix it now (mic choice and mixer level cover 90%).

## 3. Recording the real take

1. Close or silence everything noisy: chat apps, mail popups, phone on
   silent. Turn on Windows Do Not Disturb (Win+N → bell icon).
2. Open the demo in your browser (local or hosted per the shot list)
   and put this guide + the video script where you can read them
   (second monitor, printed page, or phone).
3. In OBS click "Start Recording", wait 3 silent seconds (gives a clean
   head for trimming), then start talking and clicking through
   `docs/video-plan.md` shot by shot.
4. Talk through what you do ("I approve the plan, and the worker
   starts..."). Pauses are fine; silence is better than "um".
5. Small slip? Pause 2 seconds, redo the sentence, keep going — one
   clean take beats five restarts. Only restart for a real breakage
   (app error, wrong screen).
6. At the end, hold the final screen 3 seconds, then "Stop Recording".

## 4. Phone cameo shot (optional, only if time allows)

The script may call for one "from my phone" moment. Easiest version:
join the demo URL on your phone, hold the phone where your webcam (or a
second OBS "Video Capture Device" source) can see it, and tap through
one action while narrating. Do not attempt phone screen-recording plus
video splicing today — no editing toolchain is set up for it.

## 5. After recording

1. File → "Show Recordings". Play the whole take once: every shot in
   the script visible, voiceover audible throughout, no password or
   private tab visible at any point.
2. Retake budget: 2 takes, then ship the better one. A small live
   wobble reads as authenticity; only re-record for breakage or
   inaudible audio.
3. Keep the file path handy — submission uploads this exact file.
