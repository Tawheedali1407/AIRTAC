# Logo credits

The logos in this folder come from **Simple Icons** 16.35.0 (<https://simpleicons.org>, <https://github.com/simple-icons/simple-icons>).
Simple Icons releases its icon data under **CC0 1.0** (public domain), except where an icon lists its own licence.
`scripts/make_logos.py` put each mark on a tile in its brand colour. AIRTAC never downloads a logo while it runs.

**The brand logos remain trademarks of their owners.** They are used here only to show you which app a
button opens. Using them doesn't mean any company endorses AIRTAC. Please follow each brand's own
guidelines if you publish screenshots.

## Icons with their own licence

- **GitHub Copilot** (`githubcopilot.svg`) comes from GitHub's Primer Octicons (<https://primer.style/foundations/icons/copilot-24>), under the MIT licence:

  > Copyright (c) GitHub Inc.
  >
  > Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:
  >
  > The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.
  >
  > THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

## Logo missing (the site's own icon, or a letter badge)

Simple Icons has no icon for these brands. AIRTAC shows the site's own favicon instead (fetched once from
that site and kept in data/favicons/), or a two-letter badge when there is none:

- **Apps:** LinkedIn, WPS Office, DigiLocker
- **Social:** LinkedIn
- **AI apps:** ChatGPT, Groq, Copilot, Grok, Notewise, Google AI Studio, Consensus, Manus, Emergent, Lovable, Bolt, Higgsfield, Runway, Leonardo, Gamma, Napkin, Canva
- **Brains and services:** Groq, Nebius, OpenAI, xAI, Tavily

If you add a logo yourself, make sure its licence lets you use it. Then save it here as `<name>.svg` and set `"logo": "<name>"` in `plugins/desktop/apps.json`.
