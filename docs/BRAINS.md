# Brains: which model answers what

Set in `config/airtac.toml`:
```toml
[llm]
model = "nemotron-3-nano:4b"   # the general brain: NVIDIA Nemotron 3 Nano 4B, about 2.8 GB
fallback = "qwen3.5:4b"        # used automatically if the model above isn't downloaded

[brains]
coding = ""               # empty = the general model with a coding prompt
reasoning = ""            # empty = the general model with thinking on
medical = ""              # e.g. "hf.co/unsloth/medgemma-4b-it-GGUF:Q4_K_M"
embed = "nomic-embed-text"
```
Why Nemotron: it's the same NVIDIA family as the hackathon version's cloud brain, it is small enough
for an 8 GB laptop, and it calls tools well. Its weights are free under the NVIDIA Open Model License
(free to use, change and share, but not an OSI open-source licence). Qwen 3.5 (Apache-2.0) stays as the backup.

Download with `scripts\models.ps1` (it picks for your RAM; add `-All` for the optional ones) or `ollama pull <name>`.

| Brain | Picked when the question has words like | What changes |
|---|---|---|
| general | anything else | tools, normal prompt |
| coding | code, python, bug, error, algorithm, leetcode, sql, or you're in the Practice, LeetCode or GitHub app | coding mentor prompt (idea, code, complexity) |
| reasoning | derive, calculate, circuit, voltage, torque, probability, physics, engineering | step-by-step prompt with units, thinking on |
| medical | symptom, fever, tablet, dose, disease, anatomy, pharmacology | safety prompt, no tools, library passages, "not medical advice" note |

## On an 8 GB laptop
- **One model is in memory at a time.** The installer sets `OLLAMA_MAX_LOADED_MODELS=1`. Switching brains costs a few seconds the first time.
- **Expect a few words a second.** A 4B model runs at roughly that speed on an i3, and thinking makes it slower. If it feels slow, set `model = "qwen3.5:2b"`.
- **Close heavy browser tabs before long answers.** The System app shows memory use.

## A brain in the cloud (optional)
Two ways, both need Nebius connected (Connections in the menu, or `NEBIUS_API_KEY` in `.env`):
- **One brain only:** `[brains] reasoning = "nebius:nvidia/nemotron-3-super-120b-a12b"`. Hard engineering and maths questions go to Nemotron 3 Super in the cloud; everything else stays on your PC. Without the key or the internet it falls back to the local model.
- **Everything:** `[llm] provider = "nebius"`. The whole assistant uses Nemotron 3 Super on Nebius Token Factory, like the hackathon version.

Your questions then leave the PC, so local stays the default.

## Free cloud brains with automatic failover
`[failover]` in config/airtac.toml lists free cloud services in order: Groq, Nebius, NVIDIA build, OpenRouter
(Gemini is available but left out: its free tier may train on your data). Each is used only when it's connected:
paste its key on the Connections page and press Test (or put GROQ_API_KEY and the rest in `.env`). A key saved
there takes effect on the next question, with no restart. When one answers HTTP 429 or 402
(out of free requests or credits), it rests for `cooldown_seconds` (or what the service says in Retry-After) and
the next one answers. A slow or broken one rests for two minutes. Ollama on your PC is always the last stop.
The Brains page shows each tier: ready, resting, or off, and how many answers it gave. Under each answer you see
which one replied.

A plugin can add its own provider with `p.brain_provider(...)` (docs/PLUGINS.md), so a new local engine or
service needs no change to the core.

## "Every free model"
No single PC runs every model at once. The router plus the catalog on the Brains page is the practical version:
- add a model with one command,
- give it a role,
- AIRTAC uses it when a question fits.
