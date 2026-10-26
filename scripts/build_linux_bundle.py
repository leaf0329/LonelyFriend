from __future__ import annotations

import argparse
import io
import json
import sqlite3
import subprocess
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGINS = (
    "astrbot_plugin_memory_companion",
    "astrbot_plugin_proactive_chat",
    "astrbot_plugin_qzone",
    "astrbot_plugin_lonelyfriend_memory",
)
PRIVATE_FILES = (
    "cmd_config.json", "cuteq-private.json", "cuteq-access.json",
    "persona.md", "persona-binding.json", "mcp_server.json",
)


def sqlite_snapshot(source: Path, destination: Path) -> dict:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as origin:
        with sqlite3.connect(destination) as target:
            origin.backup(target)
            if target.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise RuntimeError(f"SQLite snapshot failed verification: {source.name}")
            tables = [row[0] for row in target.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )]
            return {name: target.execute(
                'SELECT count(*) FROM "' + name.replace('"', '""') + '"'
            ).fetchone()[0] for name in tables}


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a private QQ-only Linux migration bundle.")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / ".cache"):
        raise ValueError("Private migration bundles must be written under .cache/")
    output.parent.mkdir(parents=True, exist_ok=True)
    snapshot_root = output.parent / "sqlite-snapshots"
    files: dict[str, Path] = {}
    snapshots = {}

    def include(source: Path, archive_name: Path | None = None) -> None:
        relative = archive_name or source.relative_to(ROOT)
        if source.is_dir():
            for child in source.iterdir():
                if child.name not in {".git", "__pycache__", ".locks"}:
                    include(child, relative / child.name)
        elif source.is_file() and source.suffix not in {".pyc", ".pem", ".ppk", ".key"}:
            if source.name.endswith(("-wal", "-shm")) or source.name == "req041_scoped.db":
                return
            if source.suffix in {".db", ".sqlite", ".sqlite3"}:
                target = snapshot_root / relative
                snapshots[relative.as_posix()] = sqlite_snapshot(source, target)
                files[relative.as_posix()] = target
            else:
                files[relative.as_posix()] = source

    for name in ("config", "scripts", "plugins", "deploy", "tests"):
        include(ROOT / name)
    for name in (".astrbot", ".gitignore", ".gitattributes", "README.md", "requirements.txt", "requirements-linux.lock.txt", "sources.lock.json"):
        include(ROOT / name)
    for name in PRIVATE_FILES:
        include(ROOT / "data" / name)
    include(ROOT / "data/data_v4.db")
    for name in PLUGINS:
        plugin_source = ROOT / ("plugins" if name == "astrbot_plugin_lonelyfriend_memory" else "data/plugins") / name
        include(plugin_source, Path("data/plugins") / name)
        include(ROOT / "data/plugin_data" / name)
        include(ROOT / "data/config" / (name + "_config.json"))
    for name in ("webui.json", "onebot11.json", "napcat.json"):
        include(ROOT / "data/napcat/config" / name)
    include(ROOT / "runtime/models")
    baseline = {}
    for name in ("cmd_config.json", "astrbot_plugin_memory_companion_config.json"):
        cached = ROOT / ".cache/validation-baseline" / name
        baseline[name] = cached.read_bytes() if cached.is_file() else subprocess.check_output(
            ["git", "show", f"baseline-deployed-2026-10-24:config/deployed-baseline/{name}"], cwd=ROOT
        )
    with tarfile.open(output, "w:gz", compresslevel=3, dereference=True) as archive:
        for name, source in sorted(files.items()):
            archive.add(source, arcname=name, recursive=False)
        for name, content in baseline.items():
            entry = tarfile.TarInfo(".cache/validation-baseline/" + name)
            entry.size = len(content)
            entry.mode = 0o600
            archive.addfile(entry, io.BytesIO(content))
    report = {"files": len(files) + len(baseline), "bytes": output.stat().st_size,
              "plugins": list(PLUGINS), "sqlite_tables": snapshots,
              "contains_private_data": True}
    output.with_suffix(".manifest.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
