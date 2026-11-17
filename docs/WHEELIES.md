# Teach your Pip to balance

A Rolling Pip can kick its nose up and balance on its two wheels. Every Pip is built a little differently, so each
one has to learn its own balance point. The first Pip learned it in about four tries.

## The easy way: ask Claude Code

1. Open Claude Code in this repo's folder on your computer, with the robot switched on and on the same network.
2. Say:

   > SSH into my robot and help me teach Pip how to do a wheelie.

That's it. Claude reads the wheelie section of the robot's manual and walks you through every step: setting up the
tilt sensor, finding your Pip's balance point, letting it take over from your hand, and practice. It tells you when to
hold the robot, when to let go, and when to tap Start on the screen.

Any coding AI that can run commands works the same way. Claude Code knows this repo best.

## Before you start

- **Battery power**, not USB: wheelies need the motors at full strength.
- **About a metre of clear floor** in front and behind, and someone ready to catch it.
- **Wheels tight** on their shafts. The kick works a loose wheel off (thread lock helps), and then the kick stops
  lifting the nose.
- **Plugs snug.** One flip once reset the whole robot.
- **Motors unlocked** in the app (they stay locked until the owner unlocks them).

## What happens

1. **Set up the tilt sensor** (once, and again whenever it is moved). Put the robot flat and still, then lift the
   front and hold it. It learns which way is level and which way is forward.
2. **Find the balance point.** Hold the robot on its two wheels and tilt it nose up until it feels weightless: it
   wants to fall neither forward nor back. On the first Pip that is about **52 degrees**. Yours will be different: it
   depends on where the battery and the heavy parts sit.
3. **Let it take over.** Tap Start and hold it lightly at the balance point. When it is within 4 degrees it takes
   over. Let go gently.
4. **Kick up by itself.** Settings > Robot > **Wheelie: Do it**, the **Wheelie!** button in the web app, or just say
   "do a wheelie".
5. **Practice.** Settings > Robot > **Wheelie practice**, or say "practise your wheelies". Five tries by itself: in
   each one it changes one setting a little, keeps what made it steadier, and drives back to where it started. Every
   try is saved, so it keeps getting better each time you run it.

## If it does not work

| What happens | Try |
|---|---|
| The nose barely lifts | Check the wheels are tight. A weak battery also gives a weak kick. |
| It flips over backwards | The kick is too long or too strong for your build. Ask your AI helper to shorten it. |
| It runs away forward and falls | The balance point is off: find it again. |
| It rocks fast | Run practice again: it tunes this by itself. |

Tell Claude what happened. It can read the recording of every try and change the settings for you.

<details>
<summary><b>By hand: the commands behind each step</b></summary>

These are `POST /api/wheelie` requests with the owner's API token. The details (every setting, how practice scores each
try) are in [`knowledge/manual/motors.md`](../knowledge/manual/motors.md), under "Wheelies".

| Step | Command |
|---|---|
| Set up the tilt sensor | `{"cmd":"setup"}` (on-screen steps) |
| Record while you hold it at the balance point | `{"cmd":"record","secs":8}` |
| Save your balance angle | `{"cmd":"set","tgt":52}` |
| Take over from your hand | `{"cmd":"arm"}` |
| One wheelie with the saved settings | `{"cmd":"show"}` |
| Practice | `{"cmd":"practice","n":5,"secs":20}` |
| Stop | `{"cmd":"stop"}` |

`GET /api/wheelie` lists every setting with a line of help. If it flips over backwards, lower `k2ms` (kick length) or
`cut` (the angle where the kick stops). If it runs away forward, check `kv` and `kx` are not 0.

</details>
