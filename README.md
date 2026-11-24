# Reclaim (`ReclaimWeave`) — The Agent Companion Layer

> **WHOOP for the life around agent work. Not a productivity tool. A reclamation tool.**

Agents now do the coding. The bottleneck moved: people hand work to an agent and then *watch it* — burning the very time the agent was supposed to free. **Reclaim** is the inverse product: an anti-attention companion layer that succeeds when you look *away*, guiding you toward movement, daylight, hydration, and real human conversation calibrated to your live WHOOP readiness, and measuring the trade honestly in a **Reclaimed Life Ledger**.

---

## Two Worlds, One Loop

| World | How It Works | Privacy Boundary |
|---|---|---|
| **Corporate (V0 — Zero IT)** | One tap: *"I handed work to the agent"* + rough window (5m / 15m / 30m). | **Zero employer integration, zero screen scraping.** `CorporateManualAdapter` and `ReclaimStore` forcibly strip `task_label` and `external_run_id` to `None` at the storage boundary. |
| **Personal (V1 — Connected)** | Replaceable harness adapters (`WeaveOS`, `Claude Code`, `Antigravity`, `Grokbot`, `Cursor`, `Muse`) delegate tasks and emit quiet state hooks (`running`, `waiting`, `done`, `blocked`). | Local-first SQLite WAL (`~/.reclaimweave/reclaim.sqlite`); `0600` OAuth token file; zero cloud telemetry or ads. |

---

## Core Architecture

- **Zero-Dependency Stdlib Core (`reclaimweave`):** Core models, SQLite WAL store, WHOOP Developer API v2 OAuth2 client, replaceable harness adapters, anti-attention redirect engine, social presence coordinator, quiet completion brief formatter, and reclamation ledger all run on Python 3.10+ stdlib alone.
- **WHOOP Developer API v2 (`reclaimweave/whoop.py`):**
  - Uses verified WHOOP v2 OAuth2 (`https://api.prod.whoop.com/oauth/oauth2/auth`, `/oauth/oauth2/token`) and `/developer/v2/{recovery,cycle,activity/sleep}` endpoints.
  - Lock-serialized refresh token rotation (`threading.Lock`) to avoid breaking single-use refresh chains.
  - Honors `recovery.deleted` and `sleep.deleted` webhooks per WHOOP storage terms.
  - Notes WHOOP's real-world API profile: free v2 REST API, 10-member starter sandbox cap, daily summary granularity, and strict non-medical-advice posture.
- **Anti-Attention Redirect Engine (`reclaimweave/redirect.py`):**
  - Emits at most **2–3 calm options** calibrated to WHOOP Recovery (`Red <34%` → gentle daylight/hydration/restoration; `Yellow 34–66%` → easy outdoor walk; `Green ≥67%` → brisk outdoor walk / real meal prep).
  - **Social-first:** prioritizes teammates who are *free right now* because their agents are also running.
  - **Environment-first:** biases toward outdoor, zero-carbon options and logs estimated avoided CO₂.
  - **Anti-fatigue cooldown:** refuses to nag if you were just prompted.
- **Quiet Ambient Surfaces (`reclaimweave/brief.py` & `reclaimweave/ui/index.html`):**
  - Replaces live streaming logs with **one quiet 2-sentence completion brief**, a `≤36`-char **2×2" Watch / 1×1" Charm** card, and an optional earbud voice brief.

---

## Quickstart

```bash
pip install -e ".[server]"

# Start the local companion & glanceable UI (with a realistic seeded week & free teammates)
reclaim serve --seed

# Or use the quiet CLI directly (zero-dependency core)
reclaim whoop --recovery 82 --strain 5.8 --sleep-debt 10 --since-move 60
reclaim handoff --world corporate --minutes 15
reclaim done --reclaimed 15 --stress 2
reclaim ledger
```

---

## Honest Measurement

Reclaim presents personal trends and Pearson associations across your logged days alongside an explicit disclaimer: **observed personal associations are suggestive — never causal proof and never medical advice.**
