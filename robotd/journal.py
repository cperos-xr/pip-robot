"""Long-term journal (owner, 2026-11-10: "a conversation journal skill ... searchable, compact, and notes about people").

Two small tables in the existing robot.db (core.Store):
- journal: one compact line per exchange with the owner: "O: what was said | P: what Pip answered". Text only.
  Each side is cut short so storage stays small (about 150-400 bytes a turn). No images, no audio.
- people: short notes the OWNER gave about people or pets (hair, eyes, a dog's markings). Each note keeps who said it.
  Notes are never made from photos or faces. Face images are not stored anywhere by this module.

Search is plain keyword matching first. Embeddings or RAG can come later if the journal gets big.
"""
import re
import time

STOP = {"the", "and", "for", "you", "your", "are", "was", "that", "this", "with", "what", "when", "where", "who",
        "did", "does", "have", "has", "about", "tell", "remember", "recall", "said", "say", "can", "you", "me",
        "my", "our", "its", "it's", "from", "they", "them", "there", "were", "will", "just", "also", "not", "but"}


def short(text, n):
    t = re.sub(r"\s+", " ", (text or "")).strip()
    return t if len(t) <= n else t[:n - 3].rstrip() + "..."


class Journal:
    def __init__(self, robot):
        self.r = robot
        with robot.store.lock:
            robot.store.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS journal(id INTEGER PRIMARY KEY, ts REAL, src TEXT, txt TEXT);
                CREATE INDEX IF NOT EXISTS j_ts ON journal(ts);
                CREATE TABLE IF NOT EXISTS people(id INTEGER PRIMARY KEY, ts REAL, name TEXT, note TEXT, source TEXT);
                CREATE INDEX IF NOT EXISTS p_name ON people(name);
                """
            )
            robot.store.db.commit()

    # ------------------------------------------------------------------ the journal
    def write(self, src, said, reply):
        """Record one exchange, compactly. Never raises: the journal must not break a conversation."""
        try:
            txt = f"O: {short(said, 160)} | P: {short(reply, 200)}"
            with self.r.store.lock:
                self.r.store.db.execute("INSERT INTO journal(ts, src, txt) VALUES(?,?,?)", (time.time(), src, txt))
                self.r.store.db.commit()
        except Exception as e:
            self.r.log(f"journal write failed: {e}", "warn", persist=False)

    def moment(self, description, rel_path, when=None):
        """Journal entry for a saved photo of an interesting moment, so a keyword search (bird, sunset) finds it."""
        try:
            when = when or time.strftime("%Y-%m-%d %H:%M", time.localtime())
            txt = f"MOMENT {when}: {short(description, 300)} | photo: {rel_path}"
            with self.r.store.lock:
                self.r.store.db.execute("INSERT INTO journal(ts, src, txt) VALUES(?,?,?)", (time.time(), "moment", txt))
                self.r.store.db.commit()
        except Exception as e:
            self.r.log(f"journal moment failed: {e}", "warn", persist=False)

    def search(self, query, n=8):
        """Keyword search. Lines that contain the most of the query words come first, newest first on ties."""
        words = sorted({w.lower() for w in re.findall(r"[A-Za-z0-9']+", query or "") if len(w) > 2 and w.lower() not in STOP})
        with self.r.store.lock:
            if not words:
                rows = self.r.store.db.execute("SELECT ts, src, txt FROM journal ORDER BY id DESC LIMIT ?", (n,)).fetchall()
                return [self._line(r) for r in rows]
            where = " OR ".join(["lower(txt) LIKE ?"] * len(words))
            rows = self.r.store.db.execute(f"SELECT id, ts, src, txt FROM journal WHERE {where} ORDER BY id DESC LIMIT 2000",
                                           [f"%{w}%" for w in words]).fetchall()
        scored = []
        for row in rows:
            low = row[3].lower()
            hits = sum(1 for w in words if w in low)
            scored.append((-hits, -row[0], row))
        scored.sort()
        return [self._line(r[1:]) for _, _, r in scored[:n]]

    @staticmethod
    def _line(row):
        ts, src, txt = row
        return f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(ts))} [{src}] {txt}"

    # ------------------------------------------------------------------ notes about people
    def note_person(self, name, note, source="owner said"):
        name = short(name, 40)
        note = short(note, 300)
        if not name or not note:
            return "I need a name and a note."
        with self.r.store.lock:
            self.r.store.db.execute("INSERT INTO people(ts, name, note, source) VALUES(?,?,?,?)",
                                    (time.time(), name, note, source))
            self.r.store.db.commit()
        return f"noted about {name}: {note}"

    def people(self, name=""):
        """Notes about people, grouped by name. name = "" gives everyone."""
        with self.r.store.lock:
            if name:
                rows = self.r.store.db.execute("SELECT name, note FROM people WHERE lower(name) LIKE ? ORDER BY id",
                                               (f"%{name.lower()}%",)).fetchall()
            else:
                rows = self.r.store.db.execute("SELECT name, note FROM people ORDER BY id").fetchall()
        groups = {}
        for n, note in rows:
            groups.setdefault(n, []).append(note)
        return "\n".join(f"{n}: " + "; ".join(notes) for n, notes in groups.items()) or "no notes yet"

    def people_block(self, limit=1200):
        """Short list for the system prompt, so Pip knows who it talks to."""
        text = self.people()
        if text == "no notes yet":
            return ""
        return text[:limit]
