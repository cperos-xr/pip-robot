# Security

This robot has a camera, a microphone and wheels, and it lives in someone's home. Please treat problems accordingly.

## Reporting a problem

Do not open a public issue for something that could let a stranger into someone's robot. Use GitHub's private
reporting instead: the repository's **Security** tab > **Report a vulnerability**. You will get an answer.

## How the robot is protected (so you can check it, and break it)

Nothing here depends on the code being secret.

| Thing | Protection |
|---|---|
| Web app and API (port 8080) | Every request needs an owner or guest session, or the API token. Only `/login`, `/pair`, `/public*` and the offline page are open. Guests can look; only the owner can act. |
| Owner password | PBKDF2-SHA256, 200,000 rounds, random salt (`data/web_auth.json`, mode 600). |
| Sessions | Cookie signed with a random key made on each robot; `HttpOnly`, `SameSite=Lax`, `Secure` over https. "Sign everyone out" bumps a generation number. |
| Pairing | A 6-character code shown on the robot's own screen, valid 5 minutes, one use. Guest invites are links with a long random token, valid 24 hours, one use. |
| Guessing | Wrong passwords and pairing codes are counted for each visitor address: 5 free, then a wait that doubles from 30 seconds to an hour. Password checks are also capped for all visitors together. |
| API token | 96 random bits in `data/api_token` (mode 600), for scripts, MCP and RoboDev. It can never send email. |
| Secrets | Only in `data/` (never committed). The file browser, RoboDev and the AI refuse to open them. |
| Camera | Privacy switch on the screen; the public page shows the camera only if the owner turns that on. |
| Motors | Locked until the owner unlocks them; the motor board stops after 0.5 s without commands. |
| Firmware | Flashing asks on the touchscreen and backs up the old image. The robot only downloads builds made from its own repo's default branch, never from a pull request. |
| Self-modification | RoboDev's risky steps (packages, system files, deleting, flashing, motors, public posts) wait for Allow. A broken restart rolls back. |

## Known limits: read these before you put a robot on the internet

- **Prompt injection is not solved.** The AI reads text other people wrote: web pages, email, replies to its posts.
  Such text can carry hidden instructions. The robot marks it as untrusted, and after reading any of it the two
  dangerous follow-ups wait for a tap on the screen (starting a RoboDev job, publishing a post). Other tools stay
  available: an injected instruction could still make the robot say something odd, save a wrong memory, or write an
  email draft (which you would see before sending). Files you upload yourself are trusted.
- **RoboDev is a root shell with manners.** Its permission checks are pattern lists, a seat belt and not a wall. In
  `full` mode nothing asks. Do not give RoboDev jobs you would not give a person with root on that machine.
- **Anyone in the room can talk to it.** Voice has no speaker check. Jobs handed over by voice are capped at RoboDev's
  `voice_max_mode`, and security mode cannot be switched off by voice.
- **What leaves the robot.** While it listens, the recording goes to a cloud speech-to-text model (set `voice.stt` to
  `local` to keep audio on the robot, with worse recognition). When it looks, the photo goes to the AI model you chose.
  Conversations go to that model too. The camera's privacy switch stops the photos.
- **Guests see a lot:** the live camera, photos, the feed of what was heard and said, memories. Invite people you trust.
- **`http://` on the home network is not encrypted.** Anyone on your Wi-Fi can read the traffic. Use the https tunnel
  address when that matters.
- **robotd runs as root** on a single-purpose board. If the web sign-in is ever bypassed, the board is lost.

## If you expose your robot to the internet

1. Set a long owner password (the web app refuses fewer than 8 characters; use a passphrase).
2. Put the tunnel behind an access gate, for example Cloudflare Access with your own email as the only allowed
   identity. Then strangers never reach the robot's sign-in page at all. Allow `/public*` separately if you want the
   public page.
3. Do not publish your robot's address, its inbox address, or your home network details. Keep them in `data/manual/`.
4. Use SSH keys, and turn SSH password sign-in off (`PasswordAuthentication no`).
5. Keep `agent.confirm_after_untrusted` on, and leave RoboDev in `auto`, not `full`.
