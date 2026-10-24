"""Session memory: it summarises a chat and brings up relevant earlier chats in a new one."""
from airtac import agent, config
from plugins import recall


def test_a_chat_is_summarised_after_a_couple_of_turns():
    agent.chat("help me with binary search in python", "s-ds")
    agent.chat("now the two pointer technique", "s-ds")
    row = recall.related("I'm stuck on two pointers again", "s-new")
    assert row and ("two pointer" in row[0]["summary"].lower() or "binary" in row[0]["summary"].lower())


def test_earlier_chats_are_added_to_a_new_chat_but_never_the_current_one():
    agent.chat("plan my DBMS exam revision", "s-exam1")
    agent.chat("which units for the DBMS exam", "s-exam1")
    ctx = recall.earlier("remind me about my dbms exam plan", "s-exam2")
    assert "From earlier chats" in ctx["text"] and "dbms" in ctx["text"].lower()
    assert recall.earlier("dbms exam", "s-exam1") == {}          # its own chat isn't recalled


def test_app_chats_are_kept_out_of_general_recall():
    agent.chat("solve this leetcode problem", "app:practice")
    recall.update("app:practice")
    assert recall.related("leetcode", "s-x") == []


def test_the_tool_lists_past_chats():
    from airtac import tools
    agent.chat("research free GPUs for students", "s-gpu")
    agent.chat("compare colab and kaggle", "s-gpu")
    r = tools.REGISTRY["past_chats"].fixture("gpu")
    assert r.ok and ("gpu" in r.screen.lower() or "colab" in r.screen.lower())


def test_no_crash_with_too_few_turns():
    agent.chat("hi", "s-short")
    assert recall.related("anything", "s-other") == []
