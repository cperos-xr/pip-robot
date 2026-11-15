# Journal: the conversation journal and notes about people

Written 2026-11-10 (the owner asked for it). Code: `robotd/journal.py`. Tables live in the normal `robot.db`.

## What is recorded

- **Journal** (`journal` table): one compact line per exchange with the owner, for example
  `O: what was said | P: what Pip answered`. Each side is cut short (owner 160 characters, Pip 200), so a turn takes
  about 150-400 bytes. Text only: no images, no audio, no face pictures.
- Saved photos of moments (`save_moment`) also get a line: `MOMENT <date time>: <description> | photo: <file>`, src `moment`.
- Written by `Brain.ask()` for voice (`src` = voice), the web app and API chat (`src` = web). Self-tests and the robot's
  own button prompts (READ, I see) are not journaled.
- **Notes about people and pets** (`people` table): short sentences the OWNER said, e.g. "Alex has brown hair
  and blue eyes", "Biscuit is a speckled dog". Each note keeps its name and its source ("owner said").

## Rules (also in the brain's prompt)

- Notes come only from what the owner tells Pip in words. Never from a photo, never from a face. No face images are
  stored by the journal.
- Notes are used to recognise who is talking, but only as a guess. Pip says "I'm only guessing from the description".
- Nothing is deleted by the journal code. Deleting or editing data is a risky step: ask the owner on the screen first.

## Tools (the brain)

| Tool | Does |
|---|---|
| `journal_search(query)` | Keyword search (plain text match). Best matches first, newest on ties. Used for "do you remember...". |
| `people_notes(name)` | Notes about a person or pet. Empty name = everyone. |
| `note_person(name, note)` | Save one short note, from the owner's words only. |

The notes list is also added to the system prompt (about 1200 characters at most), so Pip knows the people it lives with.

## Later

- Embeddings or RAG for search, if the journal gets large. Plain keyword search is enough for now.
- Storage: about 0.4 KB a turn. Ten turns a day is about 1.5 MB a year, well inside the SD card's free space.
