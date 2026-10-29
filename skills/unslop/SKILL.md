---
name: unslop
description: Cut AI tells from any writing, including PR descriptions, commit messages, docs, and replies.
---

# Unslop

Scan for these patterns and rewrite. Keep the meaning and the intended tone.

**Words**
- AI vocabulary: additionally, crucial, delve, enhance, foster, garner, intricate, landscape, pivotal, showcase, tapestry, testament, underscore, vibrant. Use plain words.
- Fancy verbs: "serves as", "stands as", "boasts" become "is" or "has". "Utilize" and "leverage" become "use", "facilitate" becomes "help".
- Metaphor nouns: substrate, wedge, vector, nexus, primitive, harness, surface, bedrock, scaffolding, paradigm, north star, flywheel, endgame. Use the concrete word.
- Filler: "in order to" becomes "to", "due to the fact that" becomes "because". Cut "it is important to note that".
- Adverbs propping up a weak verb. "Significantly improves" becomes the measured delta.
- Vague attributions such as "experts believe". Name the source or cut it.

**Structure**
- "Not just X, but Y." Say the point directly.
- Forced groups of three. Use the natural number.
- Synonym cycling. Pick one name for a thing and repeat it.
- Trailing -ing phrases: "...ensuring reliability". Cut them or make them a sentence.
- Dense sentences the reader has to reread. Split them, one idea each.
- Passive voice that hides the actor. "The file is parsed by the loader" becomes "the loader parses the file".
- Over-compression: arrows, dropped articles, and verbless fragments. Write whole sentences.
- Mannered prose: aphorisms, personified code, figurative verbs. Say it literally.

**Saying something**
- Name the mechanism or the number, not the feeling. "Types that follow your schema" becomes "a column rename fails the build".
- If a sentence could appear unchanged in another project's docs, it says nothing. Cut it.
- No generic conclusions such as "the future looks bright".

**Formatting**
- No em dashes or en dashes. Use a period or a comma.
- Colons only before a list or an example, never as a mid-sentence connector.
- Sentence-case headings. Straight quotes. No decorative emoji.
- Bold sparingly. A bold label that restates its line ("**Performance:** Performance improved") becomes prose.

**Chat tells**
- "I hope this helps", "Certainly!", "Great question!", "You're absolutely right". Cut them and answer directly.
- Stacked hedges: "could potentially possibly" becomes "may".
