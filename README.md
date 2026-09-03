# Deutsch-Heft A1

A single self-contained HTML page that turns a handwritten German A1 notebook into a
revision tool: clean typed-up lessons, the handwriting corrections named and explained,
self-marking flashcards, and a quiz.

**Open [`index.html`](index.html)** — no build step, no dependencies, no network calls.

## What's in it

| Section | |
|---|---|
| **Notizen** | Lektion 1–49, each with vocabulary, a sentence-structure theory block, "Mehr dazu" notes and a **Korrekturen** list naming every mistake on the original page |
| **100 Verben** | the notebook's own 100-verb list, with the `er/sie/es` form and stem changes filled in |
| **Übungsblatt** | the typed case-translation sheet, with answers |
| **Karteikarten** | flashcards for every noun, filterable by deck or by gender, keyboard-driven |
| **Quiz** | ~400 self-marking questions, per-lesson or all at once, with "repeat the wrong ones" |

Every lesson also ends with a six-question mini-quiz drawn from that lesson's pool.

## Getting around

The sticky bar carries a lesson picker — grouped 1–18 / 19–49 / reference, with `‹` `›`
to step through in order. It tracks whichever lesson is on screen as you scroll, and its
options are built from the document at load, so it can never drift from the content.
Typing a number while it has focus jumps straight there.

## Conventions

- Articles are colour-coded by gender throughout — <span>der</span> blue, <span>die</span> green,
  <span>das</span> amber, plural grey — matching the der/die/das chart the lessons were built from.
- Sentence boxes colour the slots: green subject, white verb, amber object, grey rest, blue question word.
- German first, English gloss second, everywhere.

## Gaps

Lektion 38 was never photographed. Lektion 23 and 45 have no notebook page and are reconstructed
from the course topic and from the lessons either side; both say so on the page.

## Notes

Light and dark themes both supported. Responsive down to phone width.
