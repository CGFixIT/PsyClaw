---
description: Suggest the single highest-value next action after completing a task or at the end of a session. Use when asked "what should I do next?", "what's next?", "next steps?", or to surface a logical continuation point.
---

Suggest the single highest-value next action given the current state. $ARGUMENTS

Recommend the single highest-value next action the user could take.

## Rules

- Ground the suggestion in conversation context and whatever was just accomplished.
- The recommendation must be specific and immediately actionable — not a generic platitude.
- Consider what naturally follows from the work that was completed.
- Identify the current bottleneck or logical continuation point.
- Ensure the action is executable right now given the current state.

## Format

One concise, direct suggestion. No preamble, no alternatives, no hedging.

## Notes

- Exactly one suggestion, not a list of options.
- If nothing meaningful naturally follows, say so rather than inventing busywork.
