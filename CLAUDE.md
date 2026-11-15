# For Claude (and other AI agents) working on this repo

This is the code of a small home robot (imalittlerobot). Before doing anything:

1. Read `knowledge/manual/README.md` (rules + map of the manual).
2. Read `knowledge/manual/changing_things.md` before you change code or firmware.
3. Read the manual page for the part you are touching (`body`, `software`, `api`, `screen`, `senses`,
   `brain`, `social`, `motors`, `troubleshooting`, `owner`).

The most important rules (full list in the README):
- Ask the owner before flashing firmware, changing boot/system settings, enabling motors, deleting data or spending money.
- Never print or commit secrets (`data/secrets.env`). Never commit `data/`.
- The code is public: no names, addresses, Wi-Fi names, IPs, MACs or this robot's own URLs in tracked files.
  Private facts about one robot and its home go in `data/manual/`. Run `tools/privacy_scan.py` before committing.
- No people, faces, addresses or nudity in anything public.
- Small changes, read first, test after, keep the manual up to date.
