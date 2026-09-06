# 1)

> **Status update — 2026-09-06 (docs review, Claude Code):** PARTIAL. Items 1-2 are already marked DONE in this file and confirmed shipped: `auth.enabled` (config.yaml:567, `gate_auth.py`) gates `/query` via `require_session_or_token`, and role-based access exists in `utils/authn.py` (admin/operator/audit roles). Item 4 (container deployment) has since progressed well beyond "keep in mind" — `deploy/` now holds opt-in AppArmor/seccomp/Falco hardening profiles alongside the existing `Dockerfile`/`docker-compose.yml`. Items 3, 5, and 6 remain open exactly as marked (`*`): no Telegram encrypted-transport research doc, no per-regulation compliance deep-dive beyond `docs/THREAT_MODEL.md`, and no markdown corpus-creation wizard exists anywhere under `harness/` or `agentic/`.
>
> **What's left:**
> - Item 3: Telegram in-flight/at-rest encryption research (still just a personal note, no doc)
> - Item 6: a deliberately-basic markdown wizard for corpus authoring (not started; note also flags "don't over-build it")

- Don’t forget that curl requests or powershell api commands can still query cyclaw if on same lan - need to add authentication before truly considering this secure
> DONE

# 2)
- upon first launch account auto gen, learn how the rest works and make it more user friendly through web app and add rbac not just accounts 
> DONE — first-launch `admin` is still discard-hash + `cyclaw-user passwd admin`; login UI + Users panel on both consoles; roles admin/operator/audit. Not internet-safe.

# 3)
- Verify certs, in flight encryption, and *telegram* apparently once inside their network/servers I mean it may be encrypted but the point is it’s like meta where their ais resd it - tldr there were some shitty sounding workarounds otherwise just never use telegram for anything I think is worth money or stuff I’d put in keep pass (or do I use a different ;))
> *

# 4)
- It ultimately will run in a container (deploy/) but not necessarily something to do now but keep in mind and research a bit
> *

# 5)
- learn some of the very specific differences with the 3-5 compliance data protocols - last time i looked I was surprised the regulations weren’t perfectly aligned with what I’d consider secure or keeping data private; sometimes it’s preventing acccess despite that limiting the level of access/observability.. wasn’t anything that didn’t seem possible but def will need to carefully revisit at least some and know about more to not sound like an idiot haha - 
> *

# 6)
- consider a very basic markdown cmd/terminal wizard to make creating corpus rag files easier but don’t make it too good there’s no need for possible consulting - also it’s now confirmed the Python tech stack is not really unique; but I havent really see someone use langgraph the way I do. I genuinely don’t feel like I would have to use the NeMo guardrails at least with chat and tools but yeah it’s required for companies and has name recognition and throwback to a funny movie. wonder if that was the reason for the name of NeMo as ai guardrails haha. okay this should be good as a list to avoid random new features for no reason once the coding harness is done so further development is way cheaper (check when that year of Claude is over and if they have discounts like that still)
> *
