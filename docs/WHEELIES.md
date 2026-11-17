# Teach your Pip to balance

A Rolling Pip can kick its nose up and balance on its two wheels. Every Pip is built a little differently, so each
one has to learn its own balance point. The first Pip learned it in about four tries.

The full details (the settings, how the tuner scores each try) are in
[`knowledge/manual/motors.md`](../knowledge/manual/motors.md), under "Wheelies".

## Before you start

- **Battery power**, not USB: wheelies need the motors at full strength.
- **About a metre of clear floor** in front and behind, and someone ready to catch it.
- **Wheels tight** on their shafts. The kick works a loose wheel off (thread lock helps), and then the kick stops
  lifting the nose.
- **Plugs snug.** One flip once reset the whole robot.
- **Motors unlocked** in the app (they stay locked until the owner unlocks them).

The steps below use the robot's wheelie commands. Your AI helper can send them for you: in Claude Code, ask for
example "run the wheelie setup on my robot". By hand, they are `POST /api/wheelie` with the owner's API token.

## 1. Set up the tilt sensor (once, and again whenever it is moved)

Send `{"cmd":"setup"}`. The screen walks you through it:

1. Put the robot flat and still. It learns what level is.
2. Lift the **front** up and hold it. It learns which way is forward.

## 2. Find your balance point

Hold the robot on its two wheels and tilt it nose up until it feels weightless: it wants to fall neither forward nor
back. That angle is the balance point. On the first Pip it is about **52 degrees** (it settled at 55.5 once it was
balancing by itself). Yours will be different: it depends on where the battery and the heavy parts sit.

To measure it, send `{"cmd":"record","secs":8}` and hold the robot at the balance point while it records. Ask your
AI helper to read the angle from the recording, then save it:

```json
{"cmd": "set", "tgt": 52}
```

## 3. Let it take over from your hand

Send `{"cmd":"arm"}` and tap **Start** on the screen. Hold the robot lightly on its wheels at the balance point. When
it is within 4 degrees it takes over and balances. Let go gently.

## 4. Kick up by itself

Settings > Robot > **Wheelie: Do it**, the **Wheelie!** button in the web app, or just say "do a wheelie". It kicks
backwards, then forwards, catches itself and balances.

## 5. Practice

Settings > Robot > **Wheelie practice** (or say "practise your wheelies"). One Start tap, then five tries by itself.
In each try it changes one setting a little, keeps what made it steadier, and drives back to where it started. It
stops after two failed kicks in a row, if it falls over, or when the battery is low. Run it again whenever you like:
every try is saved, so it keeps learning.

## If it does not work

| What happens | Try |
|---|---|
| The nose barely lifts | Check the wheels are tight. A weak battery also gives a weak kick. |
| It flips over backwards | The kick is too long or too strong for your build: lower `k2ms` or `cut`. |
| It runs away forward and falls | The balance point is off: measure `tgt` again. Also check `kv` and `kx` are not 0. |
| It rocks fast | Let practice tune it, or lower `kd` a little. |

Ask your AI helper to explain any setting: `GET /api/wheelie` lists every one with a line of help.
