# What AIRTAC Advance is built from

Licences as published by each project. Check again before you use anything commercially; models in particular can change terms between versions.

## Code AIRTAC uses
| Part | What for | Licence |
|---|---|---|
| Python, SQLite | language, database | PSF, public domain |
| FastAPI, Starlette, Uvicorn, Pydantic, httpx | the server | MIT / BSD |
| fpdf2 | PDFs | LGPL-3.0 |
| pypdf | reading PDFs | BSD |
| Pillow | diagrams | MIT-CMU (HPND) |
| python-docx, python-pptx, openpyxl | Word, PowerPoint, Excel | MIT |
| ddgs | DuckDuckGo search | MIT |
| psutil | system status | BSD |
| numpy | fast meaning search | BSD |
| winotify | Windows notifications | MIT |
| keyring | API keys in Windows Credential Manager | MIT |
| cryptography | the Passwords vault (scrypt + Fernet) | Apache-2.0 or BSD |
| Simple Icons (saved as SVG files, not a package) | app logos in `web/logos/` | CC0-1.0; the GitHub Copilot icon is MIT (Primer Octicons); brand logos remain trademarks of their owners |
| faster-whisper (optional) | speech to text on the PC | MIT |
| Piper / piper-tts (optional) | text to speech on the PC | GPL-3.0; each voice has its own licence |
| DejaVu fonts | PDFs | free licence (see assets/fonts) |

## Programs you install alongside
| Program | What for | Licence |
|---|---|---|
| Ollama | runs the models | MIT |
| rclone (optional) | cloud backup | MIT |
| Tailscale client (optional) | phone access | BSD-3 client, free personal plan. Headscale is a fully open server. |
| SearXNG (optional) | your own search engine | AGPL-3.0 |
| Docker + Strix (optional) | security scan of AIRTAC on your PC | Apache-2.0 |
| Claude Code, claude-mem, Agent Reach CLI (optional) | coding agent and helpers, set up from Dev tools | Claude Code: Anthropic's terms; claude-mem: Apache-2.0; Agent Reach: MIT |

## Used to build and check it (not needed to run it)
| Tool | What for | Licence |
|---|---|---|
| pytest | the 273 offline tests | MIT |
| Playwright + Chromium | phone-width page checks | Apache-2.0 / BSD |
| Bandit, pip-audit | security scans | Apache-2.0 |

## Models
| Model | Brain | Licence |
|---|---|---|
| Nemotron 3 Nano 4B (`nemotron-3-nano:4b`) | general (default), coding, reasoning | NVIDIA Open Model License: free open weights, commercial use allowed, not OSI open source |
| Nemotron 3 Super (Nebius, optional) | cloud brain for hard questions | NVIDIA Open Model License; runs on Nebius, so questions leave the PC |
| qwen3.5:4b / 2b | backup general brain | Apache-2.0 |
| FLUX.1 schnell (Nebius, optional) | AI illustrations | Apache-2.0 |
| qwen2.5-coder 1.5b / 7b | coding (optional) | Apache-2.0 |
| MedGemma 4B (GGUF by Unsloth) | medicine (optional) | Health AI Developer Foundations terms: open weights but not OSI open source; not for clinical use |
| nomic-embed-text | library search | Apache-2.0 |

## Planned
| Tool | Planned for | Licence |
|---|---|---|
| openWakeWord | wake word | Apache-2.0 code; pre-trained models CC BY-NC-SA (personal use only) |
| MediaPipe | vision and gestures | Apache-2.0 |
| scrcpy | phone screen | Apache-2.0 |
| yt-dlp | YouTube | Unlicense |
| python-telegram-bot | Telegram | LGPL-3.0 |
| Home Assistant | home | Apache-2.0 |
| stable-diffusion.cpp | image studio | MIT; each image model has its own licence |

## Data services added on 18 Oct 2026
| Service | What for | Terms |
|---|---|---|
| Open-Meteo forecast and geocoding APIs | weather in the greeting and Settings > Weather | free without a key for non-commercial use (fair use under 10,000 calls a day); data CC BY 4.0, credited as "Weather data by Open-Meteo.com" |
| Google News RSS (top stories, India edition) | "Top news" in the greeting | Google's feed for personal, non-commercial reading; headlines and links only |
| segno 1.6.6 | the pairing QR code | BSD-3-Clause |
