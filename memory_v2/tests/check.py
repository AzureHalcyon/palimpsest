import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from memory_v2.chat.loop import EventChat
from memory_v2.consolidate.replay import consolidate
from memory_v2.core.config import ChatConfig
from memory_v2.core.embed import cosine
from memory_v2.read.activate import activate
from memory_v2.read.search import expand
from memory_v2.store.graph import Graph, GraphError
from memory_v2.store.session import SessionStore, SessionSealed
from memory_v2.store.vectors import VectorStore


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
            "calls": [], "recall": [], "remember": []}


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
cache_dir = tempfile.mkdtemp()
cache_path = os.path.join(cache_dir, "vectors.json")
cache = VectorStore(cache_path, model="m", dim=2)
cache.put("e", "h", [1.0, 2.0])
cache.save()
assert VectorStore(cache_path, model="m", dim=2).get("e", "h") == [1.0, 2.0]
stale = VectorStore(cache_path, model="m", dim=3)
assert not stale.items and not os.path.exists(cache_path)
shutil.rmtree(cache_dir, ignore_errors=True)

data = tempfile.mkdtemp()
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
    call("remember", {"event": "语义空间方案"}),
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
assert any("本场节点不用 remember" in str(message.get("content") or "")
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

# recall 搜本场，并标明场次；不返回原文。
found, _record = chat._handle_recall(json.dumps({"keywords": ["语义"]}))
assert "本场" in found and space in found, found

# 新节点可以不连（孤立）；挂已有可以顺手连到别的点。
_isolated, isolated_record = chat._handle_event(json.dumps({"new": "孤立节点"}))
assert isolated_record and "孤立节点" in _isolated, _isolated
assert not any({edge["a"], edge["b"]} == {isolated_record["id"], space}
               for edge in Graph.open(data).edges.values())
_linked, linked_record = chat._handle_event(json.dumps({"event": space, "links": [start]}))
assert linked_record and linked_record["id"] == space, linked_record
assert any({edge["a"], edge["b"]} == {space, start}
           for edge in Graph.open(data).edges.values())

# 同名新建给出提醒，不静默当成同一件。
_dup, _dup_record = chat._handle_event(json.dumps({"new": "语义空间方案"}))
assert "已有同名" in _dup and _dup_record is None, (_dup, _dup_record)
assert len([eid for eid, event in Graph.open(data).events.items()
            if event["name"] == "语义空间方案"]) == 1, Graph.open(data).events

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

bad = tempfile.mkdtemp()
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

shutil.rmtree(data, ignore_errors=True)
print("OK")
