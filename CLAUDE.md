# Virtual Navigator — working agreements

See **Working on the code** in `README.md` for the reasoning behind these.

## Branch, don't build on main

Build every feature on its own branch. Merge to `main` only when the feature
is finished and the tests pass; pushing `main` is the deploy.

```bash
git switch -c <feature-name>
# … build, run .venv/bin/python -m pytest …
git switch main && git merge --no-ff <feature-name>
git push                       # the Action runs the tests, then deploys
```

**Production is deployed only by the GitHub Action, from `main`.** A push
to `main` runs the tests, deploys **staging** (`virtual-navigator-staging`),
smoke-tests it (`scripts/smoke.sh`: healthy, answering, and running that
exact commit), and only then deploys production, after a volume snapshot,
and smoke-tests that too. Never run `fly deploy` against `virtual-navigator`
from a laptop: a hand deploy builds from whatever is in the working
directory, skips the tests, and races the Action.

Work that can only be tried on a server (a `scripts/` ops tool, a tracker
link, a document import) goes to staging **from the branch, by hand**:

```bash
fly deploy --config fly.staging.toml   # virtual-navigator-staging
```

Staging sleeps between uses and wakes on the first request; its database is
its own (empty until you put something there), and nothing on it is real.
Merge after it works there, not before.

## One session, one worktree

Several Claude sessions often work in this repo at the same time. A branch
does not isolate them: one checkout has one HEAD, so `git switch` in one
session moves every session onto that branch and drags along whatever
uncommitted edits are sitting in the tree.

Before you edit, run `git status` and `git branch --show-current`. If the
tree already holds changes that aren't yours, or the branch isn't `main`,
someone else is working here. Do not `git switch`. Give yourself a separate
directory instead:

```bash
git worktree add ../vn-<feature-name> -b <feature-name>
cd ../vn-<feature-name>
```

Agents started with `isolation: "worktree"` get this for free. Remove the
worktree after the merge with `git worktree remove ../vn-<feature-name>`.
Never commit, stash, or revert files you did not change.

**Branch again after you merge.** Merging and deleting the branch leaves you
standing on `main`, so the next quick fix lands directly on it — this is how
main picks up stray commits in practice, and it is worth a deliberate
`git switch -c` before the next edit. Merge with `--no-ff`: a fast-forward
merge leaves no trace, so branch work and direct commits look identical in the
log afterwards and the mistake is invisible.

## Before pushing main

- The suite is green locally: `.venv/bin/python -m pytest`.
- `main` holds only merged, finished branches. The next push ships everything
  on it, so never merge something you would not deploy tonight.
- Never merge someone else's unfinished work to get it out of the way. If a
  branch or worktree isn't yours, say so and ask.
- **Deploy freeze around a start.** From two hours before any race gun until
  the fleet has cleared the line, nothing is pushed to `main`. A deploy
  restarts the engine and replays the gap under the lock; the start is the
  worst moment for that.

## After pushing main

Watch the Action through its three jobs (tests, staging, production), then
verify against the **live site**, not the local file:

```bash
curl -s https://virtual-navigator.fly.dev/healthz
curl -s https://virtual-navigator.fly.dev/race.html | grep <the-thing>
fly releases --app virtual-navigator | head
```

If prod doesn't match what you expect, check `fly releases` and the Action
log — someone else may have pushed since you last looked.

## Why the care

Races run continuously and the sim advances every minute. Time never rewinds,
so a bad deploy is sailed through by the fleet and cannot be undone.

## Running locally

```bash
.venv/bin/python app.py    # → http://127.0.0.1:5170
```

Production ops run through the committed scripts in `scripts/`, via
`fly ssh console -C "python /app/scripts/<script>.py …"`.
