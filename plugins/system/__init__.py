"""System: how your laptop is doing (battery, memory, processor, disk) and which model is loaded.
Useful on an 8 GB machine: if memory is nearly full, a smaller model answers faster."""
import time

import httpx

from airtac import config
from airtac.plugin_api import Tool, ToolResult, plugin, schema

p = plugin(__name__)


def snapshot() -> dict:
    import psutil
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage(str(config.ROOT.anchor or "/"))
    battery = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
    loaded = []
    if config.LLM_PROVIDER == "ollama":
        try:
            loaded = [m.get("name", "") for m in httpx.get(f"{config.LLM_BASE_URL}/api/ps", timeout=2).json().get("models", [])]
        except (httpx.HTTPError, ValueError):
            loaded = []
    return {"cpu": psutil.cpu_percent(interval=0.3), "ram_used": mem.used / 2**30, "ram_total": mem.total / 2**30,
            "ram_percent": mem.percent, "disk_free": disk.free / 2**30, "disk_total": disk.total / 2**30,
            "battery": None if battery is None else {"percent": battery.percent, "plugged": battery.power_plugged},
            "uptime_hours": (time.time() - psutil.boot_time()) / 3600, "models_loaded": loaded}


def system_status() -> ToolResult:
    s = snapshot()
    parts = [f"Memory is {s['ram_percent']:.0f} percent used", f"the processor is at {s['cpu']:.0f} percent"]
    if s["battery"]:
        parts.append(f"battery {s['battery']['percent']:.0f} percent" + (", charging" if s["battery"]["plugged"] else ""))
    spoken = ", ".join(parts) + "."
    if s["ram_percent"] > 90:
        spoken += " Memory is nearly full: close some browser tabs or use a smaller model."
    screen = (f"Memory: {s['ram_used']:.1f} of {s['ram_total']:.1f} GB ({s['ram_percent']:.0f}%)\n"
              f"Processor: {s['cpu']:.0f}%\nDisk free: {s['disk_free']:.0f} of {s['disk_total']:.0f} GB\n"
              + (f"Battery: {s['battery']['percent']:.0f}%{' (charging)' if s['battery']['plugged'] else ''}\n" if s["battery"] else "")
              + f"Up for {s['uptime_hours']:.1f} hours\nModel in memory: {', '.join(s['models_loaded']) or 'none'}")
    return ToolResult(spoken, screen)


p.add(Tool("system_status", "Check the laptop: battery, memory, processor load, free disk space and the loaded AI model.",
           schema(), "local", system_status))


@p.dashboard
def page(pg: dict) -> None:
    s = snapshot()
    pg["stats"] = [{"label": "Memory used", "value": f"{s['ram_used']:.1f} / {s['ram_total']:.1f} GB"},
                   {"label": "Processor", "value": f"{s['cpu']:.0f}%"},
                   {"label": "Disk free", "value": f"{s['disk_free']:.0f} GB"}]
    if s["battery"]:
        pg["stats"].append({"label": "Battery", "value": f"{s['battery']['percent']:.0f}%"})
    pg["lists"] = [{"title": "Model in memory", "empty": "None loaded right now.",
                    "items": [{"title": m} for m in s["models_loaded"]]}]


p.mock_rule(24.5, ("battery", "ram", "cpu", "system status", "disk space", "memory usage", "laptop status"),
            "system_status")
