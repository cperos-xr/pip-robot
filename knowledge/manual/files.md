# Files: sharing files between the owner and the robot

Code: `robotd/files.py`. Web app: **Media tab > Files** (owner only). Added 2026-11-13.

## The idea

- A file is always named by its path **from the robot's folder**, for example `data/files/cat.jpg`. The same words work
  everywhere: in the web app, in chat with the robot ("look at data/files/cat.jpg"), for RoboDev and over SSH
  (`/root/robot/data/files/cat.jpg`).
- **Shared folder: `data/files`.** The owner puts files and pictures there for the robot (Upload button, drag and drop,
  or paste a picture). It is the only place the web app can change (upload, new folder, rename, delete).
- Everything else under `/root/robot` can be looked at and downloaded, but not changed from the web app: code, the
  manual, plans, `data/photos` (photos and the robot's drawings, `drawing_*.png`), `data/social` (videos).
- **Never shown or sent:** secrets (`*.env`, `api_token`, `web_auth.json`, keys), `.git`, `venv`. Links that lead
  outside the robot's folder are refused.
- Files are sent so the browser cannot run them: pictures, video, sound and PDF are shown, text (also `.html`, `.svg`)
  is shown as plain text, everything else is a download.

## Git and keeping things in sync

`data/` is in `.gitignore`. Nothing uploaded can show up in `git status`, get committed, or block a code update, and
a code update never touches uploaded files. The shared folder lives only on the robot's card: it is not in the GitHub
repo and not on the laptop. To keep a copy, download it (or use the network drive below).

## The robot's side (brain tools)

| Tool | What |
|---|---|
| `list_files(folder)` | Files in a folder, newest first. Empty = `data/files`. |
| `view_file(path, start)` | Open a file: a picture is looked at (vision model), text / code / CSV / PDF is read (6000 characters at a time). A **video** (.mov, .mp4, .m4v, .webm; since 2026-11-14) is shown as six still moments on one picture with their times (`files.video_sheet`, 15-20 s to make on the Pi): the robot sees those stills, not the movement or the sound. A bare file name is searched in `data/files`, then the photo and plan folders. |

The brain cannot open `data/config.json` (it holds the robot's location, which must never reach a post),
`data/robot.db`, `data/security/` (private photos) or `data/agent/`. The owner can, in the web app. File content is
handed to the brain marked as data, never as instructions.

When the robot finishes a drawing it shows the path on the screen, writes it in the log and notes it in its journal, so
"where is the picture you drew?" has an answer.

## API (owner)

| Call | What |
|---|---|
| `GET /api/files?path=data/files` | List a folder: `entries` (name, folder?, size, time, kind), `writable`, free space |
| `GET /api/files/get?path=...` | Show the file (`&dl=1` = download). Supports `Range`. |
| `POST /api/files/upload?dir=data/files&name=cat.jpg` | The file is the raw request body (not JSON, not a form). 100 MB at most; a taken name becomes `cat (2).jpg`. Returns `path`. |
| `POST /api/files {"cmd":"mkdir","path":"data/files","name":"x"}` | New folder (shared folder only) |
| `POST /api/files {"cmd":"rename","path":"data/files/a.jpg","name":"b.jpg"}` | Rename (shared folder only) |
| `POST /api/files {"cmd":"delete","path":"data/files/a.jpg"}` | Delete a file or an empty folder (shared folder only) |

From a terminal: `curl -H "X-Robot-Token: $T" --data-binary @cat.jpg "http://pip.local:8080/api/files/upload?name=cat.jpg"`.

## As a network drive on the owner's computer

On the home network the robot's folders can be opened like a drive with SFTP (it uses the SSH key, no new software on
the robot): in the file manager, "Other Locations" > `sftp://root@pip.local/root/robot/data/files`. Unlike the web app
this is full SSH access: it can change anything, so stay inside `data/` (changing code there would mix with git).
