"""LeetCode: the daily problem, your stats and practice picks. Public data: no key needed."""
import random
import re

from airtac.plugin_api import SAMPLE_TAG, Tool, ToolResult, fetch, plugin, schema, unreachable

p = plugin(__name__)

GRAPHQL = "https://leetcode.com/graphql"
HEADERS = {"Content-Type": "application/json", "Referer": "https://leetcode.com", "User-Agent": "AIRTAC"}

# Spoken shortcuts -> LeetCode topic names.
TOPIC_ALIASES = {"dp": "dynamic programming", "graphs": "graph", "trees": "tree", "arrays": "array",
                 "strings": "string", "heaps": "heap", "linked lists": "linked list"}


def _daily_result(q: dict, tag: str = "") -> ToolResult:
    spoken = f"Today's LeetCode problem is {q['title']}, a {q['difficulty']} problem on {', '.join(q['topics'][:2])}."
    screen = (f"LeetCode daily {q.get('date', '')} {tag}\n{q['title']} ({q['difficulty']})\n"
              f"Topics: {', '.join(q['topics'])}\n\n{q.get('summary', '')}\n"
              f"https://leetcode.com/problems/{q['slug']}/")
    return ToolResult(spoken, screen)


# --- leetcode_daily

def leetcode_daily_fixture() -> ToolResult:
    return _daily_result(p.sample("leetcode")["daily"], SAMPLE_TAG)


def daily_data() -> dict | None:
    """Today's problem, or None when LeetCode doesn't answer (the offline tests use the recorded one)."""
    from airtac import config
    if config.OFFLINE:
        return p.sample("leetcode")["daily"]
    query = ("query { activeDailyCodingChallengeQuestion { date question { title titleSlug difficulty "
             "topicTags { name } } } }")
    data = fetch(GRAPHQL, method="POST", headers=HEADERS, json_body={"query": query})
    try:
        d = data["data"]["activeDailyCodingChallengeQuestion"]
        q = d["question"]
    except (KeyError, TypeError):
        return None
    return {"date": d["date"], "title": q["title"], "slug": q["titleSlug"],
            "difficulty": q["difficulty"], "topics": [t["name"] for t in q["topicTags"]]}


def leetcode_daily_live() -> ToolResult:
    d = daily_data()
    return _daily_result(d) if d else unreachable("LeetCode")


# --- leetcode_profile

def _profile_result(username: str, solved: dict, ranking, tag: str = "") -> ToolResult:
    total = sum(solved.values())
    spoken = (f"You've solved {total} problems: {solved.get('Easy', 0)} easy, {solved.get('Medium', 0)} medium "
              f"and {solved.get('Hard', 0)} hard.")
    screen = f"LeetCode profile: {username} {tag}\nSolved: {total}\n" + \
             "\n".join(f"  {k}: {v}" for k, v in solved.items()) + f"\nRanking: {ranking}"
    return ToolResult(spoken, screen)


def leetcode_profile_fixture() -> ToolResult:
    d = p.sample("leetcode")["profile"]   # not "p": that is this plugin
    result = _profile_result(d["username"], d["solved"], d["ranking"], SAMPLE_TAG)
    result.screen += "\nRecent: " + ", ".join(d["recent"])
    return result


def username() -> str:
    """The name saved on the Accounts page, else config [plugins.leetcode] user."""
    from airtac import memory
    return memory.kv_get("accounts", "user:leetcode") or p.setting("user", "MMDTAWHEEDALI")


def profile_data() -> dict | None:
    """{'username', 'solved': {'Easy': n, 'Medium': n, 'Hard': n}, 'ranking'} from LeetCode, or None."""
    query = ("query($u: String!) { matchedUser(username: $u) { username profile { ranking } "
             "submitStatsGlobal { acSubmissionNum { difficulty count } } } }")
    data = fetch(GRAPHQL, method="POST", headers=HEADERS,
                 json_body={"query": query, "variables": {"u": username()}})
    user = ((data or {}).get("data") or {}).get("matchedUser")
    if not user:
        return None
    solved = {s["difficulty"]: s["count"] for s in user["submitStatsGlobal"]["acSubmissionNum"] if s["difficulty"] != "All"}
    return {"username": user["username"], "solved": solved, "ranking": user["profile"]["ranking"]}


def leetcode_profile_live() -> ToolResult:
    d = profile_data()
    if d is None:
        return unreachable("LeetCode")
    return _profile_result(d["username"], d["solved"], d["ranking"])


# --- leetcode_pick

def _normalize(topic: str, difficulty: str) -> tuple[str, str]:
    topic = (topic or "").lower().strip()
    return TOPIC_ALIASES.get(topic, topic), (difficulty or "").capitalize().strip()


def _pick_result(choices: list[dict], topic: str, difficulty: str, tag: str = "") -> ToolResult:
    label = " ".join(x for x in (difficulty.lower(), topic) if x) or "practice"
    if not choices:
        return ToolResult(f"I couldn't find a {label} problem. Try another topic or difficulty.")
    q = random.choice(choices)
    # Say the difficulty once: "a medium dynamic programming problem", not "a Medium medium ...".
    kind = " ".join(x for x in (q["difficulty"].lower(), topic) if x)
    return ToolResult(f"Try {q['title']}. It's a {kind} problem.",
                      f"Practice pick {tag}\n{q['title']} ({q['difficulty']})\nTopics: {', '.join(q['topics'])}\n"
                      f"https://leetcode.com/problems/{q['slug']}/")


def leetcode_pick_fixture(topic: str = "", difficulty: str = "") -> ToolResult:
    topic, difficulty = _normalize(topic, difficulty)
    choices = [q for q in p.sample("leetcode")["problems"]
               if (not topic or topic in q["topics"]) and (not difficulty or q["difficulty"] == difficulty)]
    return _pick_result(choices, topic, difficulty, SAMPLE_TAG)


def leetcode_pick_live(topic: str = "", difficulty: str = "") -> ToolResult:
    topic, difficulty = _normalize(topic, difficulty)
    filters = {}
    if difficulty:
        filters["difficulty"] = difficulty.upper()
    if topic:
        filters["tags"] = [topic.replace(" ", "-")]  # LeetCode tag slugs use dashes
    query = ("query($f: QuestionListFilterInput) { problemsetQuestionList: questionList(categorySlug: \"\", "
             "limit: 30, skip: 0, filters: $f) { data { title titleSlug difficulty paidOnly: isPaidOnly topicTags { name } } } }")
    data = fetch(GRAPHQL, method="POST", headers=HEADERS, json_body={"query": query, "variables": {"f": filters}})
    rows = (data or {}).get("data", {}).get("problemsetQuestionList", {}).get("data")
    if rows is None:
        return unreachable("LeetCode")
    choices = [{"title": r["title"], "slug": r["titleSlug"], "difficulty": r["difficulty"],
                "topics": [t["name"].lower() for t in r["topicTags"]]} for r in rows if not r["paidOnly"]]
    return _pick_result(choices, topic, difficulty)


p.add(Tool("leetcode_daily", "Get today's LeetCode daily challenge problem.",
              schema(), "read", leetcode_daily_fixture, leetcode_daily_live))
p.add(Tool("leetcode_profile", "Get the user's LeetCode stats: problems solved by difficulty and ranking.",
              schema(), "read", leetcode_profile_fixture, leetcode_profile_live))
p.add(Tool("leetcode_pick", "Suggest one LeetCode practice problem by topic and difficulty.",
              schema({"topic": {"type": "string", "description": "e.g. dynamic programming, graph, array"},
                      "difficulty": {"type": "string", "enum": ["Easy", "Medium", "Hard", ""]}}),
              "read", leetcode_pick_fixture, leetcode_pick_live))


# ---------------------------------------------------------------- app page and Home numbers

def _profile() -> dict | None:
    """Your real profile, or None when LeetCode doesn't answer (the offline tests use the recorded one)."""
    from airtac import config
    if config.OFFLINE:
        d = p.sample("leetcode")["profile"]
        return {"username": d["username"], "solved": d["solved"], "ranking": d["ranking"]}
    return profile_data()


@p.dashboard
def page(pg: dict) -> None:
    prof = _profile()
    if prof:
        solved = prof["solved"]
        pg["stats"] = [{"label": "Solved on LeetCode", "value": sum(solved.values())},
                       {"label": "Ranking", "value": f"{prof['ranking']:,}"}]
        pg["bars"] = {"title": "Solved by difficulty",
                      "items": [{"label": k, "value": solved.get(k, 0)} for k in ("Easy", "Medium", "Hard")]}
    else:
        pg["note"] = (f"LeetCode didn't answer for {username()}. Check the username on the Accounts page "
                      "(it's the name in your profile address), or try again in a minute.")
    daily = daily_data()
    pg["lists"] = [{"title": "Today's problem", "empty": "LeetCode didn't answer. Try again in a minute.",
                    "items": [{"title": daily["title"], "meta": f"{daily['difficulty']}, {', '.join(daily['topics'])}",
                               "url": f"https://leetcode.com/problems/{daily['slug']}/"}] if daily else []}]


@p.progress
def numbers() -> dict | None:
    prof = _profile()
    if not prof:
        return None
    solved = prof["solved"]
    return {"order": 20,
            "hero": {"label": "Solved on LeetCode", "value": sum(solved.values()),
                     "sub": f"{prof['username']}, ranking {prof['ranking']:,}"},
            "bars": {"title": "By difficulty",
                     "items": [{"label": k, "value": solved.get(k, 0)} for k in ("Easy", "Medium", "Hard")]}}


# ---------------------------------------------------------------- rules for the no-model mode

def _pick_args(text, _messages):
    low = text.lower()
    topics = ["dynamic programming", "dp", "graph", "tree", "array", "string", "binary search", "two pointers",
              "heap", "stack", "linked list", "sliding window", "greedy", "bfs", "dfs", "hash table", "sorting"]
    return {"topic": next((t for t in topics if re.search(rf"\b{t}\b", low)), ""),
            "difficulty": next((d.capitalize() for d in ("easy", "medium", "hard") if d in low), "")}


p.mock_rule(19, ("leetcode profile", "leetcode stats", "my leetcode", "how many problems"), "leetcode_profile")
p.mock_rule(20, ("pick", "suggest", "practice", "practise"), "leetcode_pick", _pick_args)
p.mock_rule(21, ("leetcode", "daily problem", "problem of the day"), "leetcode_daily")
