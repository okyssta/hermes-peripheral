---
name: peripheral
description: "Search what the user has seen on screen (screenshots, OCR, timeline)."
version: 1.0.0
author: kyssta
license: MIT
metadata:
  hermes:
    tags: [screen, screenshots, ocr, memory, recall]
---

# Screen memory

`peripheral` indexes the user's screenshots locally and lets you search the text
that was on them. Reach for it whenever the user refers to something they **saw**
instead of something they typed, pasted, or sent.

## Reach for it when

- "What was that error I got yesterday?" — `screen_recall(query="error", since="yesterday")`
- "The pricing page had a number on it…" — `screen_recall(query="pricing enterprise")`
- "What was I doing around 3?" — `screen_timeline(since="today")`
- "Which page was open when you changed my config?" — `screen_timeline` and read the
  `⚙` lines (agent tool calls) next to the screens.
- The user says "look at that screenshot" without attaching anything —
  `screen_recall(query=<distinctive words they used>, limit=5)` first, then
  `screen_show(id)` to read the whole screen, and only ask them to re-send if
  nothing matches.

## Do not reach for it when

- The user attached an image in this conversation: read that image directly.
- The question is about what the agent itself did: use the session history.
- The user asks about a file on disk they can name: read the file.

## Reading the results

Each hit shows `[id] time · title · chars · kind`, one quoted line around the
match, the image path and a thumbnail path. `screen_show(id)` returns the full
OCR text of one screen. If the meaning is in the layout rather than the words
(a chart, a UI state, a photo), pass the returned path to a vision tool instead
of relying on OCR.

## Honesty rules

- OCR is imperfect: quote what it returned, and say "the screen shows" rather
  than asserting what the user meant.
- If nothing matches, say so and suggest a distinctive term (an error code, a
  product name) — never invent a screen that is not in the index.
- The index only holds what was captured. If the user's screenshot folder is not
  being watched, say that instead of implying the screen never existed.
