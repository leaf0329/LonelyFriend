from __future__ import annotations

import argparse
import io
import json
import re
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from pathlib import Path

from PIL import Image

from sticker_gallery import write_gallery

ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 8 * 1024 * 1024


def apply_manual_review(catalog: dict, review: dict) -> dict:
    if review.get("schema") != 1 or review.get("collected_on") != catalog["collected_on"]:
        raise ValueError("Manual review does not match this sticker collection")
    choices = review["choices"]
    known = {item["id"] for item in catalog["stickers"]}
    if not isinstance(choices, dict) or set(choices) - known:
        raise ValueError("Manual review contains unknown sticker IDs")
    if any(value not in {"keep", "exclude"} for value in choices.values()):
        raise ValueError("Manual review contains invalid decisions")
    if review["unmarked"] not in {"initial", "keep"}:
        raise ValueError("Manual review requires an explicit rule for unmarked images")
    result = deepcopy(catalog)
    for item in result["stickers"]:
        action = choices.get(item["id"])
        if action is None and review["unmarked"] == "initial":
            continue
        item["initial_review"] = item["review"]
        item["review"] = {"status": "reserved" if action == "exclude" else "approved",
                          "method": "user", "checked_on": review["exported_at"],
                          "decision": action or "unmarked-kept"}
        if action == "exclude":
            item["review"]["reason"] = "用户审核时明确排除，不参与自动发送"
    result["manual_review"] = {**review, "available": sum(
        item["review"]["status"] == "approved" for item in result["stickers"])}
    return result


def inspect_image(content: bytes) -> dict:
    with Image.open(io.BytesIO(content)) as image:
        if image.format not in {"GIF", "PNG", "JPEG", "WEBP"}:
            raise ValueError("Unsupported image format")
        if max(image.size) > 4096:
            raise ValueError("Image dimensions exceed 4096 pixels")
        details = {"format": image.format, "width": image.width,
                   "height": image.height, "frames": getattr(image, "n_frames", 1)}
        image.verify()
    return details


def main() -> None:
    parser = argparse.ArgumentParser(description="Download the attributed, noncommercial local QQ sticker collection.")
    parser.add_argument("--workers", type=int, default=3, choices=range(1, 7))
    parser.add_argument("--review", type=Path, help="Import an exported manual review into private local settings.")
    parser.add_argument("--unmarked", choices=["initial", "keep"], default="initial",
                        help="When importing, retain initial decisions or keep all unmarked images.")
    args = parser.parse_args()
    manifest = json.loads((ROOT / "config/stickers.json").read_text(encoding="utf-8-sig"))
    directory = ROOT / "data/stickers"
    files = directory / "files"
    files.mkdir(parents=True, exist_ok=True)
    review_path = directory / "user-review.json"
    review = None
    if args.review:
        review = {**json.loads(args.review.read_text(encoding="utf-8-sig")), "unmarked": args.unmarked}
    elif review_path.is_file():
        review = json.loads(review_path.read_text(encoding="utf-8-sig"))
    effective = apply_manual_review(manifest, review) if review is not None else manifest

    def download(item):
        name = item["file"]
        if not re.fullmatch(r"[a-z0-9_-]+\.(gif|png|jpe?g|webp)", name):
            raise ValueError("Invalid local sticker filename")
        path = files / name
        if path.is_file():
            content = path.read_bytes()
        else:
            request = urllib.request.Request(item["url"], headers={
                "User-Agent": "LonelyFriend-sticker-collection (+https://github.com/leaf0329/LonelyFriend)",
            })
            with urllib.request.urlopen(request, timeout=60) as response:
                content = response.read(MAX_BYTES + 1)
        if len(content) > MAX_BYTES:
            raise ValueError("Sticker exceeds the 8 MiB per-file limit")
        details = inspect_image(content)
        if not path.is_file():
            path.write_bytes(content)
        return {"id": item["id"], "bytes": len(content), **details}

    completed, failed = {}, []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(download, item): item for item in manifest["stickers"]}
        for future in as_completed(futures):
            item = futures[future]
            try:
                completed[item["id"]] = future.result()
            except Exception as exc:
                failed.append({"id": item["id"], "url": item["url"], "error": str(exc)})
            count = len(completed) + len(failed)
            if count % 25 == 0 or count == len(futures):
                print(f"Checked {count}/{len(futures)}: {len(completed)} ready, {len(failed)} failed", flush=True)
    available = [item for item in effective["stickers"] if item["id"] in completed]
    catalog = {**effective, "stickers": available}
    if review is not None:
        temporary = directory / "user-review.json.part"
        temporary.write_text(json.dumps(review, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(review_path)
    temporary = directory / "catalog.json.part"
    temporary.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(directory / "catalog.json")
    report = {"ready": len(available), "animated": sum(row["frames"] > 1 for row in completed.values()),
              "bytes": sum(row["bytes"] for row in completed.values()),
              "images": list(completed.values()), "failed": failed}
    (directory / "import-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    gallery = {**manifest, "stickers": [item for item in manifest["stickers"] if item["id"] in completed]}
    if review is not None:
        gallery["manual_review"] = effective["manual_review"]
    write_gallery(directory, gallery)
    print(json.dumps({k: v for k, v in report.items() if k != "images"}, ensure_ascii=False), flush=True)
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
