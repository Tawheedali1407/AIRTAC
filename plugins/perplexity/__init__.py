"""Perplexity: answers from the web with their sources, through Perplexity's Sonar API.

Perplexity is a search engine with a model on top, so it is a tool here, not a brain: AIRTAC's brain
asks it when you want an up-to-date answer with sources ("ask Perplexity ...", "with sources").
Sonar can't call AIRTAC's tools, which is why it isn't in the brain failover.

It needs PERPLEXITY_API_KEY (paid per request; add it on the Connections page). Without the key the
tool says so; the Web page still searches for free with DuckDuckGo.
"""
from airtac import connections
from airtac.plugin_api import Tool, ToolResult, after, fetch, plugin, schema, unreachable

p = plugin(__name__)
API = "https://api.perplexity.ai/chat/completions"
MODEL = "sonar"


def _test(http) -> tuple[bool, str]:
    r = http.post(API, headers={"Authorization": f"Bearer {p.secret('PERPLEXITY_API_KEY')}"},
                  json={"model": p.setting("model", MODEL), "messages": [{"role": "user", "content": "Say OK."}], "max_tokens": 5})
    if r.status_code in (401, 403):
        return False, "Perplexity refused the key. Copy it again from perplexity.ai/account/api."
    if r.status_code in (402, 429):
        return True, "The key works, but the account is out of credits or busy right now."
    r.raise_for_status()
    return True, "Connected. Ask \"Ask Perplexity ...\" for answers with sources."


p.connector(id="perplexity", name="Perplexity (Sonar API)", order=22, logo="perplexity",
            get_key="https://www.perplexity.ai/account/api/keys",
            about="Up-to-date answers from the web, with their sources. Paid per request.",
            powers=["ask_perplexity: answers with sources"], test=_test,
            note="Perplexity Pro (the app) and the API are billed separately.",
            fields=[connections.Field("PERPLEXITY_API_KEY", "API key", placeholder="pplx-...")], group="Search and dev tools")


def _result(data: dict) -> ToolResult:
    try:
        text = (data["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError):
        return ToolResult("Perplexity sent an answer I couldn't read.", ok=False)
    links = [s.get("url", "") for s in data.get("search_results") or [] if str(s.get("url", "")).startswith("https://")]
    links = links or [u for u in data.get("citations") or [] if str(u).startswith("https://")]
    used = data.get("usage") or {}
    if used:
        from airtac import plugins
        plugins.report_usage("perplexity", p.setting("model", MODEL), used.get("prompt_tokens", 0), used.get("completion_tokens", 0))
    screen = text + ("\n\nSources:\n" + "\n".join(f"- {u}" for u in links[:6]) if links else "")
    first = text.split(". ")[0][:250]
    return ToolResult(f"Perplexity says: {first}. Sources are on screen.", screen)


def ask_fixture(question: str) -> ToolResult:
    return _result(p.sample("answer"))


def ask_live(question: str) -> ToolResult:
    data = fetch(API, method="POST", write=True, timeout=30,
                 headers={"Authorization": f"Bearer {p.secret('PERPLEXITY_API_KEY')}"},
                 json_body={"model": p.setting("model", MODEL), "max_tokens": 700,
                            "messages": [{"role": "system", "content": "Answer briefly and factually."},
                                         {"role": "user", "content": question[:2000]}]})
    if data is None:
        return unreachable("Perplexity")
    return _result(data)


p.add(Tool("ask_perplexity", "Ask Perplexity for an up-to-date answer from the web, with its sources.",
           schema({"question": {"type": "string", "description": "The question to ask"}}, required=("question",)),
           "read", ask_fixture, ask_live, needs=("PERPLEXITY_API_KEY",)))


def _args(text, _messages):
    return {"question": after(text, r"\b(ask\s+)?perplexity\b") or text}


p.mock_rule(21.5, ("perplexity", "with sources"), "ask_perplexity", _args)
