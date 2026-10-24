# Roadmap

The hackathon comes first (deadline 30 Oct 2026): until then AIRTAC Advance only needs installing and trying. After that, about 4 to 6 hours a week alongside college, in this order. Each phase ends with something you can show in an interview.

| Phase | When | What | Proof for your portfolio |
|---|---|---|---|
| 0 | now to 30 Oct | Install, pull nemotron-3-nano:4b, add your syllabus, a few books and your EduSkills internship. Use it daily; press 👎 when it's wrong. | It's your daily assistant |
| 1 | 31 Oct to 15 Nov | Private voice on the PC (faster-whisper and Piper), Windows notifications, a tidy app, and phone access over Tailscale | A short video: voice on the phone, answered by your own PC |
| 2 | 16 Nov to 31 Dec | Telegram plugin, email (read and draft), calendar import (ICS), better Work mode (drafts as Word or slides), GitHub writes with Confirm | A blog post: "my assistant's plugin system" |
| 3 | Jan 2027 | Faster library search: sqlite-vec index, PDF tables, Kiwix/WikiMed import, and an evaluation set of 50 questions to score answers | Numbers: answers right before and after |
| 4 | Feb 2027 | Phone control over ADB, wake word, desktop and music polish, and fine-tuning a small model on your 👍 answers (LoRA with Unsloth on a free Colab or Kaggle GPU, then load it in Ollama) | A model card for "AIRTAC-mini" |
| 5 | Mar to Apr 2027 | Vision (MediaPipe gestures, reading a photo), Home Assistant, plugin sandboxing (a separate process per plugin), and a security review | A GSoC-quality write-up and a public demo video |

## Ideas worth more than more features
- **Measure before you add:**
  - keep the eval set from phase 3,
  - run it after every change,
  - put the score in the README.
- **Contribute upstream:** a fix to Ollama, ddgs or MediaPipe docs counts for more in interviews than one more plugin.
- **Write about it:** one post per phase on LinkedIn and dev.to, linked from your GitHub README.
