import io
import json
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from memory_v2.chat import tools
from memory_v2.chat.loop import EventChat
from memory_v2 import edits
from memory_v2.consolidate.replay import consolidate
from memory_v2.core.config import ChatConfig
from memory_v2.core.embed import cosine
from memory_v2.core.think import (ThinkFilter, normalize_message, normalize_turn,
                                 split_think)
from memory_v2.read.activate import activate, event_vectors
from memory_v2.read.search import expand
from memory_v2.store.graph import Graph, GraphError
from memory_v2.store.session import SessionStore, SessionSealed
from memory_v2.store.vectors import VectorStore
from memory_v2 import view
from testsupport import tempdir


class FakeClient:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def chat(self, messages, tools=None, temperature=None, model=None, on_delta=None):
        self.calls.append(messages)
        return self.script.pop(0) if self.script else {"role": "assistant", "content": ""}


def turn(number, user, assistant, day):
    return {"turn": number, "ts": day + "T10:00:00", "user": user,
            "assistant": assistant, "event": None, "event_id": None,
            "calls": [], "recall": [], "recollect": []}


def write_session(data_dir, session, turns):
    store = SessionStore(data_dir, session)
    for item in turns:
        store.append(item)


def call(name, arguments, call_id="c1"):
    return {"role": "assistant", "content": "",
            "tool_calls": [{"id": call_id, "type": "function",
                            "function": {"name": name,
                                         "arguments": json.dumps(arguments, ensure_ascii=False)}}]}


def reply(text):
    return {"role": "assistant", "content": text}


assert ChatConfig().embed_dim == 1024
try:
    cosine([1.0, 2.0], [1.0, 2.0, 3.0])
except ValueError:
    pass
else:
    raise AssertionError("cosine must reject different dimensions")
cache_dir = tempdir("mv2-cache-")
cache_path = os.path.join(cache_dir, "vectors.json")
cache = VectorStore(cache_path, model="m", dim=2)
cache.put("e", "h", [1.0, 2.0])
cache.save()
assert VectorStore(cache_path, model="m", dim=2).get("e", "h") == [1.0, 2.0]
stale = VectorStore(cache_path, model="m", dim=3)
assert not stale.items and not os.path.exists(cache_path)
shutil.rmtree(cache_dir, ignore_errors=True)

# 思考内联在正文里（`<think>`）：整段和流式都要拆开。
assert split_think("<think>想</think>说") == ("想", "说")
assert split_think("<think attr=1>只想想") == ("只想想", "")
assert split_think("没有标签") == ("", "没有标签")
_clean = normalize_message({"role": "assistant", "content": "<think>想</think>说"})
assert _clean["content"] == "说" and _clean["reasoning_content"] == "想", _clean
_turn_clean = normalize_turn({"assistant": "<think>想</think>说", "reasoning": ""})
assert _turn_clean["assistant"] == "说" and _turn_clean["reasoning"] == "想", _turn_clean
_filtered = []
_think = ThinkFilter()
for _chunk in ["<thi", "nk>想</th", "ink>说"]:
    _filtered.extend(_think.feed(_chunk))
_filtered.extend(_think.flush())
assert _filtered == [("reasoning", "想"), ("content", "说")], _filtered

# 空文本不送 embedding：上游收到空串会忽略 dimensions，整批按原生 2560 维回来。
class _RecordingEmbedder:
    available = True

    def __init__(self):
        self.seen = []
        self.config = ChatConfig(embed_dim=2)

    def embed(self, texts):
        self.seen.append(list(texts))
        return [[1.0, 0.0] for _ in texts]


vector_dir = tempdir("mv2-vector-")
text_store = VectorStore(os.path.join(vector_dir, "vectors.json"), model="m", dim=2)
recorder = _RecordingEmbedder()
loaded = event_vectors(None, vector_dir, {"a": "有正文", "b": "", "c": "   "},
                       recorder, store=text_store)
assert recorder.seen == [["有正文"]], recorder.seen
assert loaded["a"] == [1.0, 0.0] and loaded["b"] == [] and loaded["c"] == [], loaded
shutil.rmtree(vector_dir, ignore_errors=True)

data = tempdir("mv2-data-")
os.makedirs(os.path.join(data, "状态"), exist_ok=True)

write_session(data, "s1", [
    turn(1, "装修预算超了", "先看看超在哪", "2026-10-10"),
    turn(2, "空调一拖四要两万", "先问清全包还是裸机", "2026-10-10"),
])
graph = Graph.open(data)
first = graph.add_event("装修超预算")
second = graph.add_event("空调一拖四")
graph.attach(first, "s1", 1)
graph.attach(second, "s1", 2)
graph.add_edge(second, first, "属于")
graph.save()

result = activate(graph, data, ["空调"])
assert result["winner"] and result["winner"]["id"] == second, result
assert "空调" in result["winner"]["hits"], result["winner"]
filtered = activate(graph, data, ["空调"], after="2026-10-11")
assert not filtered["has_seed"], filtered
text = expand(graph, data, first)
assert "装修预算超了" in text and "他：" in text and "我：" in text, text

# 网络：一轮多标；新节点挂到同一个节点；已有节点只挂不新建。
client = FakeClient([
    call("recall", {"keywords": ["开场"]}),
    call("event", {"new": "刚开场"}),
    reply("你好。"),
    call("recall", {"keywords": ["语义"]}),
    call("event", {"new": "语义空间方案", "links": ["刚开场"]}),
    call("event", {"new": "参数化失败", "links": ["刚开场"]}),
    reply("两条都记下。"),
    call("recall", {"keywords": ["语义"]}),
    call("event", {"event": "语义空间方案"}),
    reply("记得。"),
    call("recall", {"keywords": ["语义"]}),
    call("recollect", {"event": "语义空间方案"}),
    call("event", {"event": "语义空间方案"}),
    reply("本场不用取。"),
])
chat = EventChat(client, SessionStore(data, "t1"), Graph.open(data), data, "t1",
                 [{"role": "system", "content": "x"}],
                 echo=lambda text: None)
for utterance in ["你好", "我做了语义空间和参数化两条路", "还记得语义空间吗", "取一下语义空间"]:
    chat.send(utterance)

turns = SessionStore(data, "t1").turns()
assert len(turns) == 4, turns
assert len(turns[1]["events"]) == 2, turns[1]
assert turns[1]["event"] == "语义空间方案", turns[1]
assert turns[2]["events"][0]["new"] is False, turns[2]
assert turns[3]["events"][0]["id"] == turns[2]["events"][0]["id"], turns[3]
assert any("这次聊天里的内容就在当前对话里" in str(message.get("content") or "")
           for message in chat.messages if message.get("role") == "tool"), chat.messages

graph = Graph.open(data)
names = {event["name"] for event in graph.events.values()}
assert {"语义空间方案", "参数化失败", "刚开场"} <= names, names
space = [eid for eid, event in graph.events.items() if event["name"] == "语义空间方案"][0]
param = [eid for eid, event in graph.events.items() if event["name"] == "参数化失败"][0]
start = [eid for eid, event in graph.events.items() if event["name"] == "刚开场"][0]
assert any({edge["a"], edge["b"]} == {space, start} and edge["relation"] == "关联"
           for edge in graph.edges.values()), graph.edges
assert any({edge["a"], edge["b"]} == {param, start} and edge["relation"] == "关联"
           for edge in graph.edges.values()), graph.edges
assert not any({edge["a"], edge["b"]} == {space, param} for edge in graph.edges.values()), graph.edges
assert graph.events[space]["segments"] == [{"session": "t1", "start": 2, "end": 4}], graph.events[space]

# recall 搜这次聊天，并标明轮次；不返回正文。
found, _record = chat._handle_recall(json.dumps({"keywords": ["语义"]}))
assert "这次聊天 · 第" in found and space in found, found

# 新节点可以不连（孤立）；挂已有可以顺手连到别的点。
_isolated, isolated_record = chat._handle_event(json.dumps({"new": "孤立节点"}))
assert isolated_record and "孤立节点" in _isolated, _isolated
assert not any({edge["a"], edge["b"]} == {isolated_record["id"], space}
               for edge in Graph.open(data).edges.values())
_linked, linked_record = chat._handle_event(json.dumps({"event": space, "links": [start]}))
assert linked_record and linked_record["id"] == space, linked_record
assert any({edge["a"], edge["b"]} == {space, start}
           for edge in Graph.open(data).edges.values())

# 同名新建：名字就是身份，直接按已有节点标，不悄悄建第二个。
_dup, _dup_record = chat._handle_event(json.dumps({"new": "语义空间方案"}))
assert _dup_record and _dup_record["id"] == space and "语义空间方案" in _dup, (
    _dup, _dup_record)
assert len([eid for eid, event in Graph.open(data).events.items()
            if event["name"] == "语义空间方案"]) == 1, Graph.open(data).events

# 名字记内容，不记谁在说。
_said, _said_record = chat._handle_event(json.dumps({"new": "他说语义空间先不分"}))
assert _said_record is None and "名字记内容" in _said, _said

# 没有值得记的内容：skip，不建节点。
_skip, _skip_record = chat._handle_event(json.dumps({"skip": True}))
assert _skip_record and _skip_record["skip"] and "不建节点" in _skip, _skip
assert chat._handle_event(json.dumps({"skip": True, "new": "x"}))[1] is None

# 还没成形：hold 挂起，落盘时并进去。
_hold, _hold_record = chat._handle_event(json.dumps({"hold": True, "new": "分母对不上"}))
assert _hold_record and _hold_record["hold"], _hold
assert chat.turn in chat._held, chat._held
# 挂起满了必须先落。
saved = chat._held
chat._held = list(range(100, 100 + tools.MAX_HELD))
_full, _full_record = chat._handle_event(json.dumps({"hold": True}))
assert _full_record is None and "挂起已满" in _full, _full
chat._held = saved
# 新建节点时把挂起的轮一起并进去。
_merged, _merged_record = chat._handle_event(json.dumps({"new": "分母与明细要对得上"}))
assert _merged_record and "并入挂起的" in _merged, _merged
assert chat.turn not in chat._held, chat._held
assert any(segment["start"] <= chat.turn <= segment["end"]
           for segment in chat.graph.events[_merged_record["id"]]["segments"])

# 名字写错了能改，连错了能断。
_renamed, _rename_record = chat._handle_event(
    json.dumps({"event": _merged_record["id"], "rename": "分母与明细加总要一致"}))
assert _rename_record and _rename_record["rename"]["to"] == "分母与明细加总要一致", _renamed
_unlinked, _unlink_record = chat._handle_event(
    json.dumps({"event": space, "unlink": [start]}))
assert _unlink_record and _unlink_record["unlink"] == [start], _unlinked
assert not any({edge["a"], edge["b"]} == {space, start}
               for edge in chat.graph.edges.values())

# 归档：模型建的边不被当成旧树边衰减掉；场次封口。
consolidate(Graph.open(data), data, "t1")
archived = Graph.open(data)
assert any(edge["relation"] == "关联" and edge["source"] == "model"
           and float(edge["strength"]) >= 0.15 for edge in archived.edges.values()), archived.edges
try:
    chat.send("再聊一句")
except SessionSealed:
    pass
else:
    raise AssertionError("archived session must refuse further turns")

# 旧数据兼容：没有 events 的旧轮次仍能读 event/event_id。
old = turn(1, "旧轮", "旧答", "2026-10-11")
old["event"] = "旧事"
old["event_id"] = "e-555555555555"
write_session(data, "s2", [old])
assert SessionStore(data, "s2").turns()[-1]["event_id"] == "e-555555555555"

bad = tempdir("mv2-bad-")
with open(os.path.join(bad, "graph.json"), "w", encoding="utf-8") as handle:
    handle.write("{not json")
try:
    Graph.open(bad)
except GraphError:
    pass
else:
    raise AssertionError("corrupt graph must raise")
names = os.listdir(bad)
assert any(name.startswith("graph.json.corrupt-") for name in names), names
assert not os.path.exists(os.path.join(bad, "graph.json")), names
shutil.rmtree(bad, ignore_errors=True)

# 一轮里：skip 不建节点；hold 先挂着，下一轮落盘时并进去；重启后挂起还在。
data2 = tempdir("mv2-hold-")
write_session(data2, "k1", [])
client2 = FakeClient([
    call("recall", {"keywords": ["开场"]}),
    call("event", {"skip": True}),
    reply("那就这样。"),
    call("recall", {"keywords": ["分母"]}),
    call("event", {"hold": True, "new": "分母对不上"}),
    reply("先记着。"),
])
chat2 = EventChat(client2, SessionStore(data2, "k1"), Graph.open(data2), data2, "k1",
                  [{"role": "system", "content": "x"}],
                  echo=lambda text: None)
chat2.send("今天先到这里")
chat2.send("那分母对不上呢")
turns2 = SessionStore(data2, "k1").turns()
assert turns2[0]["mode"] == "无事件" and not turns2[0]["events"], turns2[0]
assert turns2[1]["mode"] == "挂起" and turns2[1]["draft"], turns2[1]
assert not Graph.open(data2).events, Graph.open(data2).events
# 重启：挂起从场次记录恢复
again = EventChat(FakeClient([]), SessionStore(data2, "k1"), Graph.open(data2), data2, "k1",
                  [{"role": "system", "content": "x"}], echo=lambda text: None)
assert again._held == [2], again._held
merged, _record = again._handle_event(json.dumps({"new": "分母与明细要对得上"}))
assert "并入挂起的 1 轮" in merged, merged
assert not again._held, again._held
segmented = again.graph.events[_record["id"]]["segments"]
assert segmented and segmented[0]["start"] <= 2 <= segmented[0]["end"], segmented
shutil.rmtree(data2, ignore_errors=True)

shutil.rmtree(data, ignore_errors=True)

# ── 编辑清单：改名 / 删事件 / 剪边 ──────────────────────────────
data3 = tempdir("mem-edits-")
graph3 = Graph(os.path.join(data3, "graph.json"))
first3 = graph3.new_event_id()
second3 = graph3.new_event_id()
third3 = graph3.new_event_id()
for event_id, name in ((first3, "甲"), (second3, "乙"), (third3, "丙")):
    graph3.events[event_id] = {"name": name, "segments": [], "seq": 1}
graph3.edges["e1"] = {"a": first3, "b": second3, "relation": "相关", "strength": 0.5}
graph3.edges["e2"] = {"a": second3, "b": third3, "relation": "属于", "strength": 0.4}
graph3.pairs["p1"] = {"a": second3, "b": third3}
graph3.save()

ops_file = os.path.join(data3, "edits.json")
with open(ops_file, "w", encoding="utf-8") as handle:
    json.dump({"ops": [{"op": "rename", "id": first3, "name": "甲改"},
                       {"op": "unlink", "a": first3, "b": second3},
                       {"op": "delete", "id": second3},
                       {"op": "delete", "id": "没有这一件"}]},
              handle, ensure_ascii=False)
payload = edits.load(ops_file)
result = edits.apply(graph3, payload["ops"])
assert result["changed"] == 3, result
assert result["skipped"] and "找不到" in result["skipped"][0], result
assert graph3.events[first3]["name"] == "甲改", graph3.events[first3]
assert second3 not in graph3.events, graph3.events
assert not [key for key, edge in graph3.edges.items()
            if second3 in (edge.get("a"), edge.get("b"))], graph3.edges
assert not [key for key, pair in graph3.pairs.items()
            if second3 in (pair.get("a"), pair.get("b"))], graph3.pairs
saved = edits.backup(graph3.path)
graph3.save()
assert saved and os.path.exists(saved), saved
# 同名不让改：甲改 已经占了，另一件改成它会跳过
with open(ops_file, "w", encoding="utf-8") as handle:
    json.dump({"ops": [{"op": "rename", "id": third3, "name": "甲改"}]},
              handle, ensure_ascii=False)
again = edits.apply(graph3, edits.load(ops_file)["ops"])
assert again["changed"] == 0 and again["skipped"], again
assert graph3.events[third3]["name"] == "丙", graph3.events[third3]
shutil.rmtree(data3, ignore_errors=True)

# ── 记忆图：刚记下来的孤立事件也要看得见 ────────────────────────
# 没有一条边的节点半径只有 5 个单位，缩到整屏剩一两个像素；标签原来只在
# 度数 ≥7 或选中/悬停或放大到 1.9 倍时才画，于是"记了但图上找不到"。
data4 = tempdir("mem-view-")
graph4 = Graph(os.path.join(data4, "graph.json"))
for position, name in enumerate(("新记的一件", "更早的一件", "第三件")):
    event_id = graph4.new_event_id()
    graph4.events[event_id] = {"name": name, "segments": [], "seq": 1,
                               "created": "2026-10-25T23:5%d:11" % position,
                               "notes": [{"text": "记下来的内容",
                                          "at": "2026-10-25T23:51:11"}]}
graph4.save()
page = view.render_page(data4, "自检")
found = re.search(r'<script id="payload" type="application/json">(.*?)</script>',
                  page, re.S)
assert found, "页面里要内嵌数据"
payload = json.loads(found.group(1))
ai = payload["ais"][0]
assert len(ai["nodes"]) == 3, ai["nodes"]
assert len({(node["x"], node["y"]) for node in ai["nodes"]}) == 3, ai["nodes"]
template = io.open(os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "view_template.html"), encoding="utf-8").read()
assert "S.nodes.length <= 40" in template, "节点少的时候要全标名字"
assert "function drawRadius(" in template, "孤立节点要有屏幕上的最小半径"
shutil.rmtree(data4, ignore_errors=True)


print("OK")
