"""Work mode: plans, steps, the report at the end, Confirm for write steps, Stop, skills and the API."""
import json
from pathlib import Path

from fastapi.testclient import TestClient

from airtac import agent, config, memory, work
from airtac.llm import LLMReply
from airtac.main import app

client = TestClient(app, base_url="http://localhost")
GOAL = "Research free GPU options for students and write me a one-page PDF"


class FakeLLM:
    """Answers the planning prompt with `plan` and the writing prompt with `report`."""

    def __init__(self, plan: dict, report: str = "## Summary\nColab and Kaggle give free GPUs [1].\n## Sources\n[1] Sample, https://nebius.com/"):
        self.plan, self.report, self.prompts = plan, report, []

    def chat(self, messages, tools, max_tokens=600):
        prompt = messages[-1]["content"]
        self.prompts.append(prompt)
        if "Reply with JSON only" in prompt:
            return LLMReply(text="Here is the plan:\n" + json.dumps(self.plan))
        return LLMReply(text=self.report)


def test_a_job_without_a_model_ends_in_a_report_and_files_only_on_convert():
    job = work.run_now(GOAL)
    assert job["status"] == "done", job
    assert [s["status"] for s in job["steps"]] == ["done", "done", "done", "done"]
    assert job["steps"][1]["args"]["url"] == "https://nebius.com/"
    assert job["report"] and not job.get("file") and not (config.OUTPUTS_DIR / "work").exists()   # no PDF by itself
    assert "convert" in job["spoken"].lower()
    url = client.post(f"/api/work/{job['id']}/convert/pdf").json()["file"]
    pdf = config.OUTPUTS_DIR / "work" / Path(url).name
    assert pdf.read_bytes().startswith(b"%PDF") and pdf.with_suffix(".md").exists()
    assert client.get(url).status_code == 200
    for fmt in ("docx", "pptx"):
        assert client.post(f"/api/work/{job['id']}/convert/{fmt}").json()["file"].endswith("." + fmt)
    assert client.post(f"/api/work/{job['id']}/convert/exe").status_code == 422
    from plugins.web import convert_report
    assert convert_report("word").ok
    notes = memory.notifications_after(0)
    assert notes[-1]["plugin"] == "work" and notes[-1]["title"].startswith("Work mode:")


def test_a_job_with_a_model_uses_its_plan_and_its_writing():
    llm = FakeLLM({"title": "GPU options", "steps": [
        {"title": "Search", "tool": "web_search", "args": {"query": "free GPU students"}},
        {"title": "Delete everything", "tool": "rm_rf", "args": {}},
        {"title": "Read the best one", "tool": "read_page", "args": {"url": "$result2"}}]})
    job = work.new_job(GOAL)
    work._work(job, llm)
    assert job["title"] == "GPU options" and job["status"] == "done"
    assert [s["tool"] for s in job["steps"]] == ["web_search", "read_page", "write"]
    assert job["report"].startswith("## Summary")


def test_a_write_step_waits_for_confirm():
    llm = FakeLLM({"title": "Track it", "steps": [
        {"title": "Search", "tool": "web_search", "args": {"query": "gpu"}},
        {"title": "Create an issue", "tool": "create_issue", "args": {"title": "Try Colab", "repo": "airtac"}}]})
    job = work.new_job("Find GPUs and open an issue about it on github")
    work._work(job, llm)
    assert job["status"] == "waiting" and job["steps"][1]["status"] == "waiting"
    work.confirm(job["id"], approve=True)
    work._work(job, llm)
    assert job["steps"][1]["status"] == "done" and job["status"] == "done"


def test_skills_rerun_a_good_plan():
    job = work.run_now(GOAL)
    skill_id = work.save_skill(job["id"], "GPU research")
    skill = memory.list_skills()[0]
    assert skill["name"] == "GPU research" and [s["tool"] for s in skill["steps"]] == ["web_search", "read_page", "read_page"]
    assert skill["steps"][1]["args"] == {"url": "$result1"}  # links are found again each run
    again = work.run_skill(skill_id)
    assert again["status"] == "running" and again["title"] == "GPU research"
    work._work(again)
    assert again["status"] == "done" and memory.get_skill(skill_id)["runs"] == 1


def test_stop_and_a_job_left_running():
    job = work.new_job(GOAL)
    job["stop"] = True
    work._work(job)
    assert job["status"] == "stopped"
    other = work.new_job(GOAL)
    other["status"] = "running"
    work._save(other)
    work.JOBS.clear()
    assert work.get(other["id"])["status"] == "stopped"


def test_work_api_and_starting_by_voice():
    started = client.post("/api/work", json={"goal": GOAL}).json()
    assert started["status"] == "planning" and "notes" not in started
    listed = client.get("/api/work").json()
    assert listed["jobs"][0]["id"] == started["id"] and listed["examples"] and listed["skills"] == []
    assert client.post(f"/api/work/{started['id']}/confirm", json={"approve": True}).status_code == 409
    assert client.post("/api/skills", json={"job_id": started["id"]}).status_code == 409
    assert client.post(f"/api/work/{started['id']}/stop").json() == {"stopped": True}
    reply = agent.chat("In work mode, look up how DNS works and make me notes")
    assert reply.steps[0].tool == "start_work"
