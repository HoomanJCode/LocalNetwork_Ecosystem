# AI Commit Rules & Git Workflow

> These rules govern how AI agents commit code to this repository.
> **Follow them exactly.** Do not commit directly to `master`.

---

## 1. Core Principles

| Rule | Why |
|------|-----|
| **Never commit directly to `master`** | Every change goes through a temporary branch that gets reviewed, merged, and deleted. |
| **Never make a single giant commit** | Break work into small, logical, reviewable commits. One commit per logical step. |
| **One temporary branch per todo item** | A "todo" is a task from the TODO files (see `TODO.md` → `docs/todos/*.md`). |
| **Update the todo files to reflect reality** | Mark tasks done, rename files that fully passed. |

---

## 2. Workflow Overview

```
┌─────────────────────────────────────────────────────────────────┐
│  1. Pick a todo item (e.g. Phase 5, task 5.1)                   │
│                                                                 │
│  2. Create a temporary branch from master                       │
│       git checkout master && git checkout -b feat/task-5.1      │
│                                                                 │
│  3. Implement the task in small steps.                          │
│     After EACH logical step, make a small commit                │
│     (see §3 for commit message format).                         │
│     ┌────────┐  ┌────────┐  ┌────────┐  ┌────────┐             │
│     │ commit │→ │ commit │→ │ commit │→ │ commit │  (3-10+)   │
│     └────────┘  └────────┘  └────────┘  └────────┘             │
│                                                                 │
│  4. Run the relevant tests / lints for the task                 │
│       python -m pytest tests/test_xxx.py -v                     │
│                                                                 │
│  5. BEFORE merging: update the todo file                        │
│     - Mark completed task items as [x]                          │
│     - If the whole FILE's phases passed: add "passed" suffix    │
│                                                                 │
│  6. Merge the branch into master (no fast-forward, keep history)│
│       git checkout master                                       │
│       git merge --no-ff feat/task-5.1                           │
│                                                                 │
│  7. Delete the temporary branch                                 │
│       git branch -d feat/task-5.1                               │
│                                                                 │
│  8. Move to the next todo item                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## 3. Branch Naming

Use one temporary branch per todo task:

```
feat/task-<phase>.<task>-<short-description>
fix/task-<phase>.<task>-<short-description>
```

Examples:
- `feat/task-5.1-platform-detection`
- `feat/task-17.1-proxy-config`
- `fix/task-3.3-registry-bug`

### Branch rules
- Always branch off the **latest `master`**.
- Keep the branch focused on **one task** — never mix multiple tasks in one branch.
- If you need to work on two tasks that depend on each other, finish and merge the first
  before starting the second.
- **Never push the temp branch** unless collaborating with other humans.

---

## 4. Commit Rules

### 4.1 Commit message format

Use [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <short summary>

<body — what and why, wrapped at 72 chars>
```

### 4.2 Commit types

| Type | Use for |
|------|---------|
| `feat` | A new feature, module, or capability |
| `fix` | A bug fix |
| `refactor` | Code change that doesn't alter behavior |
| `test` | Adding or updating tests |
| `docs` | Documentation only |
| `chore` | Build, tooling, dependencies, non-code |
| `style` | Formatting, whitespace, lint fixes |

### 4.3 Scope

The scope is the module or file being touched:

```
feat(client): add platform detection module
fix(server): correct heartbeat timeout handling
test(encryption): add GCM tamper tests
docs(todos): mark phase 5 complete
```

### 4.4 How to break work into commits

Within a single task branch, commit **after each logical step**:

| Step | Commit |
|------|--------|
| Create the module skeleton | `feat(client): add skeleton for nat_traversal module` |
| Implement the core logic | `feat(client): implement UDP hole-punching state machine` |
| Wire it into the system | `feat(client): integrate nat_traversal into tunnel_manager` |
| Add tests | `test(nat): add hole-punch success and timeout tests` |
| Run tests, fix failures | `fix(nat): handle socket timeout edge case` |

### 4.5 Commit hygiene

- **One logical change per commit.** Don't bundle unrelated edits.
- **Never commit generated files, secrets, or config with credentials.**
  Check `.gitignore` is up to date.
- **Never commit with `--no-verify`** — let tests/lints run.
- **Never amend or force-push** temp branches unless fixing a typo in your own unshared branch.
- If you catch a small mistake before merging, prefer a new small commit over rewriting history.

---

## 5. Testing Before Merge

Before merging, the task must pass its tests:

```bash
# Run the tests relevant to the task
python -m pytest tests/test_<task-file>.py -v

# Run the full unit suite (cheap, no root needed)
python -m pytest tests/ -v

# For TUN / root-dependent features, note they need --e2e and may be
# skipped in CI — say so in the merge message.
```

**If a test fails:** fix it on the same branch, add a commit, re-run.
**Never merge a branch with failing tests.**

---

## 6. Updating the TODO Files Before Merge

### 6.1 Mark completed tasks

Before merging, update the relevant todo file in `docs/todos/`:

```diff
- - [ ] 5.1 **`client/platform_detection.py`** — Platform capability detection
+ - [x] 5.1 **`client/platform_detection.py`** — Platform capability detection
```

Also update the **Summary table** in `TODO.md` if phase estimates changed.

### 6.2 Rename files that fully passed

If **every task in a todo file's phases** is complete AND all its tests pass,
rename the file by appending `passed` to the end of the **filename** (before `.md`):

```
docs/todos/00-foundation.md        →  docs/todos/00-foundation-passed.md
docs/todos/01-server.md            →  docs/todos/01-server-passed.md
```

### 6.3 Rules for renaming

| Condition | Action |
|-----------|--------|
| All tasks in the file complete + tests pass | Rename to `*-passed.md` |
| File renamed to `*-passed.md` | Include the rename in the **merge commit** |
| One or more tasks still open | **Do NOT rename.** Only mark the done checkboxes. |

### 6.4 Update the index

After a rename, update `TODO.md`:

- Point the phase links to the new `*-passed.md` filename.
- Optionally add a ✅ to the phase row.

---

## 7. Merging

### 7.1 Merge command

```bash
git checkout master
git pull --ff-only              # stay in sync (if remote exists)
git merge --no-ff feat/task-5.1
```

**Always use `--no-ff`** so the branch's commits stay grouped in history.

### 7.2 Merge commit message

```
Merge task 5.1: platform detection

- Implemented PlatformCapabilities detection (OS, root, TUN, Termux)
- Added degradation rules for missing capabilities
- Added test_platform_detection.py (7 tests, all passing)
- Updated docs/todos/02-client-vpn.md (5.1 marked done)
```

### 7.3 Delete the branch after merge

```bash
git branch -d feat/task-5.1       # safe delete (only if merged)
# If git refuses, the branch wasn't merged — investigate, don't force.
```

After merge, optionally:

```bash
git push origin master            # only if a remote exists and user asked
```

---

## 8. Checklist Before Each Merge

- [ ] Branch is up to date with latest `master`
- [ ] All task tests pass
- [ ] No failing tests on the branch
- [ ] Todo file updated: completed tasks marked `[x]`
- [ ] If fully passed: file renamed to `*-passed.md` and index updated
- [ ] Commit messages follow Conventional Commits
- [ ] No secrets, credentials, or generated files in the branch
- [ ] Branch will be merged with `--no-ff`
- [ ] Branch will be deleted after merge

---

## 9. Common Pitfalls

| Pitfall | Fix |
|---------|-----|
| Committing directly to `master` | Always `git checkout -b` first. |
| One huge commit with everything | Split into logical steps, commit after each. |
| Forgetting to update the todo file | Update `[x]` marks **before** the merge. |
| Renaming to `passed` too early | Only rename when ALL tasks in the file are done + tests pass. |
| Leaving temp branches behind | Delete after merge with `git branch -d`. |
| Merging with failing tests | Run tests first; fix on the branch. |
| Force-pushing / rewriting history | Never do it on shared branches. |
| Forgetting the summary table | Update `TODO.md` summary when estimates/status change. |
