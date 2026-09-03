# Virtual Navigator — working agreements

See **Working on the code** in `README.md` for the reasoning behind these.

## Branch, don't build on main

Build every feature on its own branch. Merge to `main` only when the feature
is finished, tested, and going out in the next deploy.

```bash
git switch -c <feature-name>
# … build and test …
git switch main && git merge <feature-name>
fly deploy
```

`fly deploy` builds from the **working directory, not from `main`**. Unfinished
work sitting in the tree ships the moment anyone deploys anything else. Two
features in progress in the same file also mean neither can be committed
without hand-separating the diff.

## Before deploying

- `git status` must be clean. If it isn't, stop — you don't know what you are
  about to ship. Commit it, stash it, or switch branches.
- Never bundle someone else's unfinished work into your deploy. If the tree
  holds changes that aren't yours, say so and ask rather than shipping them.
- `main` must stay deployable, because the next person to deploy anything
  ships whatever is sitting there.

## After deploying

Verify against the **live site**, not the local file — an edit on disk is not
live:

```bash
curl -s https://virtual-navigator.fly.dev/race.html | grep <the-thing>
fly releases --app virtual-navigator | head
```

If prod doesn't match what you expect, check `fly releases` — someone else may
have deployed since you last looked.

## Why the care

Races run continuously and the sim advances every minute. Time never rewinds,
so a bad deploy is sailed through by the fleet and cannot be undone.

## Running locally

```bash
.venv/bin/python app.py    # → http://127.0.0.1:5170
```

Production ops run through the committed scripts in `scripts/`, via
`fly ssh console -C "python /app/scripts/<script>.py …"`.
