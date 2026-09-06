# Terminal UX Next Steps

> **Status update — 2026-09-06 (docs review, Claude Code):** PARTIAL. The doc's own "Current Patch" banner (2026-07-19) already confirms the CSS-token/`AbortController`/Firefox-scrollbar/reduced-motion work landed in `static/terminal.html` — spot-checked: `static/terminal.js` still uses `AbortController` (not the removed `AbortSignal.timeout()`), and `static/terminal.html` defines `scrollbar-width`/`scrollbar-color` and `prefers-reduced-motion` rules. The "Next Web Interface Pass" section (cross-browser visual QA, keyboard-traversal checks, screenshot capture) is manual UX work with no automated test to verify against, and nothing in `tests/test_terminal_contract.py` (which only checks the route/POST-path contract) covers it — so it remains an open, unverified backlog exactly as the doc's own banner says.
>
> **What's left:**
> - Run the manual cross-browser visual-smoke pass (§"Next Web Interface Pass" items 1-2: Chrome/Edge/Firefox/Brave, desktop/tablet/phone widths).
> - Do the keyboard-traversal QA pass (item 5: toolbar traversal, Escape, Shift+Enter, confirmation focus trap) and check off the Acceptance Checklist items against real browsers.

> **Status (2026-07-19):** the **Current Patch** list below has **landed** in `static/terminal.html` — verified: shared control tokens (`--control-height`, `--radius`, `--radius-pill`), wrapping ops toolbar (`flex-wrap`), `AbortController` request timeouts (`AbortSignal.timeout()` fully removed, abort timer cleared on every exit path), Firefox scrollbar styling (`scrollbar-width` / `scrollbar-color`), and `prefers-reduced-motion` handling — all with zero added dependencies. The **Next Web Interface Pass** items below remain an open *manual* visual-QA backlog (cross-browser screenshots, keyboard traversal checks).

## Current Patch

- Keep the existing dark amber/green terminal identity.
- Normalize toolbar, action, confirmation, and send controls around shared sizing and rounded control tokens.
- Let the ops toolbar wrap instead of squeezing or overflowing on narrow browser widths.
- Use `AbortController` for request timeouts so the terminal is not tied to `AbortSignal.timeout()` support.
- Add Firefox scrollbar styling and reduced-motion handling without adding dependencies.

## Next Web Interface Pass

1. Run a visual smoke pass in Chrome, Edge, Firefox, and one Chromium-based browser such as Brave.
2. Capture desktop, tablet-width, and phone-width screenshots of:
   - empty terminal
   - one answer with sources expanded
   - confirmation prompt
   - each ops console opened
3. Promote repeated controls into tiny local CSS groups only if more terminal UI is added.
4. Replace remaining inline width styles in console form fields once each panel gets a deliberate form layout.
5. Add keyboard checks for toolbar traversal, Escape close, send, Shift+Enter newline, and confirmation focus trapping.

## Acceptance Checklist

- Toolbar buttons wrap without horizontal scroll at 360px width.
- Input bar remains usable at 360px width.
- Sources and proposal previews scroll in Firefox and Chromium.
- Focus rings are visible on buttons, inputs, selects, and source toggles.
- No network call waits forever when the gateway or ops route stalls.
- No new frontend dependency is required.

## Non-Goals

- Do not rewrite the terminal into a framework app yet.
- Do not change the gateway API contract for UI polish.
- Do not weaken the soul, sync, agentic, filesystem, or SQL gates for convenience.
