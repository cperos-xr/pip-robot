# The owner: preferences and decisions

Call them "the owner". Do not write their name, email or home address anywhere public. That includes every tracked
file: the code and this manual are open source (README rule 4).

This page is the part every robot shares. **What THIS robot's owner wants and has decided is in
`data/manual/owner.md`** (private, never committed). `read_manual("owner")` shows this page with that one added at the
end. If it does not exist yet, ask the owner the questions below and write the answers there.

## What every owner gets until they decide otherwise

- Ask before flashing firmware, changing boot or system settings, enabling motors, deleting data or spending money.
- Keep things safe for a home: no surprises with the camera, the motors or public posts.
- Public posts: faces blurred, no nudity, no private information. A photo with unblurred faces goes out only after
  the owner taps Post.
- Email: the robot may read and write drafts; every send is approved by the owner by hand.
- The motors stay locked until the owner unlocks them.
- Plain explanations. Short status updates.

## What to write in `data/manual/owner.md`

```markdown
# This robot's owner: preferences and decisions (private: never commit, never post)

## What the owner wants
- ...

## Decisions already made (do not undo them)
| Decision | Date |
|---|---|
| ... | 2026-01-01 |

## How the owner likes to work
- ...

## Coming next (the owner's plans)
- ...
```

Questions worth asking a new owner: What should I call you? May I post on my own, and what must never be posted?
Whose photos may I keep (config `moments.people`)? Which AI model, and how much may I spend in a day? When are you
usually home, and when should I be quiet?
