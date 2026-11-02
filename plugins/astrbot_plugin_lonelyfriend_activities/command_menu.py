from __future__ import annotations

from collections import Counter

PLUGIN_CATEGORIES = {
    "builtin_commands": "会话管理",
    "astrbot_plugin_lonelyfriend_activities": "热点审核",
    "astrbot_plugin_memory_companion": "记忆管理",
    "astrbot_plugin_rsshub": "RSS订阅",
    "astrbot_plugin_qzone": "QQ空间",
    "astrbot_plugin_meme_generator": "表情包",
    "astrbot_plugin_parser": "链接解析",
    "astrbot_plugin_self_learning": "自主学习",
}
REVIEW_HANDLERS = {"approve_review", "reject_review", "pending_reviews", "view_review", "review_records", "review_statistics"}


def visible_commands(items, permitted):
    result = []
    for item in items:
        if not item["enabled"] or not item["plugin_activated"] or item["has_conflict"] or not permitted(item):
            continue
        category = ("菜单导航" if item["plugin"] == "astrbot_plugin_lonelyfriend_activities" and item["handler_name"] == "command_menu"
                    else PLUGIN_CATEGORIES.get(item["plugin"], item["plugin_display_name"]))
        result.append({**item, "category": category})
        result.extend(visible_commands(item["sub_commands"], permitted))
    return result


def render_menu(entries, query: str, *, prefix: str, entry_command: str, capability_hints: list[str]) -> str:
    entry = prefix + entry_command
    categories = sorted(Counter(item["category"] for item in entries).items())
    query = query.strip()
    if not query:
        lines = [f"命令菜单：当前可见 {len(entries)} 个命令及子命令。"]
        lines.extend(f"{category}（{count}个）：{entry} {category}" for category, count in categories)
        lines.extend([f"\n完整列表：{entry} 全部", f"关键词查询：{entry} 热点、{entry} 表情"])
        if capability_hints:
            lines.append("\n也可直接用自然语言：\n" + "\n".join(capability_hints))
        lines.append("\n按当前启用状态与权限列出；部分命令还受插件内部开关限制。")
        return "\n".join(lines)

    page = 1
    if query.isdecimal():
        page, query = int(query), "全部"
    elif " " in query and query.rsplit(" ", 1)[1].isdecimal():
        query, page_text = query.rsplit(" ", 1)
        page = int(page_text)
    term = query.casefold()
    matches = entries if query == "全部" else [item for item in entries if term in " ".join(
        [item["category"], item["effective_command"], item["description"], *item["aliases"]]).casefold()]
    if not matches:
        return f"没有找到“{query}”相关的可见命令。发送 {entry} 查看分类。"

    exact = [item for item in matches if item["effective_command"].casefold() == term or term in [alias.casefold() for alias in item["aliases"]]]
    if len(exact) == 1 and page == 1 and not exact[0]["is_group"]:
        item = exact[0]
        lines = [f"{prefix}{item['effective_command']}｜{item['category']}", item["description"].strip()[:1100] or "该指令没有登记详细说明，请查看所属模块帮助。"]
        if item["aliases"]:
            lines.append("别名：" + "、".join(item["aliases"]))
        lines.append(f"\n返回分类：{entry}")
        return "\n".join(lines)

    matches = sorted(matches, key=lambda item: (item["category"], item["effective_command"].casefold()))
    page_count = (len(matches) + 9) // 10
    if not 1 <= page <= page_count:
        return f"“{query}”共有{page_count}页，请使用 {entry} {query} 1 至 {page_count}。"
    lines = [f"{query}：{len(matches)}个命令，第{page}/{page_count}页"]
    for item in matches[(page - 1) * 10:page * 10]:
        description = " ".join(item["description"].split())[:48]
        lines.append(f"{prefix}{item['effective_command']}" + (f" — {description}" if description else ""))
    help_entries = [item for item in entries if item["original_command"] in {"mcomp help", "表情帮助", "rsshelp"}
                    and any(match["plugin"] == item["plugin"] for match in matches)]
    if help_entries:
        lines.append("\n模块详细帮助：" + "、".join(prefix + item["effective_command"] for item in help_entries))
    if page < page_count:
        lines.append(f"下一页：{entry} {query} {page + 1}")
    lines.append(f"查用法：{entry} 完整命令名；返回：{entry}")
    return "\n".join(lines)
