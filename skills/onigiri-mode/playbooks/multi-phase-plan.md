### Multi-phase or multi-PR plan

**You own the plan and then the code, one PR at a time. The plan is a checklist you run box by box and the operator audits from the evidence.**

1. When the change is one or two files with an obvious approach, skip the plan. Say so and stop.
2. Settle open questions by prototype before you write. Run `playbooks/experiment.md` for each. Keep the branch, the SHA, and the screenshots for Appendix A. Ask the operator only about a product or preference call that no run can settle. Give options.
3. Explore in this thread, one area at a time, writing notes to a scratch file so the context stays small. Each area's note records file pointers, conventions, test commands, and entry points. No inlined dumps in the plan.
4. Copy the skeleton below into the plan file and fill every placeholder. Unless the operator names a path, write the file under the agent store's `docs/`. Keep every heading and sub-block in the order shown. One section per PR. One PR is one change with its own evidence.
5. Write with the **technical-writing** skill in full, then the **unslop** skill. The body is one Diátaxis mode, how-to. Appendices hold explanation and reference. Each heading states the task or the finding.
6. Check the plan against the shape below and fix every gap.
7. Hand back. Post the plan path and what step 6 found, then stop. Execution starts on the operator's explicit go.

**Verification.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked. Every verification block opens with that sentence. The live block is mandatory: it names the surface, how to drive it, and the screen state or log line that proves the behavior. Drive it yourself; a live check is one box with a concrete scenario, the screenshot or output it saves, and its pass predicate. Keep the check count to what the change actually touches. One check runs the same load-bearing scenario on trunk and head, so a regression is visible; if trunk lacks the feature, record that and check the behavior the diff adds instead. The perf block names the metric, how it is measured at both trunk and head, and the number that fails. A PR that changes an interaction is review-gated: the operator reviews it in chat with screenshots before merge. A PR that changes no interaction writes `**Review gate.** None. <PR id> is not review-gated.`

**Driving the surface.** Pick the tool by surface. Browser and web UIs get whatever the repo uses to drive a browser. CLIs and TUIs get the repo's own harness. Native mobile gets whatever simulator-driving the repo has. A PR that touches two surfaces gets a live check on each. Its live check still names how each scenario is checked. A surface with no way to drive it is a risk in Appendix C, and Its live check still names how each scenario is checked.

````markdown
# <Program> plan

<Under ten lines. What changes, for whom, the rule the program enforces, and the PR ids in order.>

## How to read this

One box is one unit of work. Every box names the evidence that checks it. A nested box is a sub-step of the box above it. Check a box only when its evidence exists: a file, a log line, a screenshot, a test run, or a SHA. The body is a how-to. The appendices explain and record.

Work runs in one thread. PRs land in the order below, one at a time. <Which PR ids stop at merge-ready for the operator.>

Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

## Program checklist

### Arm the program

- [ ] State the protocol and this plan to the operator, then stop. Start execution only on the operator's explicit go.
- [ ] Read these from trunk at program start: the execution playbook, `playbooks/opening-a-pr.md`, and each skill the program uses.

### Order

- [ ] <PR id> and <PR id> are independent and first. Both branch from `main`.
- [ ] <PR id> after <PR id> merges.
- [ ] Hold the file boundaries. <PR id or class> touches only `<glob>`.
- [ ] Hold the review gate. <PR ids> change an interaction. They wait for the operator's review in chat with screenshots before merge.

### PR mechanics, for every PR

- [ ] Open the PR ready, never draft, per **Opening a PR**. `gh pr create --base <base-branch>`, or the repo's own tool when it has one.
- [ ] Run the repo's lint and typecheck once before the PR-facing push. Push with hooks on.
- [ ] Triage every Bugbot and security-reviewer comment per `../references/bugbot-triage.md`.
- [ ] Rebase onto current trunk before the code-ready report and before babysitting. Rebase again only at merge prep, on a `git merge-tree` conflict with trunk, on a CI failure that comes from a change on trunk, or when a known concurrent change to the same files lands.

### Verdict and merge, for every PR

- [ ] Run the unit, live, and perf boxes at the code-ready head. A new head gets a fresh pass over the boxes whose evidence the change invalidates.
- [ ] Judge every finding by the playbook's blocking rule. The verdict is clean when none blocks. A failure on trunk behavior in the regression check is recorded against trunk and does not block the PR.
- [ ] <The merge or handoff rule, with the patch-id rule from `playbooks/shipping.md`.>

## <Task as a verb phrase> (<PR id>)

**Depends on.** <PR id, or None.>

**Files.**

- [ ] Edit `<path>`.
- [ ] Create `<path>`.
- [ ] Delete `<path>`.

**Build.**

- [ ] <One change. Name the symbol and the file.>

**You see.**

- [ ] <One observable result, with the exact log line or screen state.>

**Verify, unit.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] <Test file and the case it gains.> Run `<command>`.

**Verify, live.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] Check 1. Regression against trunk. Run <the same load-bearing scenario> at trunk and head. If trunk lacks the feature, record that and check <the behavior the diff adds plus the end state the user waits for>. Save `<slug>.png`. Pass when <predicate>.
- [ ] Check 2. <Scenario.> Save `<slug>.png`. Pass when <predicate>.
- [ ] Check 3. <Scenario.> Save `<slug>.png`. Pass when <predicate>.

**Verify, perf.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] Metric. <What is measured at both trunk and head.>
- [ ] Probe. <The command or procedure, run at trunk and at the head.>
- [ ] Baseline. Record the trunk <value> first.
- [ ] Rule. <Head against trunk, with the number that fails. If the scenarios differ, use absolute budgets instead of an invalid ratio.>

**Review gate.** The operator reviews before merge.

- [ ] Copy check <n> screenshots into `<media path>/<pr-id>-review-<slug>.png`.
- [ ] Post the screenshots in chat. Stop at merge-ready. Wait for the operator's click.

**Merge.**

- [ ] Clean verdict at the exact head SHA.
- [ ] Bugbot triage done.
- [ ] Rebased onto current trunk after the verdict, patch-id unchanged.
- [ ] <The squash-merge that lands it.>

## Close the program

- [ ] Every box above is checked with its evidence.
- [ ] Reply to the operator with what landed, what is merge-ready, and what stays open.

## Appendix A. Prototype evidence

<Each open question a prototype answered, with the branch, the SHA, and the artifact links. Each question that stays unproven.>

## Appendix B. Alternatives rejected

<Each approach weighed and why it lost.>

## Appendix C. Risks

<Each risk with the PR it lands in and what you watch.>

## Appendix D. Links and reading list

<Docs to read before editing. The skill each PR uses. The trail per `show-me-your-work`.>
````

**Reply:** the plan path, the PR ids with their dependencies and the review-gated set, what the prototypes proved and what stays unproven, and what step 6 found.
