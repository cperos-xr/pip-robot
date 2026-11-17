# Running a Pip workshop

A plan for a two-hour workshop where kids (with a grown-up) build a **Desk Pip** and bring it to life. No
soldering and no batteries: everything runs from USB power. It works for adults too.

<!-- photo: a workshop table with three stations, kids plugging in a webcam, a Pip face on screen. -->

## Who it is for

- Ages 10 and up with a grown-up at each station, or 13 and up on their own.
- No coding or electronics experience needed.
- 2 to 3 people per station.

## What each station needs

| Part | Notes |
|---|---|
| Brain | Raspberry Pi Zero 2 W or newer, or an Orange Pi Zero. Pre-installed SD card (see Before the day). |
| Screen | CYD touchscreen (ESP32-2432S028R), firmware already flashed |
| Eyes and ears | Any USB webcam with a microphone |
| Voice | A small USB or Bluetooth speaker |
| USB hub | A powered hub avoids most problems |
| Power | 5 V 3 A USB supply |
| Cables | The right USB adapters for your brain (the Pi Zero needs micro-USB OTG adapters) |
| Mounting | A few printed stud plates and LEGO bricks (optional, but kids love this part) |

Check current prices before you buy. The CYD screen is about $15.

## Before the day (the host does this)

Installing on a small board takes a while. Do it at home so the workshop is about building, not waiting.

1. **Install every SD card ahead of time.** Run the installer once per brain (`/make-it-alive` or
   `tools/install.sh`, see [MAKE_IT_ALIVE.md](MAKE_IT_ALIVE.md)), then test it with the parts it will use.
2. **Give each brain its own name:** `pip1`, `pip2`, `pip3`. Several robots all called `pip` on one network get
   confused. Write the name on a sticker on each board.
3. **Use one AI key for the whole workshop**, with a spending limit set on the provider's site. Never hand out the
   key itself.
4. **Wi-Fi:** bring a 2.4 GHz network (a travel router or phone hotspot). The Orange Pi Zero cannot use 5 GHz. Put
   it in each card before the day.
5. **Turn off sharing:** leave Bluesky posting and email off (they are off unless the owner turns them on).
6. **Flash the screens** with the Pip firmware and check each one shows the face.
7. **Print a one-page sheet** per station: the robot's name, its web address (`http://pip1.local:8080`) and the
   plan below.

## The two hours

| Time | What happens |
|---|---|
| 0:00 | **Meet Pip.** Show a finished Pip. It says hello, plays a game, and answers a kid's question. |
| 0:10 | **What is a robot?** Brain, eyes, ears, voice, face. Hand each station its parts and let them match each part to its job. |
| 0:25 | **Plug it together.** Screen, webcam, speaker and hub into the brain. Mount boards on stud plates if you have them. |
| 0:50 | **Make it alive.** Power on. The face appears, Pip finds its parts and says its first words. |
| 1:05 | **Talk to it.** Ask it questions, have it look at things and describe them, give it a name. |
| 1:25 | **Play with RoboOS.** Open the desktop on the screen, draw in Paint, install an app from the App Store, play a game. |
| 1:45 | **Show and tell.** Each station shows one thing their Pip did. |
| 1:55 | **Take it home** (if the parts go home): the sheet has the web address and a link to the Discord. |

### For older or faster groups

- Ask Pip (in the web app) to **write a new RoboOS app**, such as a timer or a quiz, and watch it appear on the screen.
- Look at the app's code together. It is one short Python file.

## Safety and privacy

- **No lithium batteries** at kids' workshops. USB power only.
- **Motors stay locked** by default. Leave wheels for a later session with an adult at each station.
- **Camera and microphone:** tell parents what Pip records. The camera has a privacy switch on the screen. Do not keep
  photos of kids, and get a parent's OK before posting any workshop photos.
- **Voice:** speech goes to a cloud speech-to-text service by default. Switch to the offline model (Vosk) if you
  prefer nothing leaves the room.
- **Discord** is for ages 13 and up. Younger builders can join through a parent's account.

## After the workshop

- Post photos (with permission) in #show-your-build on Discord.
- Note every step that went wrong and fix it in these docs. The next host will thank you.
