---
name: peripheral
description: "Remember and search what was on the user's screen — screenshots and live screen captures, locally OCR'd."
version: 2.1.2
author: kyssta
license: MIT
metadata:
  hermes:
    tags: [screen, screenshots, capture, ocr, memory, recall, vision]
---

# Screen memory

`peripheral` gives you a memory of the user's screen: the screenshots already on the
machine, plus screens it captures itself — read locally with OCR, indexed with the
time they appeared, searchable by text and openable as an image.

Reach for it whenever the user refers to something they **saw** instead of something
they typed, pasted, or sent — or asks what is on their screen right now.

## Reach for it when

- "What was that error I got yesterday?" — `screen_recall(query="error", since="yesterday")`
- "Look at my screen / what does this say?" — `screen_capture()` — it photographs the
  display, OCRs it, and (on a vision model) attaches the image for you to read.
- "The pricing page had a number on it…" — `screen_recall(query="pricing enterprise")`
- "What was I doing around 3?" — `screen_timeline(since="today")`
- "Which page was open when you changed my config?" — `screen_timeline` and read the
  `⚙` lines (agent tool calls) next to the screens.
- "Forget that screen" — `screen_forget(id=…)` or `screen_forget(all=true)`.

## Do not reach for it when

- The user attached an image in this conversation: read that image directly.
- The question is about what you did: use the session history.
- The user asks about a file on disk they can name: read the file.

## Reading the results

Each hit shows `[id] time · chars · title`, a quoted line around the match, and the
image path. A `· live` marker means Peripheral captured that screen itself; `· no text`
means OCR found nothing readable there (a photo, a chart, a blank frame) and the image
is the only evidence.

`screen_show(id)` returns the full OCR text **and the image itself** when the active
model can see images. When the meaning is in the layout — a chart, a dialog, a state —
look at the attached image rather than trusting the OCR.

## When the search comes back partial

`screen_recall` ANDs the words you pass. If no screen held all of them, the answer
starts with *"No screen held every word of …"* and lists the screens that share some,
each marked `· matched: <words>`. Treat that as a lead, not as the answer: say it is a
partial match, then open the screen with `screen_show` before quoting anything.

Time words `today`, `yesterday`, `tomorrow`, `this week`, weekdays, `2h`, `3 days`,
`an hour ago` and `2026-11-04` all parse. If a bound you passed could not be parsed,
`screen_recall` says so in the result — repeat that caveat instead of presenting an
unfiltered answer as if it were windowed.

## Honesty rules

- OCR is imperfect: quote what it returned, and say "the screen shows" rather than
  asserting what the user meant.
- If nothing matches, say so and suggest a distinctive term (an error code, a product
  name). Never invent a screen that is not in the index.
- Never describe a screen you have not opened in this turn: call `screen_show` first.
- If capture is unavailable (headless machine, no capture binary), say that plainly —
  `screen_capture` returns the reason, and `/peripheral status` shows readiness.
- Screen memory is private: do not paste raw OCR of unrelated screens into an answer,
  and honour a request to forget immediately.
