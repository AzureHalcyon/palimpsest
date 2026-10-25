#!/usr/bin/env python3
"""事件图查看器：把 memory_v2 的事件图渲染成可交互的 HTML（另可出 PNG）。

节点是事件；边是模型自己声明的关系（属于/相关）；内容近邻来自
`content-index.json`（1024 维向量的近邻，词面近邻取并集）。
点节点做两跳扩散，规则与 `read/activate.py` 一致：边权 0.85（model）/ 0.6（其它来源），
内容近邻 0.5×相似度，增益 > 0.05 才继续。

用法：
    python -m memory_v2 view --data data/memory          # 写到 .local/view/graph-view.html
    python -m memory_v2 view --data data/memory --png    # 同时出 PNG 快照

看图只需要一个浏览器（HTML 自带数据）；`networkx` 只用于链接布局的社区划分，
`matplotlib` 只在出 PNG 时用，都没有也能跑。
"""

from __future__ import annotations

import argparse
import colorsys
import json
import math
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))

from .read.search import event_text      # noqa: E402
from .store.graph import Graph           # noqa: E402

TRACE_TOP = 18
CONTENT_KEEP = 8          # 数据里每个节点保留多少条内容近邻（页面可用滑杆只画前几条）
HOP_GAIN = 0.05
MODEL_WEIGHT = 0.85
CONTENT_WEIGHT = 0.5

BG = "#0a0d13"


TRACE_TOP = 18
CONTENT_KEEP = 8          # 数据里每个节点保留多少条内容近邻（页面可用滑杆只画前几条）
HOP_GAIN = 0.05
MODEL_WEIGHT = 0.85
CONTENT_WEIGHT = 0.5

BG = "#0a0d13"


# --------------------------------------------------------------------------
# 读数据


def load_content(data_dir: str) -> dict:
    """content-index.json：{event_id: [[相似度, other_id], ...]}，按相似度降序。"""
    path = os.path.join(data_dir, "content-index.json")
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return {}
    items = payload.get("items")
    if not isinstance(items, dict):
        return {}
    out = {}
    for event_id, pairs in items.items():
        try:
            out[str(event_id)] = [(float(value), str(other)) for value, other in pairs]
        except (TypeError, ValueError):
            continue
    return out


def load_vectors(data_dir: str) -> dict:
    path = os.path.join(data_dir, "vectors.json")
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return {}
    items = payload.get("items") or {}
    return {str(key): list(item.get("vector") or [])
            for key, item in items.items() if isinstance(item, dict)}


def snippet(text: str, limit: int = 120) -> str:
    text = " ".join(str(text or "").split())
    return text[:limit] + ("…" if len(text) > limit else "")


# --------------------------------------------------------------------------
# 布局


def scale_points(points, span: float):
    """把点集映射到 [-span, span]，按 1%/99% 分位裁掉离群点。"""
    import numpy as np

    array = np.asarray(points, dtype=np.float64)
    if array.size == 0:
        return array
    low = np.percentile(array, 1, axis=0)
    high = np.percentile(array, 99, axis=0)
    half = np.where(high - low < 1e-9, 1.0, (high - low) / 2.0)
    centred = np.clip((array - (low + high) / 2.0) / half, -1.0, 1.0)
    return centred * span


def semantic_layout(vectors: dict, ids: list, span: float):
    """向量前两个主成分，作为“语义投影”布局。

    还没有向量的节点（刚建、没嵌过）放在原点；只要有两个以上有向量的节点就出结果，
    不要因为一个缺向量就整张退回力导向——那会让两种布局长得一模一样。
    """
    import numpy as np

    known = [event_id for event_id in ids if vectors.get(event_id)]
    if len(known) < 2:
        return None
    rows = [vectors[event_id] for event_id in known]
    matrix = np.asarray(rows, dtype=np.float64)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    matrix = matrix / norms
    centred = matrix - matrix.mean(axis=0)
    _u, _s, vt = np.linalg.svd(centred, full_matrices=False)
    projected = scale_points(centred @ vt[:2].T, span)
    position = {event_id: row for row, event_id in enumerate(known)}
    out = [[0.0, 0.0] for _ in ids]
    for row, event_id in enumerate(ids):
        if event_id in position:
            out[row] = [float(projected[position[event_id]][0]),
                        float(projected[position[event_id]][1])]
    return out


def communities(graph, ids: list, index: dict) -> list:
    """关系边上的社区划分（Louvain）；用来把“一伙的”事件拉到一起。"""
    import networkx as nx

    network = nx.Graph()
    network.add_nodes_from(range(len(ids)))
    for edge in graph.edges.values():
        first, second = str(edge.get("a")), str(edge.get("b"))
        if first in index and second in index and first != second:
            network.add_edge(index[first], index[second])
    parts = nx.community.louvain_communities(network, seed=7)
    groups = [0] * len(ids)
    for number, part in enumerate(parts):
        for node in part:
            groups[node] = number
    return groups


def force_layout(count: int, links: list, content: list, groups: list, span: float,
                 iterations: int = 520):
    """d3 风格的力导向，外加社区内聚和社区间排斥。

    关系边（AI 自己声明的）静止长度短、刚度高；内容近邻软一些、长一些；同一社区
    的节点被拉向自己的中心，不同社区的中心互相推开。这样有关系的点会抱成一团，
    团与团之间留出空隙，而不是铺成一张均匀的网。
    """
    import numpy as np

    charge = 200.0
    group_charge = 16000.0
    group_pull = 0.05
    gravity = 0.008
    damping = 0.5
    rest_model, stiffness_model = 16.0, 0.7
    rest_content, stiffness_content = 34.0, 0.2

    angles = np.linspace(0, 2 * math.pi, count, endpoint=False)
    pos = np.stack([np.cos(angles), np.sin(angles)], axis=1) * 220.0
    pos = pos + np.random.default_rng(7).normal(0.0, 6.0, pos.shape)
    vel = np.zeros_like(pos)

    model_a = np.asarray([first for first, _second in links], dtype=int)
    model_b = np.asarray([second for _first, second in links], dtype=int)
    cont_a = np.asarray([row[0] for row in content], dtype=int)
    cont_b = np.asarray([row[1] for row in content], dtype=int)
    cont_w = np.asarray([min(1.0, max(0.2, float(row[2]))) for row in content])

    group_ids = np.asarray(groups, dtype=int)
    group_count = int(group_ids.max()) + 1 if count else 1
    group_onehot = (group_ids[:, None] == np.arange(group_count)[None, :]).astype(float)

    def springs(disp, first, second, rest, stiffness, weights=None):
        if not len(first):
            return
        vector = pos[second] - pos[first]
        length = np.linalg.norm(vector, axis=1) + 1e-6
        pull = (length - rest) * stiffness
        if weights is not None:
            pull = pull * weights
        force = vector / length[:, None] * pull[:, None]
        index = np.concatenate([first, second])
        for axis in (0, 1):
            disp[:, axis] += np.bincount(index, weights=np.concatenate([force[:, axis],
                                                                      -force[:, axis]]),
                                         minlength=count)

    for _step in range(iterations):
        # 节点间短程排斥
        delta = pos[:, None, :] - pos[None, :, :]
        dist2 = (delta ** 2).sum(-1)
        np.fill_diagonal(dist2, 1e12)
        np.maximum(dist2, 36.0, out=dist2)
        disp = (delta / np.sqrt(dist2)[..., None] * (charge / dist2)[..., None]).sum(1)

        springs(disp, model_a, model_b, rest_model, stiffness_model)
        springs(disp, cont_a, cont_b, rest_content, stiffness_content, cont_w)

        # 社区中心与社区间排斥
        centers = np.zeros((group_count, 2))
        for axis in (0, 1):
            centers[:, axis] = np.bincount(group_ids, weights=pos[:, axis],
                                           minlength=group_count)
        counts = group_onehot.sum(0)
        centers = centers / np.maximum(counts, 1.0)[:, None]
        delta_group = pos[:, None, :] - centers[None, :, :]
        dist2_group = (delta_group ** 2).sum(-1)
        dist2_group[group_onehot > 0] = 1e12
        np.maximum(dist2_group, 400.0, out=dist2_group)
        disp += (delta_group / np.sqrt(dist2_group)[..., None]
                 * (group_charge / dist2_group)[..., None]).sum(1)
        disp += (centers[group_ids] - pos) * group_pull

        disp -= pos * gravity
        vel = (vel + disp) * damping
        speed = np.linalg.norm(vel, axis=1, keepdims=True)
        limit = np.minimum(1.0, 30.0 / np.maximum(speed, 1e-9))
        vel = vel * limit
        pos = pos + vel

    lonely = [index for index in range(count)
              if index not in set(model_a.tolist()) and index not in set(model_b.tolist())
              and index not in set(cont_a.tolist()) and index not in set(cont_b.tolist())]
    return scale_points(pos, span), lonely


# --------------------------------------------------------------------------
# 扩散


def spread(graph, content: dict, seed: str, hops: int = 2):
    """镜像 read/activate.py 的扩散：两跳，逐跳累加，记录首次点亮的那一跳和来源。"""
    local = {seed: 1.0}
    first = {seed: (1.0, 0, "")}
    frontier = {seed: 1.0}
    for hop in range(1, hops + 1):
        nxt = {}
        for node, score in frontier.items():
            for other, edge in graph.neighbors(node):
                if other == seed:
                    continue
                weight = MODEL_WEIGHT if edge.get("source") == "model" else 0.6
                gain = score * float(edge.get("strength", 0.2)) * weight
                if gain <= HOP_GAIN:
                    continue
                if gain > nxt.get(other, 0.0):
                    nxt[other] = gain
                    if other not in first:
                        first[other] = (gain, hop, node)
            for similarity, other in content.get(node, ()):
                if other == seed:
                    continue
                gain = score * CONTENT_WEIGHT * similarity
                if gain <= HOP_GAIN:
                    continue
                if gain > nxt.get(other, 0.0):
                    nxt[other] = gain
                    if other not in first:
                        first[other] = (gain, hop, node)
        for other, value in nxt.items():
            local[other] = local.get(other, 0.0) + value
        frontier = nxt
    return local, first


# --------------------------------------------------------------------------
# 组装


def build_ai(data_dir: str, key: str, span: float = 620.0,
             label: str = "",
             traces: bool = True, layout: bool = True) -> dict:
    graph = Graph.open(data_dir)
    content = load_content(data_dir)
    vectors = load_vectors(data_dir)

    events = graph.events
    ids = sorted(events, key=lambda event_id: (str(events[event_id].get("created") or ""),
                                               int(events[event_id].get("seq") or 0)))
    index = {event_id: position for position, event_id in enumerate(ids)}

    sessions = sorted({str(segment.get("session") or "")
                       for event in events.values()
                       for segment in event.get("segments") or []
                       if segment.get("session")})
    session_index = {name: position for position, name in enumerate(sessions)}

    degree = {event_id: 0 for event_id in ids}
    links = []
    edges = []
    for edge in graph.edges.values():
        first, second = str(edge.get("a")), str(edge.get("b"))
        if first not in index or second not in index or first == second:
            continue
        degree[first] += 1
        degree[second] += 1
        links.append((index[first], index[second]))
        left = {str(segment.get("session") or "")
                for segment in events[first].get("segments") or []}
        right = {str(segment.get("session") or "")
                 for segment in events[second].get("segments") or []}
        edges.append([index[first], index[second], str(edge.get("relation") or "相关"),
                      float(edge.get("strength", 0.2)), float(edge.get("support", 0.0)),
                      1 if edge.get("source") == "replay" else 0,
                      0 if (left & right) else 1])

    nodes = []
    for event_id in ids:
        event = events[event_id]
        segments = event.get("segments") or []
        node_sessions = sorted({session_index[str(segment.get("session"))]
                                for segment in segments
                                if str(segment.get("session")) in session_index})
        text = ""
        try:
            text = snippet(event_text(graph, data_dir, event_id))
        except Exception:
            text = ""
        nodes.append({
            "id": event_id,
            "n": str(event.get("name") or ""),
            "s": node_sessions,
            "d": degree[event_id],
            "c": round(float(event.get("consolidation", 0.0) or 0.0), 3),
            "r": 1 if event.get("reward") else 0,
            "t": text,
            "ctx": str(event.get("context") or ""),
        })

    # 内容近邻：每个节点取最近的若干条，合并时保留更好的名次（rank 越小越近）
    ranked_pairs = {}
    for event_id in ids:
        for rank, (similarity, other) in enumerate(content.get(event_id, ())):
            if rank >= CONTENT_KEEP or other not in index:
                break
            pair = tuple(sorted((index[event_id], index[other])))
            previous = ranked_pairs.get(pair)
            if previous is None or rank < previous[1]:
                ranked_pairs[pair] = (float(similarity), rank)
    content_pairs = sorted(
        ([pair[0], pair[1], round(similarity, 4), rank]
         for pair, (similarity, rank) in ranked_pairs.items()),
        key=lambda row: (row[3], -row[2]))

    traces = {}
    if traces:
        for event_id in ids:
            local, first = spread(graph, content, event_id)
            ranked = sorted(local.items(), key=lambda item: -item[1])[:TRACE_TOP]
            rows = []
            for other, score in ranked:
                _value, hop, parent = first.get(other, (score, 0, ""))
                rows.append([index[other], round(score, 4), hop,
                             index.get(parent, -1) if parent else -1])
            traces[str(index[event_id])] = rows

    semantic = semantic_layout(vectors, ids, span)
    groups = communities(graph, ids, index)
    if layout:
        physics = [(row[0], row[1], row[2]) for row in content_pairs if row[3] < 4]
        positions, _lonely = force_layout(len(ids), links, physics, groups, span)
        if semantic is None:
            semantic = positions
        for position, node in enumerate(nodes):
            node["x"] = round(float(positions[position][0]), 2)
            node["y"] = round(float(positions[position][1]), 2)
    for position, node in enumerate(nodes):
        node["g"] = int(groups[position])
    if semantic is not None:
        for position, node in enumerate(nodes):
            node["sx"] = round(float(semantic[position][0]), 2)
            node["sy"] = round(float(semantic[position][1]), 2)

    replay = sum(1 for edge in edges if edge[5])
    return {
        "key": key,
        "label": label,
        "data": data_dir,          # 页面导出编辑清单时带回去，apply 照着这个目录写
        "sessions": sessions,
        "nodes": nodes,
        "edges": edges,
        "content": content_pairs,
        "traces": traces,
        "stats": {
            "events": len(ids),
            "edges": len(edges),
            "model": len(edges) - replay,
            "replay": replay,
            "content": len(content_pairs),
            "cross": sum(1 for edge in edges if edge[6]),
            "isolated": sum(1 for event_id in ids if degree[event_id] == 0),
            "sessions": len(sessions),
            "span": span,
        },
    }


# --------------------------------------------------------------------------
# PNG


def _session_rgb(position: int, count: int):
    ratio = 0.0 if count <= 1 else position / float(count - 1)
    hue = (200.0 + 130.0 * ratio) / 360.0
    return colorsys.hls_to_rgb(hue, 0.62, 0.72)


def render_png(ai: dict, path: str, seed: int | None = None) -> str:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.patheffects as patheffects
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    from matplotlib.collections import LineCollection

    nodes, edges, content = ai["nodes"], ai["edges"], ai["content"]
    sessions = len(ai["sessions"]) or 1
    span = ai["stats"]["span"] * 1.06

    figure = plt.figure(figsize=(11, 11), dpi=170)
    figure.patch.set_facecolor(BG)
    axis = figure.add_axes([0.02, 0.02, 0.96, 0.90])
    axis.set_facecolor(BG)

    if seed is None:
        hairlines = []
        for first, second, _similarity, _rank in content:
            hairlines.append([(nodes[first]["x"], nodes[first]["y"]),
                              (nodes[second]["x"], nodes[second]["y"])])
        axis.add_collection(LineCollection(hairlines, colors="#ffffff", linewidths=0.3,
                                           alpha=0.035, zorder=1))

    plain, cross, replay = [], [], []
    for first, second, _rel, _strength, _support, source, is_cross in edges:
        line = [(nodes[first]["x"], nodes[first]["y"]),
                (nodes[second]["x"], nodes[second]["y"])]
        if source:
            replay.append(line)
        elif is_cross:
            cross.append(line)
        else:
            plain.append(line)
    axis.add_collection(LineCollection(plain, colors="#5f7fb8", linewidths=0.5,
                                       alpha=0.26, zorder=2))
    axis.add_collection(LineCollection(cross, colors="#8fc3ff", linewidths=0.8,
                                       alpha=0.42, zorder=3))
    if replay:
        axis.add_collection(LineCollection(replay, colors="#ffb066", linewidths=1.6,
                                           alpha=0.85, zorder=4))

    sizes = [9 + 24 * math.sqrt(node["d"] + 1) + 40 * min(0.6, node["c"])
             for node in nodes]
    colors = [_session_rgb(node["s"][0] if node["s"] else 0, sessions) for node in nodes]

    activated = {}
    if seed is not None:
        for row, score, hop, _via in ai["traces"].get(str(seed), []):
            activated[row] = (score, hop)
        for row, _score, hop, via in ai["traces"].get(str(seed), []):
            if hop == 0 or via < 0:
                continue
            axis.plot([nodes[via]["x"], nodes[row]["x"]], [nodes[via]["y"], nodes[row]["y"]],
                      color="#ffd479" if hop == 1 else "#ff9d5c",
                      linewidth=1.7 if hop == 1 else 0.9,
                      alpha=0.8 if hop == 1 else 0.45, zorder=5, solid_capstyle="round")
        lit = [row for row in activated if row != seed]
        axis.scatter([nodes[row]["x"] for row in lit], [nodes[row]["y"] for row in lit],
                     s=[sizes[row] * 3.4 for row in lit], c="#ffb066", alpha=0.10,
                     linewidths=0, zorder=5)

    axis.scatter([node["x"] for node in nodes], [node["y"] for node in nodes],
                 s=sizes, c=colors, edgecolors="#0a0d13", linewidths=0.5, zorder=6,
                 alpha=0.95)
    if seed is not None and activated:
        lit = [row for row in activated if row != seed]
        axis.scatter([nodes[row]["x"] for row in lit], [nodes[row]["y"] for row in lit],
                     s=[sizes[row] * 1.5 for row in lit], c="#ffd479", alpha=0.95,
                     edgecolors="#0a0d13", linewidths=0.4, zorder=7)
        axis.scatter([nodes[seed]["x"]], [nodes[seed]["y"]],
                     s=[sizes[seed] * 3.4], c="#ffffff", alpha=0.96,
                     edgecolors="#ffb066", linewidths=1.2, zorder=8)

    font = None
    for candidate in ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/msyhbd.ttc"):
        if os.path.exists(candidate):
            font = font_manager.FontProperties(fname=candidate)
            break

    labels = sorted(range(len(nodes)), key=lambda row: -nodes[row]["d"])[:10]
    if seed is not None:
        labels = sorted(set(labels) | set(activated),
                        key=lambda row: -nodes[row]["d"])[:14]

    placed = []
    for row in labels:
        x, y = nodes[row]["x"], nodes[row]["y"]
        name = nodes[row]["n"]
        width = 8.4 * len(name) + 14
        height = 20.0
        chosen = None
        for dx, dy in ((0, 20), (0, -24), (width * 0.55 + 12, 6),
                       (-width * 0.55 - 12, 6), (0, 40), (0, -44)):
            box = (x + dx - width / 2, y + dy - height / 2,
                   x + dx + width / 2, y + dy + height / 2)
            if all(box[2] < other[0] or box[0] > other[2]
                   or box[3] < other[1] or box[1] > other[3] for other in placed):
                chosen = (dx, dy, box)
                break
        if chosen is None:
            continue
        dx, dy, box = chosen
        placed.append(box)
        axis.text(x + dx, y + dy, name, ha="center", va="center", fontsize=8.5,
                  color="#e2eaf8", fontproperties=font, zorder=9,
                  path_effects=[patheffects.withStroke(linewidth=2.6, foreground=BG)])

    axis.set_aspect("equal")
    axis.set_xlim(-span, span)
    axis.set_ylim(-span, span)
    axis.axis("off")

    stats = ai["stats"]
    caption = "%s · 事件 %d · 边 %d（模型 %d / 重放 %d）· 跨场次 %d · 无关系边 %d · 场次 %d" % (
        ai["label"], stats["events"], stats["edges"], stats["model"], stats["replay"],
        stats["cross"], stats["isolated"], stats["sessions"])
    figure.text(0.025, 0.968, caption, color="#8b9bb2", fontsize=10.5,
                fontproperties=font, ha="left", va="top")
    figure.text(0.025, 0.941, "节点颜色＝首现场次（早 → 晚）· 点大小＝关系边条数 · 细线＝内容近邻",
                color="#5d6c82", fontsize=9, fontproperties=font, ha="left", va="top")
    if seed is not None:
        rows = ai["traces"].get(str(seed), [])
        hops = {1: 0, 2: 0}
        for _row, _score, hop, _via in rows:
            if hop in hops:
                hops[hop] += 1
        figure.text(0.025, 0.035,
                    "起点「%s」· 两跳扩散后按分数取前 %d 个：第 1 跳 %d / 第 2 跳 %d"
                    % (nodes[seed]["n"], len(rows), hops[1], hops[2]),
                    color="#c9d4e6", fontsize=10.5, fontproperties=font,
                    ha="left", va="bottom")

    figure.savefig(path, facecolor=BG)
    plt.close(figure)
    return path


# --------------------------------------------------------------------------
# HTML

TEMPLATE = os.path.join(HERE, "view_template.html")


def build_html(payload: dict, out_path: str) -> str:
    with open(TEMPLATE, encoding="utf-8") as handle:
        template = handle.read()
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    data = data.replace("</", "<\\/")
    if "__PAYLOAD__" not in template:
        raise SystemExit("模板里没有 __PAYLOAD__ 占位符")
    page = template.replace("__PAYLOAD__", data)
    with open(out_path, "w", encoding="utf-8") as handle:
        handle.write(page)
    return out_path


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="事件图查看器：把一个记忆目录画成 HTML")
    parser.add_argument("--data", default=os.path.join("data", "memory"),
                        help="记忆目录（里面要有 graph.json）")
    parser.add_argument("--label", default="", help="页面上显示的名字；默认用目录名")
    parser.add_argument("--out", default=os.path.join(".local", "view"),
                        help="输出目录")
    parser.add_argument("--span", type=float, default=620.0)
    parser.add_argument("--png", action="store_true", help="同时输出 PNG 快照")
    parser.add_argument("--seed", default="", help="PNG 激活快照的起点事件 id")
    args = parser.parse_args(argv)

    data_dir = os.path.abspath(args.data)
    if not os.path.isdir(data_dir):
        raise SystemExit("没有这个目录：%s" % data_dir)
    label = args.label or os.path.basename(data_dir.rstrip("\\/")) or "memory"
    os.makedirs(args.out, exist_ok=True)
    print("读取 %s …" % data_dir, flush=True)
    payload = {"generated": time.strftime("%Y-%m-%d %H:%M"),
               "ais": [build_ai(data_dir, label, args.span, label=label)]}

    html_path = os.path.join(args.out, "graph-view.html")
    build_html(payload, html_path)
    print("HTML: %s" % html_path)

    if args.png:
        for ai in payload["ais"]:
            path = os.path.join(args.out, "network-%s.png" % ai["key"])
            render_png(ai, path)
            print("PNG: %s" % path)
            if ai["nodes"]:
                seed = next((position for position, node in enumerate(ai["nodes"])
                             if node["id"] == args.seed), None)
                if seed is not None:
                    path = os.path.join(args.out, "spread-%s.png" % ai["key"])
                    render_png(ai, path, seed=seed)
                    print("PNG: %s" % path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
