from __future__ import annotations

import asyncio

from configure import ROOT, apply_context_policy, apply_memory_policy, configure_local_embedding, install_project_plugin, load_persona_prompt, memory_tools, read_json, set_persona, write_json


def main() -> None:
    install_project_plugin()
    root_config = ROOT / "data/cmd_config.json"
    configs = [root_config]
    for path in configs:
        value = read_json(path)
        apply_context_policy(value)
        configure_local_embedding(value)
        write_json(path, value)
    memory_path = ROOT / "data/config/astrbot_plugin_memory_companion_config.json"
    memory = read_json(memory_path)
    apply_memory_policy(memory)
    write_json(memory_path, memory)
    asyncio.run(set_persona(load_persona_prompt(), memory_tools()))
    print("Applied the detailed-memory policy; account credentials and chat allowlists were preserved.")


if __name__ == "__main__":
    main()
