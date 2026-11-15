"""Bluesky stats tracker: every 30 minutes, record how each of the robot's posts is doing.

Tables (data/robot.db):
  bsky_posts      one row per post: current likes / reposts / replies / quotes (+ our posts.id when it is ours)
  bsky_counts     history: a row each time a post's numbers change (so "how did post X do over time" works)
  bsky_likes      every like with its exact time (Bluesky keeps the time, so this goes back to the very first post)
  bsky_reposts    who reposted, first seen time (Bluesky keeps no time for reposts: only known since tracking began)
  bsky_replies    replies to the robot's posts (author, text, time)
  bsky_followers  followers, first seen / gone time (followers that existed before tracking began have first_seen 0)
  bsky_profile    followers / following / posts count, a row each time they change

Only the public Bluesky API is used (no login). report(hours) is what the brain's get_social_stats tool returns.
"""
import os
import threading
import time
from datetime import datetime

import requests

API = "https://public.api.bsky.app/xrpc/"
EVERY_S = 1800

SCHEMA = """
CREATE TABLE IF NOT EXISTS bsky_posts(uri TEXT PRIMARY KEY, created REAL, text TEXT, post_id INTEGER, likes INTEGER,
    reposts INTEGER, replies INTEGER, quotes INTEGER, updated REAL);
CREATE TABLE IF NOT EXISTS bsky_counts(id INTEGER PRIMARY KEY, ts REAL, uri TEXT, likes INTEGER, reposts INTEGER,
    replies INTEGER, quotes INTEGER);
CREATE TABLE IF NOT EXISTS bsky_likes(uri TEXT, did TEXT, handle TEXT, name TEXT, ts REAL, PRIMARY KEY(uri, did));
CREATE TABLE IF NOT EXISTS bsky_reposts(uri TEXT, did TEXT, handle TEXT, name TEXT, first_seen REAL, PRIMARY KEY(uri, did));
CREATE TABLE IF NOT EXISTS bsky_replies(uri TEXT PRIMARY KEY, parent TEXT, did TEXT, handle TEXT, text TEXT, ts REAL);
CREATE TABLE IF NOT EXISTS bsky_followers(did TEXT PRIMARY KEY, handle TEXT, name TEXT, first_seen REAL, gone REAL);
CREATE TABLE IF NOT EXISTS bsky_profile(ts REAL, followers INTEGER, follows INTEGER, posts INTEGER);
CREATE INDEX IF NOT EXISTS bsky_likes_ts ON bsky_likes(ts);
CREATE INDEX IF NOT EXISTS bsky_counts_ts ON bsky_counts(ts);
"""


def _ts(iso):
    try:
        return datetime.fromisoformat((iso or "").replace("Z", "+00:00")).timestamp()
    except ValueError:
        return time.time()


def _when(ts):
    return time.strftime("%b %d %-I:%M %p", time.localtime(ts)) if ts else "?"


def _short(text, n=60):
    t = " ".join((text or "").split())
    return t if len(t) <= n else t[:n - 1] + "…"


class SocialTracker:
    def __init__(self, robot):
        self.r = robot
        self.lock = threading.Lock()
        self.last = 0.0
        self.error = ""
        with self.r.store.lock:
            self.r.store.db.executescript(SCHEMA)
            self.r.store.db.commit()

    # ------------------------------------------------------------------ helpers
    def _db(self, sql, args=(), many=False):
        with self.r.store.lock:
            cur = self.r.store.db.executemany(sql, args) if many else self.r.store.db.execute(sql, args)
            rows = cur.fetchall()
            self.r.store.db.commit()
            return rows

    @staticmethod
    def _get(method, **params):
        r = requests.get(API + method, params=params, timeout=20)
        r.raise_for_status()
        return r.json()

    def _pages(self, method, key, limit_pages=20, **params):
        out, cursor = [], None
        for _ in range(limit_pages):
            d = self._get(method, limit=100, **params, **({"cursor": cursor} if cursor else {}))
            out += d.get(key, [])
            cursor = d.get("cursor")
            if not cursor or not d.get(key):
                break
        return out

    # ------------------------------------------------------------------ sweep
    def maybe_sweep(self):
        """Called from the social tick; sweeps in the background every 30 minutes."""
        if time.time() - self.last < EVERY_S or self.lock.locked() or not os.environ.get("BLUESKY_HANDLE"):
            return
        threading.Thread(target=self.sweep, daemon=True).start()

    def sweep(self):
        if not self.lock.acquire(blocking=False):
            return
        try:
            self.last = time.time()
            self._sweep()
            self.error = ""
        except Exception as e:
            self.error = str(e)[:200]
            self.r.log(f"bluesky tracker: {e}", "warn", persist=False)
        finally:
            self.lock.release()

    def _sweep(self):
        handle = os.environ.get("BLUESKY_HANDLE")
        now = time.time()
        first_run = not self.r.store.kv_get("bsky_track_since")
        prof = self._get("app.bsky.actor.getProfile", actor=handle)
        me = prof.get("did")
        last = self._db("SELECT followers, follows, posts FROM bsky_profile ORDER BY ts DESC LIMIT 1")
        cur = (prof.get("followersCount", 0), prof.get("followsCount", 0), prof.get("postsCount", 0))
        if not last or tuple(last[0]) != cur:
            self._db("INSERT INTO bsky_profile VALUES(?,?,?,?)", (now, *cur))

        ours = {u: pid for pid, u in self._db("SELECT id, bsky_uri FROM posts WHERE bsky_uri IS NOT NULL AND bsky_uri != ''")}
        old = {row[0]: row[1:] for row in self._db("SELECT uri, likes, reposts, replies, quotes FROM bsky_posts")}
        feed = self._pages("app.bsky.feed.getAuthorFeed", "feed", 5, actor=handle, filter="posts_no_replies")
        for f in feed:
            p = f.get("post") or {}
            if (p.get("author") or {}).get("did") != me or f.get("reason"):
                continue                                   # reposts of other people's posts are not ours
            uri = p["uri"]
            n = (p.get("likeCount", 0), p.get("repostCount", 0), p.get("replyCount", 0), p.get("quoteCount", 0))
            rec = p.get("record") or {}
            self._db("INSERT INTO bsky_posts(uri, created, text, post_id, likes, reposts, replies, quotes, updated) "
                     "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(uri) DO UPDATE SET likes=excluded.likes, reposts=excluded.reposts, "
                     "replies=excluded.replies, quotes=excluded.quotes, updated=excluded.updated, post_id=excluded.post_id",
                     (uri, _ts(rec.get("createdAt")), rec.get("text", ""), ours.get(uri), *n, now))
            before = old.get(uri)
            if before is None or tuple(before) != n:
                self._db("INSERT INTO bsky_counts(ts, uri, likes, reposts, replies, quotes) VALUES(?,?,?,?,?,?)", (now, uri, *n))
            if n[0] and (before is None or before[0] != n[0]):
                likes = self._pages("app.bsky.feed.getLikes", "likes", 10, uri=uri)
                self._db("INSERT OR IGNORE INTO bsky_likes VALUES(?,?,?,?,?)",
                         [(uri, x["actor"]["did"], x["actor"].get("handle", ""), x["actor"].get("displayName", ""),
                           _ts(x.get("createdAt") or x.get("indexedAt"))) for x in likes if x.get("actor")], many=True)
            if n[1] and (before is None or before[1] != n[1]):
                reps = self._pages("app.bsky.feed.getRepostedBy", "repostedBy", 5, uri=uri)
                self._db("INSERT OR IGNORE INTO bsky_reposts VALUES(?,?,?,?,?)",
                         [(uri, x["did"], x.get("handle", ""), x.get("displayName", ""), 0 if first_run else now) for x in reps],
                         many=True)
            if n[2] and (before is None or before[2] != n[2]):
                th = self._get("app.bsky.feed.getPostThread", uri=uri, depth=1, parentHeight=0).get("thread") or {}
                rows = []
                for rp in th.get("replies") or []:
                    q = rp.get("post") or {}
                    if q.get("uri"):
                        a = q.get("author") or {}
                        rows.append((q["uri"], uri, a.get("did", ""), a.get("handle", ""), (q.get("record") or {}).get("text", ""),
                                     _ts((q.get("record") or {}).get("createdAt"))))
                self._db("INSERT OR IGNORE INTO bsky_replies VALUES(?,?,?,?,?,?)", rows, many=True)

        known = {d: g for d, g in self._db("SELECT did, gone FROM bsky_followers")}
        if first_run or len([1 for g in known.values() if not g]) != cur[0]:
            fol = self._pages("app.bsky.graph.getFollowers", "followers", 20, actor=handle)
            seen = set()
            for x in fol:
                seen.add(x["did"])
                if x["did"] not in known:
                    self._db("INSERT INTO bsky_followers VALUES(?,?,?,?,NULL)",
                             (x["did"], x.get("handle", ""), x.get("displayName", ""), 0 if first_run else now))
                elif known[x["did"]]:
                    self._db("UPDATE bsky_followers SET gone=NULL, first_seen=? WHERE did=?", (now, x["did"]))
            for d, g in known.items():
                if d not in seen and not g:
                    self._db("UPDATE bsky_followers SET gone=? WHERE did=?", (now, d))
        if first_run:
            self.r.store.kv_set("bsky_track_since", now)
        self.r.log(f"bluesky tracker: {len(feed)} posts checked, {cur[0]} followers", persist=False)

    # ------------------------------------------------------------------ reading
    def counts_by_post_id(self):
        return {pid: {"likes": l, "reposts": rp, "replies": rr, "quotes": q}
                for pid, l, rp, rr, q in self._db("SELECT post_id, likes, reposts, replies, quotes FROM bsky_posts WHERE post_id IS NOT NULL")}

    def report(self, hours=24):
        hours = max(1, min(int(hours or 24), 24 * 60))
        if not self.last and not self._db("SELECT 1 FROM bsky_posts LIMIT 1"):
            self.sweep()
        posts = self._db("SELECT uri, created, text, likes, reposts, replies, quotes, updated FROM bsky_posts ORDER BY created DESC")
        if not posts:
            return "No Bluesky numbers yet" + (f" (last error: {self.error})" if self.error else "") + "."
        prof = self._db("SELECT followers, follows, posts FROM bsky_profile ORDER BY ts DESC LIMIT 1")
        since = float(self.r.store.kv_get("bsky_track_since") or 0)
        cut = time.time() - hours * 3600
        title = {u: t for u, _c, t, *_ in posts}
        upd = max(p[7] or 0 for p in posts)
        tot = [sum(p[i] or 0 for p in posts) for i in (3, 4, 5, 6)]
        out = [f"Bluesky, checked {int((time.time() - upd) / 60)} min ago. Totals: {len(posts)} posts, {tot[0]} likes, "
               f"{tot[1]} reposts, {tot[2]} replies, {tot[3]} quotes"
               + (f", {prof[0][0]} followers (following {prof[0][1]})." if prof else ".")]
        out.append(f"Tracking since {_when(since)}. Like times are exact back to the first post; new followers and reposts "
                   "are only known since tracking began.")

        likes = self._db("SELECT uri, handle, name, ts FROM bsky_likes WHERE ts>=? ORDER BY ts", (cut,))
        reps = self._db("SELECT uri, handle FROM bsky_reposts WHERE first_seen>=? AND first_seen>0", (cut,))
        repl = self._db("SELECT parent, handle, text, ts FROM bsky_replies WHERE ts>=? ORDER BY ts", (cut,))
        newf = self._db("SELECT handle, name FROM bsky_followers WHERE first_seen>=? AND first_seen>0 AND gone IS NULL", (cut,))
        lost = self._db("SELECT handle FROM bsky_followers WHERE gone>=?", (cut,))
        span = f"the last {hours} hours" if hours != 24 else "the last 24 hours"
        out.append(f"\nIn {span}: {len(likes)} new likes from {len({l[1] for l in likes})} accounts, {len(reps)} new reposts, "
                   f"{len(repl)} new replies, {len(newf)} new followers" + (f", {len(lost)} unfollowed" if lost else "") + ".")
        if likes:
            by_post = {}
            for u, *_ in likes:
                by_post[u] = by_post.get(u, 0) + 1
            out.append("Where the new likes went:")
            for u, k in sorted(by_post.items(), key=lambda x: -x[1])[:8]:
                p = next(x for x in posts if x[0] == u) if any(x[0] == u for x in posts) else None
                out.append(f"- {k} on \"{_short(title.get(u, '?'))}\" (posted {_when(p[1]) if p else '?'}, now {p[3] if p else '?'} likes)")
            who = {}
            for _u, h, n, _t in likes:
                who[h] = who.get(h, 0) + 1
            top = sorted(who.items(), key=lambda x: -x[1])[:6]
            out.append("Who liked the most: " + ", ".join(f"@{h} ({k})" for h, k in top))
            hrs = {}
            for *_x, t in likes:
                key = time.strftime("%a %-I %p", time.localtime(t))
                hrs[key] = hrs.get(key, 0) + 1
            out.append("Likes by hour: " + ", ".join(f"{k}: {v}" for k, v in list(hrs.items())[-12:]))
        if newf:
            out.append("New followers: " + ", ".join(f"@{h}" + (f" ({n})" if n else "") for h, n in newf[:10]))
        if reps:
            out.append("New reposts: " + ", ".join(f"@{h} on \"{_short(title.get(u, '?'), 40)}\"" for u, h in reps[:8]))
        for parent, h, text, t in repl[-6:]:
            out.append(f"Reply from @{h} ({_when(t)}) on \"{_short(title.get(parent, '?'), 40)}\": {_short(text, 160)}")

        out.append("\nEvery post (newest first): posted | likes, reposts, replies | text")
        for u, created, text, l, rp, rr, q, _ in posts[:30]:
            out.append(f"- {_when(created)} | {l} likes, {rp} reposts, {rr} replies | {_short(text, 70)}")
        return "\n".join(out)[:7000]
