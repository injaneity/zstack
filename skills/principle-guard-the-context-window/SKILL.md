---
name: principle-guard-the-context-window
description: "Apply when context is filling up: large outputs, long files, repeated reads. Keep summaries in the thread, not raw payloads; write bulk to a file and read back the slice you need."
---

# Guard the Context Window

**Why:** Context overflow degrades reasoning quality, creates compression artifacts, and halts progress.

**Pattern:**
- **Size phases and cap scope.** Limit files per phase, set turn budgets, account for mechanism costs.
