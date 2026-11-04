"""Explicit live GPT -> Bonsai/selector residency replay. Stop dev first.

Run with PYTHONPATH=., --live, --baseline-file <old model_lifecycle.py>, --lms
<lms.exe>. It unloads ONLY the idle GPT/Bonsai/selector test models. No user data.
"""
import argparse
import asyncio
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

from src.core.intelligence import model_lifecycle as fixed
from src.shared.config_manager import ConfigManager

BASE = "http://localhost:1234/v1"
GPT, BONSAI, SELECTOR = "openai/gpt-oss-20b", "prism-ml/bonsai-27b", "qwen2.5-3b-instruct"


async def run(args):
    spec = importlib.util.spec_from_file_location("baseline_lifecycle", args.baseline_file)
    baseline = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = baseline
    spec.loader.exec_module(baseline)
    ConfigManager.load_config()
    config = ConfigManager._config
    config["tools"]["llm_selector_model"] = SELECTOR
    lifecycle = config["local_models"]["lifecycle"]
    lifecycle.update(vram_budget_gb=16, preload_on_connect=True)
    report = {}
    for label, module in (("before", baseline), ("after", fixed)):
        models = json.loads(subprocess.check_output([args.lms, "ps", "--json"], text=True))
        if any(m["status"] != "idle" for m in models):
            raise RuntimeError("Models are busy; finish the active turn first")
        for model in models:
            if model["identifier"] in (GPT, BONSAI, SELECTOR):
                subprocess.run([args.lms, "unload", model["identifier"]], check=True, capture_output=True)
        lifecycle["model_size_estimates_gb"][BONSAI] = 8
        if label == "before":
            lifecycle["model_size_estimates_gb"].pop(BONSAI)
        config["local_models"]["active_model"] = {"name": GPT, "provider": "lmstudio", "base_url": BASE}
        assert await module.ensure_resident(BASE, GPT, module.OWNER_MAIN_MODEL)
        assert await module.ensure_resident(BASE, SELECTOR, module.OWNER_TOOL_SELECTOR)
        events = []
        original_load, original_unload = module.load_model, module.unload_model

        async def load(url, model, **kwargs):
            events.append(["load", model])
            return await original_load(url, model, **kwargs)

        async def unload(url, model):
            events.append(["unload", model])
            return await original_unload(url, model)

        module.load_model, module.unload_model = load, unload
        assert await module.switch_main_model({"name": GPT, "base_url": BASE}, {"name": BONSAI, "base_url": BASE})
        config["local_models"]["active_model"]["name"] = BONSAI
        assert await module.ensure_resident(BASE, SELECTOR, module.OWNER_TOOL_SELECTOR)
        assert await module.ensure_resident(BASE, BONSAI, module.OWNER_MAIN_MODEL)
        await module.warm_models(canonical_lane="agent")
        report[label] = events
        print(json.dumps({label: events}), flush=True)
    assert report["after"] == [["unload", GPT], ["load", BONSAI]], report
    assert ["unload", BONSAI] in report["before"], report
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(json.dumps(report, indent=2), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument("--baseline-file", required=True)
    parser.add_argument("--lms", required=True)
    parser.add_argument("--report", default="data/dev_logs/bonsai_residency_acceptance.json")
    asyncio.run(run(parser.parse_args()))
