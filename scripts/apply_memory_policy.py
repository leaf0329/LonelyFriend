from __future__ import annotations

import asyncio

from configure import ROOT, apply_context_policy, apply_memory_policy, install_project_plugin, memory_tools, read_json, set_persona, write_json


def main() -> None:
    install_project_plugin()
    root_config = ROOT / "data/cmd_config.json"
    configs = [root_config]
    profile = ROOT / "data/cuteq-public-profile.json"
    if profile.exists():
        configs.append(ROOT / "data/config" / f"abconf_{read_json(profile)['id']}.json")
    for path in configs:
        value = read_json(path)
        apply_context_policy(value)
        write_json(path, value)
    memory_path = ROOT / "data/config/astrbot_plugin_memory_companion_config.json"
    memory = read_json(memory_path)
    apply_memory_policy(memory)
    write_json(memory_path, memory)
    asyncio.run(set_persona((ROOT / "config/persona.md").read_text(encoding="utf-8"), memory_tools()))
    print("Applied the detailed-memory policy; account credentials and chat allowlists were preserved.")


if __name__ == "__main__":
    main()
