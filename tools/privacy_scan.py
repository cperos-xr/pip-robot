#!/usr/bin/env python3
"""Keeps private things out of the public code: run it before every commit and every publish.

The code of this robot is open source, but each robot lives in somebody's home. This scan stops the commit when a
tracked file (or a commit message) contains:

  1. a secret: an API key, token, password or private key (by its shape), or the real values from this robot's
     secret files (data/secrets.env and friends), matched exactly;
  2. a private detail from YOUR list, data/private_terms.txt: one word or phrase per line (your name, your Wi-Fi
     name, your robot's web address, IP and MAC addresses ...). That file is never committed;
  3. something that looks like a home-network detail: a MAC address, or an IP address that is not one of the
     documented example addresses.

Usage:
  tools/privacy_scan.py                 scan the tracked files of this repo (the working tree)
  tools/privacy_scan.py --staged        scan what is about to be committed (used by the pre-commit hook)
  tools/privacy_scan.py --history       also scan every old version of every file and every commit message
  tools/privacy_scan.py --dir PATH      scan a folder that is not a git repo (used by tools/publish.sh)
  tools/privacy_scan.py --msg FILE      scan a commit message (used by the commit-msg hook)
  tools/privacy_scan.py --install-hooks install the git hooks in this clone

Exit code 0 = clean, 1 = something found (values are never printed in full), 2 = could not run.
A line that is a false alarm can be marked with the words `privacy-scan: ok` in a comment on that line.
Standard library only (it runs on the robot and in CI).
"""
import argparse
import ipaddress
import math
import os
import re
import stat
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OK_MARK = "privacy-scan: ok"
BINARY = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".ico", ".uf2", ".bin", ".stl", ".step", ".stp", ".3mf",
          ".f3d", ".mp4", ".mov", ".mp3", ".wav", ".ogg", ".pdf", ".zip", ".gz", ".ttf", ".woff", ".woff2"}
SKIP_DIRS = {".git", "venv", "__pycache__", "node_modules", "data", "models"}

# ------------------------------------------------------------------------------------------------ what a secret looks like
SECRET_SHAPES = {
    "Anthropic API key": r"sk-ant-[A-Za-z0-9_\-]{20,}",
    "OpenRouter API key": r"sk-or-(?:v1-)?[A-Za-z0-9]{32,}",
    "OpenAI-style API key": r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{32,}",
    "GitHub token": r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})",
    "AWS key": r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b",
    "Google API key": r"\bAIza[0-9A-Za-z_\-]{35}\b",
    "Slack token": r"\bxox[baprs]-[A-Za-z0-9\-]{10,}",
    "private key": r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    "JWT or Cloudflare tunnel token": r"\beyJ[A-Za-z0-9_\-]{40,}",
    "AgentMail key": r"\bam_[a-z0-9_]{24,}",
    "Hugging Face token": r"\bhf_[A-Za-z0-9]{30,}",
    "Pinterest token": r"\bpina_[A-Za-z0-9_\-]{20,}",
    "Bluesky app password": r"\b(?=[a-z0-9\-]*[0-9])(?=[a-z0-9\-]*[a-z])[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4}\b",
    "address with a password in it": r"[a-z][a-z0-9+.\-]*://[^/\s:@\"'<>]{1,64}:[^/\s:@\"'<>{}$]{3,}@",
    "password or key written in the file": r"(?i)\b(?:pass(?:word|wd)?|pwd|psk|secret|token|api[_\-]?key|auth[_\-]?key)\b[\"']?\s*[:=]\s*[\"']"
                                           r"([^\"'\s<>{}$]{12,})[\"']",
}
SECRET_SHAPES = {k: re.compile(v) for k, v in SECRET_SHAPES.items()}
LONG_TOKEN = re.compile(r"(?<![A-Za-z0-9+/_\-])[A-Za-z0-9+/_\-]{40,}={0,2}(?![A-Za-z0-9+/_\-])")

# ------------------------------------------------------------------------------------------------ home-network details
MAC = re.compile(r"(?<![0-9A-Fa-f:\-])(?:[0-9A-Fa-f]{2}[:\-]){5}[0-9A-Fa-f]{2}(?![0-9A-Fa-f:\-])")
IPV4 = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
# Addresses that are fine in public code: loopback, "any", the project's own direct-cable link (laptop .1, robot .2),
# public DNS servers used for connectivity tests, and the ranges reserved for documentation and examples.
OK_IPS = {"0.0.0.0", "127.0.0.1", "127.0.0.53", "127.0.0.54", "255.255.255.255", "255.255.255.0", "192.168.77.1", "192.168.77.2", "1.1.1.1", "8.8.8.8",
          "9.9.9.9", "192.168.1.20", "192.168.1.1"}
OK_NETS = [ipaddress.ip_network(n) for n in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24", "224.0.0.0/4")]
OK_MACS = {"aa:bb:cc:dd:ee:ff", "00:00:00:00:00:00", "ff:ff:ff:ff:ff:ff", "00:11:22:33:44:55", "01:23:45:67:89:ab"}
EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}")
OK_EMAIL_DOMAINS = ("example.com", "example.org", "example.net", "users.noreply.github.com", "anthropic.com", "github.com",
                    "pip.local", "robot.local", "orangepizero.local", "localhost")
SECRET_FILES = ("data/secrets.env", "data/anthropic.env", "data/openrouter.env", "data/github.env", "data/agentmail.env",
                "data/api_token", ".env")


def entropy(s):
    c = Counter(s)
    return -sum(n / len(s) * math.log2(n / len(s)) for n in c.values())


def mask(s):
    s = str(s)
    return (s[:4] + "…" if len(s) > 8 else "…") + f" ({len(s)} characters)"


def git(*args, cwd=ROOT, text=True):
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=text)


# ------------------------------------------------------------------------------------------------ this robot's own lists
def private_terms():
    """Words from data/private_terms.txt (and $PRIVACY_TERMS, and a private_terms.txt next to the repo)."""
    out, seen = [], set()
    places = [ROOT / "data" / "private_terms.txt", ROOT.parent / "private_terms.txt", ROOT.parent / "robot-private" / "private_terms.txt"]
    if os.environ.get("PRIVACY_TERMS"):
        places.insert(0, Path(os.environ["PRIVACY_TERMS"]))
    for f in places:
        try:
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            t = line.strip()
            if t and not t.startswith("#") and len(t) >= 3 and t.lower() not in seen:
                seen.add(t.lower())
                out.append(t)
    return out


def secret_values():
    """The real secret values of this robot (or of the PC's .env), to be matched exactly. Never printed."""
    vals = {}
    for rel in SECRET_FILES:
        for base in (ROOT, ROOT.parent):
            f = base / rel
            try:
                lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for line in lines:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                key, _, v = line.partition("=") if "=" in line else ("value", "", line)
                v = v.split(" #")[0].strip().strip("\"'")
                key = key.strip()
                # names of things (a repo, a handle, a board id) are not secrets; keys, tokens and passwords are
                if len(v) >= 12 and re.search(r"(?i)key|token|secret|pass|value", key):
                    vals[v] = f"{rel}: {key}"
    return vals


# ------------------------------------------------------------------------------------------------ the scan itself
class Scan:
    def __init__(self):
        self.terms = private_terms()
        self.term_rx = re.compile("|".join(re.escape(t) for t in sorted(self.terms, key=len, reverse=True)), re.I) if self.terms else None
        self.secrets = secret_values()
        self.found = []          # (where, line number, kind, shown)

    def add(self, where, n, kind, shown):
        self.found.append((where, n, kind, shown))

    def text(self, where, text):
        for value, src in self.secrets.items():
            if value in text:
                n = text[:text.index(value)].count("\n") + 1
                self.add(where, n, "SECRET", f"the real value of {src}")
        for n, line in enumerate(text.splitlines(), 1):
            if OK_MARK in line:
                continue
            for name, rx in SECRET_SHAPES.items():
                for m in rx.finditer(line):
                    tok = m.group(1) if m.groups() else m.group(0)
                    if re.fullmatch(r"[xX*.\-_<>a-z ]*", tok) and len(set(tok)) < 6:      # xxxx-xxxx placeholders
                        continue
                    if name == "password or key written in the file" and (entropy(tok) < 3.2 or re.search(r"(?i)example|your|changeme|placeholder|\.\.\.", tok)):
                        continue
                    self.add(where, n, "SECRET", f"{name}: {mask(tok)}")
            for m in LONG_TOKEN.finditer(line):
                tok = m.group(0)
                if entropy(tok) >= 4.5 and not re.search(r"[/\\]", tok) and not tok.startswith(("sha", "http")) \
                        and re.search(r"[0-9]", tok) and re.search(r"[a-z]", tok) and re.search(r"[A-Z]", tok):
                    self.add(where, n, "SECRET?", f"long random-looking text: {mask(tok)}")
            if self.term_rx:
                for m in self.term_rx.finditer(line):
                    self.add(where, n, "PRIVATE", f"a word from your private list: {m.group(0)[:3]}… ({len(m.group(0))} characters)")
            for m in MAC.finditer(line):
                if m.group(0).lower().replace("-", ":") not in OK_MACS:
                    self.add(where, n, "NETWORK", f"a MAC address: {m.group(0)[:8]}…")
            for m in IPV4.finditer(line):
                try:
                    ip = ipaddress.ip_address(m.group(0))
                except ValueError:
                    continue
                if m.group(0) in OK_IPS or any(ip in net for net in OK_NETS):
                    continue
                if ip.is_private or ip.is_link_local:
                    self.add(where, n, "NETWORK", f"a home-network IP address: {m.group(0)}")
            for m in EMAIL.finditer(line):
                dom = m.group(0).rsplit("@", 1)[1].lower()
                if not dom.endswith(OK_EMAIL_DOMAINS) and not re.search(r"\.(png|jpg|svg|py|md|js|html)$", dom):
                    self.add(where, n, "EMAIL", f"an email address: {m.group(0)[:2]}…@{dom}")

    def blob(self, where, data):
        if Path(where.split(" @ ")[0]).suffix.lower() in BINARY:
            for value, src in self.secrets.items():
                if value.encode() in data:
                    self.add(where, 0, "SECRET", f"the real value of {src}")
            return
        if b"\x00" in data[:8000]:
            return
        self.text(where, data.decode("utf-8", "replace"))

    def photo(self, where, data):
        """A photo straight from a phone can carry the place it was taken (GPS): that is a home address."""
        if Path(where.split(" @ ")[0]).suffix.lower() not in (".jpg", ".jpeg"):
            return
        i = data.find(b"Exif\x00\x00")
        if i < 0:
            return
        ex = data[i + 6:i + 6 + 65536]
        le = ex[:2] == b"II"
        u16 = lambda b: int.from_bytes(b, "little" if le else "big")
        try:
            off = u16(ex[4:8])
            for k in range(u16(ex[off:off + 2])):
                e = ex[off + 2 + 12 * k: off + 14 + 12 * k]
                if u16(e[:2]) == 0x8825:                                   # the GPS block
                    self.add(where, 0, "LOCATION", "this photo has the GPS position it was taken at (strip it: exiftool -all= file.jpg)")
        except Exception:
            pass


def tracked_files():
    out = git("ls-files", "-z")
    if out.returncode != 0:
        sys.exit("privacy_scan: this is not a git repo (use --dir PATH for a plain folder)")
    return [f for f in out.stdout.split("\0") if f]


def walk(folder):
    folder = Path(folder)
    for p in sorted(folder.rglob("*")):
        if p.is_file() and not any(part in SKIP_DIRS for part in p.relative_to(folder).parts[:-1]) and ".git" not in p.parts:
            yield p


def install_hooks():
    hooks = Path(git("rev-parse", "--git-path", "hooks").stdout.strip())
    hooks = hooks if hooks.is_absolute() else ROOT / hooks
    hooks.mkdir(parents=True, exist_ok=True)
    py = '"$(git rev-parse --show-toplevel)/tools/privacy_scan.py"'
    for name, args in (("pre-commit", "--staged"), ("commit-msg", '--msg "$1"')):
        f = hooks / name
        f.write_text(f"#!/bin/sh\n# installed by tools/privacy_scan.py --install-hooks: keeps secrets and private details out of commits\n"
                     f"exec python3 {py} {args}\n")
        f.chmod(f.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    print(f"hooks installed in {hooks}: every commit in this clone is scanned first")


def main():
    ap = argparse.ArgumentParser(description="Keep secrets and private details out of the public code.")
    ap.add_argument("--staged", action="store_true")
    ap.add_argument("--history", action="store_true")
    ap.add_argument("--dir")
    ap.add_argument("--msg")
    ap.add_argument("--install-hooks", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    if a.install_hooks:
        return install_hooks()
    s = Scan()
    n_files = 0
    if a.msg:
        s.text("the commit message", Path(a.msg).read_text(encoding="utf-8", errors="replace"))
    elif a.dir:
        for p in walk(a.dir):
            data = p.read_bytes()
            rel = str(p.relative_to(a.dir))
            s.blob(rel, data)
            s.photo(rel, data)
            n_files += 1
    elif a.staged:
        names = [f for f in git("diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z").stdout.split("\0") if f]
        for f in names:
            data = git("show", ":" + f, text=False).stdout
            s.blob(f, data)
            s.photo(f, data)
            n_files += 1
    else:
        for f in tracked_files():
            p = ROOT / f
            if p.is_file():
                data = p.read_bytes()
                s.blob(f, data)
                s.photo(f, data)
                n_files += 1
    if a.history:
        seen = set()
        for line in git("rev-list", "--objects", "--all").stdout.splitlines():
            sha, _, path = line.partition(" ")
            if not path or sha in seen:
                continue
            seen.add(sha)
            if git("cat-file", "-t", sha).stdout.strip() != "blob":
                continue
            data = git("cat-file", "blob", sha, text=False).stdout
            s.blob(f"{path} @ {sha[:8]} (old version)", data)
            s.photo(f"{path} @ {sha[:8]} (old version)", data)
            n_files += 1
        log = git("log", "--all", "--format=%h%x01%an <%ae>%x01%B%x02").stdout
        for chunk in log.split("\x02"):
            if chunk.strip():
                h, who, body = (chunk.strip().split("\x01") + ["", ""])[:3]
                s.text(f"commit {h} (message)", body)
                s.text(f"commit {h} (author)", who)

    found = sorted(set(s.found))
    hard = [f for f in found if f[2] != "SECRET?"]
    if not a.quiet or found:
        what = "the commit message" if a.msg else f"{n_files} file{'s' if n_files != 1 else ''}"
        lists = f"{len(s.terms)} private words, {len(s.secrets)} secret values" if (s.terms or s.secrets) else \
            "no private list found (data/private_terms.txt): only shapes are checked"
        print(f"privacy scan: {what} checked ({lists})")
    for where, n, kind, shown in found:
        print(f"  {kind:9} {where}{':' + str(n) if n else ''}  {shown}")
    if hard:
        print(f"\nSTOP: {len(hard)} thing{'s' if len(hard) != 1 else ''} must not go public. Move private facts to data/manual/ "
              f"(never committed), secrets to data/secrets.env.\nA false alarm? Add a comment with `{OK_MARK}` on that line.")
        return 1
    if found:
        print("\nOnly 'SECRET?' lines: look at them; they are allowed (random-looking text is often just a hash or an id).")
    elif not a.quiet:
        print("clean: nothing private found")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
