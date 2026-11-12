---
name: charts
description: "Make charts and short animations of stats end to end: pick a true and interesting finding, gather the data and record where each number came from, compare models fairly, draw it as a plain bar, line or scatter, and render it with code you write. Use for /skill:charts, 'make a chart of', 'animate this data', 'do a batch of charts on this launch', or any chart or video meant for a post, a doc or a PR."
---

# Charts

Turn data into a chart someone can trust at a glance. You find the sources, pull the numbers and record where each came from, check them, write the render code, and produce the file. The human says what to look at, reviews, writes the caption and decides what goes out.

Adapted from Adam Holter's article ["How I make my AI charts and animations"](https://x.com/AdamHoltererer/status/2120174791982186506) (10 Nov 2026) and the generic skill in his [swipe-feed](https://github.com/adamholter/swipe-feed) repo (`skills/what-works-on-x`). His published output is at <https://adam.holter.com/animations/>. The rules are his experience of what holds up. Check them against your own reviewer's corrections.

## 1. Pick the finding

- Start from one claim the chart proves, in one sentence, such as "Gemini 4 Carbon at medium beats every model at high". If you can't write the sentence, there is no chart yet.
- The finding must be true and worth a second look: a gap people don't expect, a change over time, a ranking that contradicts the launch post. A chart that restates the headline number adds nothing.
- Use data the audience recognises. A benchmark nobody has heard of needs one line of context, or a different benchmark.

## 2. Gather the data

When the ask doesn't come with data, you find it. When it does, you still check it against its source.

- Find the sources. Prefer primary ones: the benchmark's own results, a lab's launch post or system card, a pricing page, or an aggregator that publishes its method (Artificial Analysis, Epoch AI). A news story or a post quoting a number is a pointer to the source, not the source.
- Pull the numbers by downloading the published file or table where one exists, rather than reading them off a page or an image. Keep the raw download unchanged in the piece's directory and write the filtering as code, so the step can be rerun.
- Record where each number came from: the URL, the retrieval date, the exact file, table or section, and the row or model id as the source writes it. Put this in a column of the data file or in `notes.md`.
- Record the conditions each number was measured under, such as effort level, harness, scorer, plan or date. The next step needs them.
- If the source lacks a number the finding needs, say so on the chart or drop that row. Never fill a gap from memory or a guess.

## 3. Make the comparison honest

A chart can be accurate in every number and still mislead. These are the rules for model comparisons.

- **One effort per model, and say which.** Plot medium or high, not max. Max usually buys a small gain over xhigh for a much higher cost and wall time, and nobody runs it day to day, so a max-vs-default chart exaggerates the gap. Pick one level and hold it across models. Where a model's top level is high (Gemini), high is the match, and the footnote says so. If you show the effort curve itself, show every level. Many aggregators publish only max for most benchmarks. Look for a table that has a row at one shared level for each model, and if none has, say on the chart that every bar is max.
- **Cost is what people pay.** Use the subscription plan price where people use the model through a plan. Use API price only when the audience pays per token, and label it.
- **Speed is time per task**, not tokens per second. Tokens per second ignores how many tokens a model spends, so it doesn't say how long the work takes.
- **Same conditions.** Same benchmark version, scorer, harness and tool access for every bar. If one row comes from a different setup, drop it or mark it on the chart.
- **Sample size and noise.** When two bars sit inside each other's error bars, say so or don't rank them.
- **Units.** Read every number back against the source before rendering: billions vs trillions, percent vs percentage points, per task vs per run. A unit slip is the mistake people catch in the replies.
- **Axes.** Bars start at zero. A line or scatter may zoom, with the axis labelled. Don't cut an axis to make a gap look bigger.

## 4. Draw it plainly

- Use a bar, a line or a scatter. If the source already has a clear chart, reuse its form and cite it.
- No flagpoles, gauges, dials, pictograms or other themed shapes. Theming makes a chart harder to read and doesn't make it more interesting.
- Don't write a title. Titles you generate read as filler, and the human writes the framing in the caption. A one-line axis or subtitle label that says what the number is ("ARC-AGI-2 score, High effort") is fine.
- Label values on the bars or points so nobody has to read the grid. Sort bars by value unless order carries meaning (time, size).
- Highlight the thing the finding is about, such as one model, with colour or weight, and keep everything else neutral.
- A footnote carries the source, the date, the effort level and any caveat from step 3. It is part of the chart, not optional.
- Size for where it goes. For a feed, square or 4:5, with text big enough to read on a phone.

## 5. Animate only when motion adds something

Animate in two cases only:

- the data changes over time or across a setting, and the motion shows that change; or
- you need to show several things in turn so one frame isn't cluttered.

Otherwise make a still. When you do animate:

- The first frame is the finished chart, every bar and label in place. Feeds and link previews show the first frame, and a viewer who never presses play still gets the point. The animation starts after it.
- Keep it short and end on the finished chart too. Most of Adam's published animations run 14 to 30 seconds.
- Export H.264 MP4 with `yuv420p` and an even width and height, which most players and feeds accept.

## 6. Render with code

There is no chart tool to pick. You write a small program that draws the chart and renders the file, and the program is the record of how the chart was made.

- Keep the data from step 2 in its own file (CSV or JSON) next to the render script, so a reviewer can check numbers without reading code.
- Any plotting library works: matplotlib, Vega-Lite, D3 with a headless browser, or SVG written by hand. For video, render frames and join them with ffmpeg, or use the library's animation writer.
- Render, then open the output and look at it. Check that labels don't overlap, nothing is clipped, the numbers match the data file, and the first frame of a video is the finished chart (`ffmpeg -i out.mp4 -frames:v 1 first.png`).
- Keep each piece in its own directory: the raw download, the data file, the render script, the output, and a `notes.md` with the one-sentence finding, the sources from step 2, and the caveats from step 3.

## 7. Work in batches

- A prompt like "do a batch on this launch post" means many pieces (Adam's batches are about 20), each a different finding, not ten views of one table. Vary the shape: ranking, change over time, cost vs score, a correction of a misleading claim.
- Ship each piece for review as soon as it renders. Don't hold the batch.
- Draft a caption for each, then expect it to be rewritten. A drafted caption states the finding in a few plain words. It does not hedge or restate the chart.
- One long-running thread for the batches keeps context on what has already been made, so the batches repeat less. Before starting a batch, list what earlier batches already covered.

## 8. Fold every correction back in

When the human corrects a piece, fix that piece, apply the same fix to the rest of the batch, and add a rule to the skill the agent reads, in the section the mistake belongs to, so the next batch doesn't repeat it. In a repo that means a line in the repo's own chart skill or rules file. If the correction contradicts a rule here, the human's correction wins, and the rule gets rewritten.

## When the chart is for X

Rules that come from posting on X, not from chart craft in general:

- X uses a video's first frame as its preview. This is the main reason for the finished-first-frame rule above, though the rule helps on any feed.
- A post gets about five words of attention. The chart carries the evidence and the caption carries the opinion, so the caption should be short and say something a reader can agree or disagree with. A ranking works because every placement is a claim.
- Volume beats polish. Most posts get little reach, and a post that misses costs almost nothing, so many honest pieces teach more than one perfect piece. Never trade honesty for reach to get there.
- Write from the owner's real view. Pitch only what they believe and would defend. swipe-feed's `what-works-on-x` skill covers posting in more depth.
- Post through the owner's review step, never straight from a batch. If posting is automated, check the profile before posting and never press Post twice.
