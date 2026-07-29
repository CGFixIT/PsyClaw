# Fixture usage

`GitHubCodingRunner` copies this tree to a temporary directory, overlays the
candidate's declared surfaces onto the copy, and checks every `FixtureCase`
against the copied files. Nothing here is imported, installed, or executed —
the files exist only as deterministic overlay targets and case content.

The nested `docs/` path doubles as fixture material: cases and surfaces can
exercise multi-segment relative paths (e.g. `docs/usage.md`) without the
committed tree ever being mutated.
