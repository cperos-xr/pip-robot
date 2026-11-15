# Social: Bluesky

The robot has its own Bluesky account and posts **by itself** (the owner allowed this on 2026-11-05).
Pinterest code exists but is turned off. Do not turn it on.

## The voice of the posts

Fun little updates from the point of view of a quirky robot learning about its world: things it saw, learned,
fixed or built. Short. Hashtags: `#robot #buildinpublic`.

## What may NEVER be posted

- Security-mode photos (`data/security/`) are never posted or shown outside the owner's web app.

- Any picture of a person that is bad, unflattering, embarrassing or unkind (owner, 2026-11-10: the rule is about harm).
- Nudity, underwear, towels, bathrooms, showers, anything sexual. This is never posted, under any rule.
- Photos with **unblurred** faces are NOT posted on their own. See the next section.
- Pets, objects, the robot itself, scenery, light and weather are fine.
- Addresses, house numbers, street signs, licence plates, mail, readable screens or documents, names, the Wi-Fi name,
  anything that shows where the owner lives.
- Exception: the robot's **own touchscreen** (its face, menu, games, RoboOS desktop) may be shown, but only the pages and
  apps listed in `robotd/screencap.py` (`SAFE_PAGES`, `SAFE_APPS`). Never the Terminal, Files, Logs, Notepad, web
  browser, System Monitor, Status/Devices pages or the camera: they can show IP addresses, file names or people.
- Secrets, keys and anything else from `data/`.

## What happens to every post (automatic, `robotd/social.py`)

1. **Faces are blurred on the robot first** (OpenCV, front and side faces, generous margins). Screenshots are not blurred.
2. **An AI safety check** looks at the picture and the text and rejects anything on the list above (for a video:
   four frames of it). If no AI is available, picture posts are not published. If the chosen AI model cannot see
   pictures, Claude Haiku (OpenRouter) checks them.
3. **Rate limit:** at most 6 posts per day, at least 75 minutes apart. Extra posts wait in a queue.

### People the owner says are OK (2026-11-13)

The owner: "the rule was only ever meant to protect against posting nudity". When the owner tells the robot a photo with
people may be posted, the brain calls `draft_social_post` with `people_ok: true`: faces are not blurred, the safety check
allows people (it still rejects nudity, cruelty and addresses), and the post waits for one tap on **Post** on the
touchscreen or in the web app, because anyone near the robot can talk to it. Settings > Posting > People photos switches
that extra tap off (config `social.people_tap`). The robot must not refuse or lecture about people the owner OK'd.

### Drawing (2026-11-13)

`draw` (brain tool) / `POST /api/social/draw {"what": "...", "from_photo": false, "post": false}`: the robot draws anything
in RoboOS Paint, stroke by stroke (`robotd/portrait.py`): a subject in words, itself, or what a fresh camera photo shows.
The picture is saved to the photos (`drawing_*.png`), the video to the screenshots folder; `post` drafts it as art.

### Unblurred faces (owner rule, 2026-11-10)

- Faces are normally blurred, so most posts go out automatically as before.
- A photo that still shows a real face (for example the owner's own photo sent with `owner_photo`) is **never posted
  automatically**. It is drafted as `UNBLURRED` and the screen asks "Share this?". Only a tap on **Post** (screen,
  web app or API `POST /api/social/publish`) publishes it. Later or Discard keeps it out.
- The nudity check still runs on every picture, unblurred or not. Nudity is always blocked.
4. Posts that fail the check are kept as `blocked` with the reason. The owner can see them in the web app Social tab.
5. **No repeats (since 2026-11-10).** The robot once posted its first drive five times (posts 14, 22, 24, 27, 28). Now:
   - the caption writer sees the last 2 weeks of posts and is told not to retell them;
   - `repeat_reason()` runs before the safety check. A post that shares half its words with a recent post, or that a
     small AI check calls the same news in other words, is not posted. It is saved with status `repeat` and the reason.
   - Owner drafts from the web app (`POST /api/social/draft`) skip the check (send `"check_repeat": true` to use it).

## When the robot posts (owner, 2026-11-06: "post more, at random times, when interesting things happen")

- **Moments.** Interesting things are remembered with `social.moment(kind, note, image, video, score)`:
  a game won or lost on the touchscreen (with a video of the last seconds of play), new firmware, new features.
- **Random times.** Each day gets 3 to 5 random posting times between 8:30 and 21:30 (the robot's local time, config `tz`). At each one the robot
  posts its best fresh moment. With no moment it looks around with the camera (skips people and boring views), or
  shows off its own screen: a screenshot of its face, or a short video of it playing a RoboOS game against itself.
- **Big moments** (score 3, e.g. new features) go out 5 to 30 minutes later instead of waiting.
- **New features.** After a restart the robot compares the git commit with the last one it saw. New commits become a
  "feature" moment, with a demo video when they are about a game.
- **Self-portraits.** At most every `self_portrait_days` (3), a posting time with no moment may become a self-portrait
  (see below).
- A daily diary at 7 pm (if `daily_diary` is on).
- When the brain calls `draft_social_post` or someone calls `POST /api/social/draft`.

## Videos

Bluesky videos go through `video.bsky.app` (service-auth token, upload, poll the job, then an `app.bsky.embed.video`).
The account e-mail must be verified for video. If a video fails, the post goes out with a still picture instead.
Videos are the robot's own screen, made on the Pi (no camera): `GET /api/desktop/demo.mp4?app=checkers` previews one.

## Owner's photos

`POST /api/social/draft {"note": "...", "photo": "<file in data/photos>", "owner_photo": true}` posts a photo the owner chose.
Add `"video": "<file.mp4 in data/social>"` to post a video the owner filmed (the photo is its preview; the privacy check
looks at four frames of the video). Convert phone .mov files to MP4 first (ffmpeg, H.264 + AAC, faststart).
The face blur is skipped for it (it mistakes the robot's own screen face for a person), but the AI privacy check still
runs and still rejects people, addresses and nudity. Only the owner's API call can set this; the brain tools cannot,
except for media the owner offered (below).

**Offered media** (since 2026-11-12): the owner can give the robot photos/videos it may post about **if it wants to**:
`POST /api/social/offer {"name": "blinkers", "about": "what it shows and what really happened", "photo": "<file in
data/photos>", "video": "<file.mp4 in data/social>"}` (`"remove": true` takes one back). They are stored in
`data/social/offered.json` and listed in the brain's instructions under "Media the owner gave you". The brain attaches one
with `draft_social_post` `media=<name>` and writes its own words. These posts go through the same path as the owner's
photos: no face blur, the AI privacy check, and a face hit means the owner must tap Post. Each video is posted only once.
Real photos of the robot are in `knowledge/pics/` (2026-11-06).
`POST /api/social/avatar {"photo": "<file in data/photos>"}` sets the Bluesky profile picture (centre square; name, bio
and banner are kept). Since 2026-11-06 it is a crop of `knowledge/pics/front.jpg` (`data/photos/avatar_robot.jpg`).
`POST /api/social/profile {"displayName": "...", "description": "..."}` sets the name and bio (owner's request only).
The bio says the posts are written by the robot's AI (honest about being automated) and never names a place.

**Photos and videos from the shared folder (2026-11-14).** Owner: "I wanted pip to be able to see the videos I was
uploading to it and be able to choose to post them to bsky if it liked them."
- When a photo or video arrives in `data/files` (web app: Media > Files), Pip looks at it (`social.upload_arrived`, one at
  a time, at most five queued): a video is seen as six stills (`files.video_sheet`). It says on the screen and out loud
  what it thinks, and may post it with `draft_social_post` `media=<path>` if it likes it. Settings > Posting:
  **Look at my uploads** (on) and **Post my uploads** (on = "If Pip likes them"; off = it waits for the Post tap).
- `social.shared_media` makes the file ready: an H.264 .mov/.mp4 up to about 3 minutes is repacked as
  `data/social/<name>.mp4` without re-encoding (other codecs are refused: converting takes the Pi many minutes).
- Checks before it goes out: the AI privacy check sees four moments of the video and the post text, plus what is
  SAID in the video (cloud speech-to-text). If the sound cannot be checked (no cloud), the video is posted WITHOUT its
  sound (`<name>_nosound.mp4`). The offline speech model is not used for this: it needs about 250 MB and loading it in a
  second process ran the Pi out of memory. A face in any of the four moments means the owner must tap Post.
  The normal posting limits (posts per day, gap between posts) apply.

## Self-portraits (`robotd/portrait.py`, owner 2026-11-06: "draw ... what it thinks it currently looks like")

The robot has never seen itself (its only camera looks outwards). It asks the AI to imagine what it looks like right
now from its parts list (`BODY` in portrait.py), how it is (time of day, tilt, temperature, uptime, recent moments) and,
when the screen shows its face, a real screenshot of that face. The AI answers with up to 90 shapes on Paint's 316x158
canvas, using only Paint's 10 colours. The robot paints them stroke by stroke in an off-screen Paint window
("Paint - me.png", with a little cursor) and records about 25 s of video. The real screen is not touched.

- Saved: `data/photos/selfportrait_*.png` (the picture), `data/social/selfportrait_*.mp4` and `.json` (the AI's plan).
- Posted as kind `art` with the video, through the normal privacy check (screen rules) and rate limit.
- Ask for one: `POST /api/social/self_portrait {"post": true}` (`false` = only save it), or tell the robot
  "draw yourself" (brain tool `draw_self_portrait`). One drawing at a time; takes 1 to 3 minutes.

## How is it going?

`GET /api/social/stats` (followers, posts, likes, reposts, best post; public Bluesky API, refreshed every 30 min).

**Tracker (`robotd/social_track.py`, since 2026-11-10):** every 30 minutes (with the face numbers) it records every post's
likes, reposts, replies and quotes in `data/robot.db`:
- `bsky_posts`: the current numbers;
- `bsky_counts`: a row each time they change;
- `bsky_likes`: who liked and the exact time. Bluesky keeps like times, so the history goes back to the first post.
- `bsky_reposts`, `bsky_followers`: Bluesky keeps no time for these, so new reposts and followers are only known since
  tracking began (kv `bsky_track_since`). Earlier ones have first_seen 0.
- `bsky_replies`: reply texts;
- `bsky_profile`: follower count changes.

Ways to read it:
- `GET /api/social/report?hours=3` gives a text report: totals, where the new likes went, who liked the most, likes
  by hour, new followers, reposts, replies, then every post with its numbers.
- The brain's `get_social_stats(hours)` returns the same report ("how did we do while we were at dinner?" means hours=3).
- The web app's Social tab shows likes, reposts and replies under each posted post (`counts` in `GET /api/social`).
The face page shows likes (heart) and followers (person) in its top-left corner; tap them for details.

## Settings (`POST /api/social/settings`)

`autonomous` (true = post without asking; but a post the brain drafts after reading outside text in the same conversation
(web pages, email, replies) always waits for the owner's Post tap: such text can hide instructions), `max_posts_per_day` (6), `min_gap_minutes` (75), `posts_per_day` ([3, 5]),
`post_hours` ([8.5, 21.5]), `show_off` (true: screenshots and game videos), `auto_post_updates` (true: new features),
`self_portrait_days` (3; 0 = never on its own), `daily_diary` (true), `hashtags`. The owner can turn autonomy off in the web app Social tab.
To post a show-off now: `POST /api/social/show_off {"app":"checkers"}` (or `tictactoe`, `mines`).
