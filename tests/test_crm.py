"""People and pipeline (Ali, 18 Oct: "schedule should be like CRM"): contacts, a board with stages, follow-ups that
go on the schedule (so the greeting says them), and notes and a history per person and item."""
from datetime import date, timedelta

from fastapi.testclient import TestClient

from airtac import agent
from airtac.main import app
from plugins import crm
from plugins.schedule import store

client = TestClient(app, base_url="http://localhost")
API = "/api/plugins/crm"


def test_contacts_items_board_and_history():
    priya = client.post(f"{API}/contacts", json={"name": "Priya Sharma", "role": "Recruiter", "org": "Acme",
                                                  "email": "priya@acme.example"}).json()
    assert priya["name"] == "Priya Sharma" and priya["history"][0]["kind"] == "created"
    assert client.post(f"{API}/contacts", json={"name": "X Y", "email": "not-an-email"}).status_code == 422
    it = client.post(f"{API}/items", json={"title": "Acme ML internship", "contact_id": priya["id"], "due": "2026-11-14"}).json()
    assert it["stage"] == "Lead" and it["contact"] == "Priya Sharma"
    b = client.post(f"{API}/items", json={"title": "Second", "stage": "Lead"}).json()
    board = client.post(f"{API}/items/{it['id']}/move", json={"stage": "Contacted"}).json()
    assert [i["title"] for i in board["columns"][1]["items"]] == ["Acme ML internship"]
    board = client.post(f"{API}/items/{b['id']}/move", json={"stage": "Contacted", "before": it["id"]}).json()
    assert [i["title"] for i in board["columns"][1]["items"]] == ["Second", "Acme ML internship"]
    detail = client.post(f"{API}/items/{it['id']}/activity", json={"kind": "email", "text": "Sent my resume"}).json()
    kinds = [h["kind"] for h in detail["history"]]
    assert kinds[:2] == ["email", "stage"] and detail["history"][1]["text"] == "Lead → Contacted"
    person = client.get(f"{API}/contacts/{priya['id']}").json()
    assert any(h["text"] == "Sent my resume" for h in person["history"]) and person["items"][0]["title"] == "Acme ML internship"
    assert client.get(f"{API}/contacts?q=acme recruiter").json()["contacts"][0]["id"] == priya["id"]


def test_stages_can_be_renamed_and_items_are_never_lost():
    it = crm.add_item({"title": "Club fest", "stage": "Waiting"})
    r = client.post(f"{API}/stages", json={"stages": ["Idea", "Doing", "Done", "doing"]}).json()
    assert r["stages"] == ["Idea", "Doing", "Done"]
    assert crm.item(it)["stage"] == "Idea"
    assert client.post(f"{API}/stages", json={"stages": ["Only", "  "]}).status_code == 422


def test_follow_ups_go_on_the_schedule_and_are_logged():
    cid = crm.add_contact({"name": "Rahul", "role": "Mentor"})
    soon = (date.today() + timedelta(days=2)).isoformat()
    person = client.post(f"{API}/contacts/{cid}/follow-up", json={"date": soon, "time": "17:30", "note": "ask about GSoC"}).json()
    f = person["follow_ups"][0]
    assert f["title"] == "Follow up: Rahul (ask about GSoC)" and f["time"] == "17:30"
    on_schedule = [e for e in store.list_events() if e["category"] == "follow-up"]
    assert on_schedule and on_schedule[0]["title"].startswith("Follow up: Rahul")
    assert client.get(f"{API}/contacts").json()["contacts"][0]["follow_up"]["date"] == soon
    assert client.post(f"{API}/contacts/{cid}/follow-up", json={"date": soon, "time": "5pm"}).status_code == 422
    client.post(f"{API}/follow-ups/{f['id']}/done")
    assert crm.follow_ups() == []
    assert crm.contact(cid)["history"][0]["text"].startswith("Done: Follow up: Rahul")


def test_the_chat_can_log_look_up_and_set_follow_ups():
    r = agent.chat("note for Ananya: met her at the AI meetup, she works on agents", "crm1")
    assert "Saved a note for Ananya" in r.spoken and "added Ananya" in r.spoken
    r = agent.chat("follow up with Ananya tomorrow", "crm1")
    assert "follow up with Ananya" in r.spoken, r.spoken
    r = agent.chat("who should I follow up with?", "crm1")
    assert "1 follow-up" in r.spoken
    look = crm.crm_lookup("ananya")
    assert "met her at the AI meetup" in look.screen


def test_deleting_a_person_keeps_their_items():
    cid = crm.add_contact({"name": "Old contact"})
    iid = crm.add_item({"title": "Their project", "contact_id": cid})
    assert client.post(f"{API}/contacts/{cid}/delete").json() == {"deleted": cid}
    assert crm.item(iid)["contact_id"] is None
    assert client.get(f"{API}/contacts/{cid}").status_code == 404
