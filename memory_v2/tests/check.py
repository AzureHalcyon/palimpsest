import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from memory_v2 import cli
from memory_v2.chat.loop import EventChat
from memory_v2.consolidate.replay import consolidate
from memory_v2.read.activate import activate
from memory_v2.read.search import expand
from memory_v2.store.graph import Graph
from memory_v2.store.ingest import ingest
from memory_v2.store.session import SessionStore


class FakeClient:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def chat(self, messages, tools=None, temperature=None, model=None):
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
                            "function": {"name": name, "arguments": arguments}}]}


data = tempfile.mkdtemp()
os.makedirs(os.path.join(data, "状态"), exist_ok=True)

write_session(data, "s1", [
    turn(1, "装修预算超了", "先看看超在哪", "2026-10-10"),
    turn(2, "空调一拖四要两万", "先问清全包还是裸机", "2026-10-10"),
])
write_session(data, "s2", [
    turn(1, "我妈下周来住", "客房先腾出一张床", "2026-10-12"),
    turn(2, "方案改到第四版", "先问领导在意什么", "2026-10-12"),
])

graph = Graph.open(data)
first = graph.add_event("装修超预算")
second = graph.add_event("空调一拖四")
third = graph.add_event("她妈来住")
fourth = graph.add_event("方案第四版")
graph.attach(first, "s1", 1)
graph.attach(second, "s1", 2)
graph.attach(third, "s2", 1)
graph.attach(fourth, "s2", 2)
graph.add_edge(second, first, "属于")
graph.save()

result = activate(graph, data, ["空调"])
assert result["winner"] and result["winner"]["id"] == second, result
assert "空调" in result["winner"]["hits"], result["winner"]
filtered = activate(graph, data, ["空调"], after="2026-10-11")
assert not filtered["has_seed"], filtered
text = expand(graph, data, first)
assert "装修预算超了" in text and "他：" in text and "我：" in text, text

stats = consolidate(graph, data, "s1")
assert stats["seeds"] >= 2, stats
assert stats["lit"] >= 2, stats
for _ in range(3):
    consolidate(graph, data, "s1")
graph = Graph.open(data)
assert any(edge.get("relation") == "相关" for edge in graph.edges.values()), graph.edges
assert any(float(edge.get("strength", 0)) > 0.1 for edge in graph.edges.values()), graph.edges

store = SessionStore(data, "s3")
client = FakeClient([
    call("event", json.dumps({"name": "新的事"})),
    call("recall", json.dumps({"keywords": ["新"]})),
    {"role": "assistant", "content": "好。"},
])
chat = EventChat(client, store, Graph.open(data), data, "s3",
                 [{"role": "system", "content": "x"}])
reply = chat.send("说件新事")
assert reply == "好。", reply
saved = store.turns()
assert saved[-1]["event"] == "新的事" and saved[-1]["event_id"], saved[-1]

client = FakeClient([
    call("event", json.dumps({"name": "空调的事"})),
    call("recall", json.dumps({"keywords": ["空调"]})),
    {"role": "assistant", "content": "查到了。"},
])
chat = EventChat(client, SessionStore(data, "s4"), Graph.open(data), data, "s4",
                 [{"role": "system", "content": "x"}])
chat.send("上次说的空调呢")
saved = SessionStore(data, "s4").turns()
assert saved[-1]["recall"] and saved[-1]["recall"][0]["candidates"], saved[-1]

client = FakeClient([
    call("event", json.dumps({"name": "空调的事"})),
    call("recall", json.dumps({"keywords": ["空调"]})),
    call("remember", json.dumps({"event": second})),
    {"role": "assistant", "content": "我记得空调那件事。"},
    {"role": "assistant", "content": "嗯，我记得。"},
])
chat = EventChat(client, SessionStore(data, "s5"), Graph.open(data), data, "s5",
                 [{"role": "system", "content": "x"}])
chat.send("你还记得空调吗")
saved = SessionStore(data, "s5").turns()
assert saved[-1]["remember"] and saved[-1]["remember"][0]["id"] == second, saved[-1]
writer_input = client.calls[3][1]["content"]
assert "·" in writer_input, writer_input
assert Graph.open(data).events[second]["access"] > 0, Graph.open(data).events[second]
current = saved[-1]["event_id"]
assert current and any({edge["a"], edge["b"]} == {current, second}
                       for edge in Graph.open(data).edges.values()), Graph.open(data).edges
assert os.path.exists(os.path.join(data, "说法", second + ".jsonl"))

client = FakeClient([
    call("event", json.dumps({"mode": "换事", "name": "装修"})),
    call("event", json.dumps({"mode": "子事", "name": "预算"})),
    call("event", json.dumps({"mode": "继续"})),
    call("recall", json.dumps({"keywords": ["装修"]})),
    {"role": "assistant", "content": "好。"},
])
chat = EventChat(client, SessionStore(data, "s6"), Graph.open(data), data, "s6",
                 [{"role": "system", "content": "x"}])
chat.send("先聊装修")
assert len(chat.stack) == 2, chat.stack
after = Graph.open(data)
child = [e for e, v in after.events.items() if v["name"] == "预算"][0]
parent = [e for e, v in after.events.items() if v["name"] == "装修"][0]
assert any(edge["a"] == child and edge["b"] == parent and edge["relation"] == "属于"
           for edge in after.edges.values()), after.edges
assert SessionStore(data, "s6").turns()[-1]["stack"] == [parent, child]
client.script.extend([
    call("event", json.dumps({"mode": "回"})),
    call("recall", json.dumps({"keywords": ["装修"]})),
    {"role": "assistant", "content": "好。"},
])
chat.send("回到装修")
assert chat.stack == [parent], chat.stack

client = FakeClient([
    call("event", json.dumps({"mode": "换事", "name": "装修"})),
    call("event", json.dumps({"mode": "换事", "name": "工作"})),
    call("event", json.dumps({"mode": "接旧", "event": "装修", "because": "同一套房"})),
    call("event", json.dumps({"mode": "子事", "name": "预算", "parents": ["工作"]})),
    call("event", json.dumps({"mode": "关联", "event": "工作", "because": "同一周"})),
    call("event", json.dumps({"mode": "继续"})),
    call("recall", json.dumps({"keywords": ["装修"]})),
    {"role": "assistant", "content": "好。"},
])
chat = EventChat(client, SessionStore(data, "s7"), Graph.open(data), data, "s7",
                 [{"role": "system", "content": "x"}])
chat.send("接着装修说")
after = Graph.open(data)


def in_s7(event_id):
    return any(segment.get("session") == "s7"
               for segment in after.events[event_id].get("segments") or [])


budget = [e for e, v in after.events.items() if v["name"] == "预算" and in_s7(e)][0]
parents = {edge["b"] for edge in after.edges.values()
           if edge["a"] == budget and edge["relation"] == "属于"}
decoration = next(p for p in parents if after.events[p]["name"] == "装修")
work = next(p for p in parents if after.events[p]["name"] == "工作")
assert parents == {decoration, work}, parents
assert any({edge["a"], edge["b"]} == {budget, work} and edge["relation"] == "相关"
           for edge in after.edges.values()), after.edges
assert chat.stack == [decoration, budget], chat.stack
view = chat._state_view()
assert "属于：" in view and "相关：" in view, view
client.script.extend([
    call("event", json.dumps({"mode": "回"})),
    call("recall", json.dumps({"keywords": ["装修"]})),
    {"role": "assistant", "content": "好。"},
])
chat.send("回装修")
assert chat.stack == [decoration], chat.stack

chat = EventChat(FakeClient([]), SessionStore(data, "s8"), Graph.open(data), data, "s8", [])
chat.stack = [decoration]
chat._pending_event = decoration
result, _ = chat._handle_event(json.dumps({"mode": "子事", "name": "装修"}))
assert "已经在当前这条线上" in result, result
chat.graph.add_event("同名测")
chat.graph.add_event("同名测")
chat.graph.save()
result, _ = chat._handle_event(json.dumps({"mode": "接旧", "event": "同名测"}))
assert "有多个同名" in result, result

cli.main(["mark", "--data", data, "--event", second])
assert Graph.open(data).events[second]["reward"], Graph.open(data).events[second]
cli.main(["unlink", "--data", data, "--first", current, "--second", second])
assert not any({edge["a"], edge["b"]} == {current, second}
               for edge in Graph.open(data).edges.values())
cli.main(["detach", "--data", data, "--event", current, "--last"])
assert not Graph.open(data).events[current].get("segments"), Graph.open(data).events[current]

client = FakeClient([
    call("event", json.dumps({"mode": "换事", "name": "催查"})),
    {"role": "assistant", "content": "先答"},
    call("recall", json.dumps({"keywords": ["催查"]})),
    {"role": "assistant", "content": "补查后答"},
])
chat = EventChat(client, SessionStore(data, "s9"), Graph.open(data), data, "s9",
                 [{"role": "system", "content": "x"}])
reply = chat.send("测试催查")
assert reply == "补查后答", reply

data2 = tempfile.mkdtemp()
os.makedirs(os.path.join(data2, "状态"), exist_ok=True)
shutil.copy(os.path.join(data, "状态", "s7.jsonl"),
            os.path.join(data2, "状态", "s7.jsonl"))
graph2 = Graph.open(data2)
stats = ingest(graph2, data2)
assert stats["sessions"] == 1 and stats["events"] >= 2, stats
assert any(edge["relation"] == "属于" for edge in graph2.edges.values()), graph2.edges
shutil.rmtree(data2, ignore_errors=True)

client = FakeClient([
    call("event", json.dumps({"mode": "换事", "name": "嵌套根"})),
    call("event", json.dumps({"mode": "子事", "name": "嵌套中"})),
    call("event", json.dumps({"mode": "子事", "name": "嵌套末"})),
    call("event", json.dumps({"mode": "继续"})),
    call("recall", json.dumps({"keywords": ["嵌套"]})),
    {"role": "assistant", "content": "好。"},
])
chat = EventChat(client, SessionStore(data, "s10"), Graph.open(data), data, "s10",
                 [{"role": "system", "content": "x"}])
chat.send("一层套一层")
after = Graph.open(data)
root = [e for e, v in after.events.items() if v["name"] == "嵌套根"][0]
mid = [e for e, v in after.events.items() if v["name"] == "嵌套中"][0]
leaf = [e for e, v in after.events.items() if v["name"] == "嵌套末"][0]
assert any(edge["a"] == mid and edge["b"] == root and edge["relation"] == "属于"
           for edge in after.edges.values()), after.edges
assert any(edge["a"] == leaf and edge["b"] == mid and edge["relation"] == "属于"
           for edge in after.edges.values()), after.edges

graph = Graph.open(data)
first_same = graph.add_event("同名序")
second_same = graph.add_event("同名序")
assert graph.resolve("同名序") == second_same, (first_same, second_same)
assert graph.events[second_same]["seq"] > graph.events[first_same]["seq"]
assert second_same.startswith("e-") and len(second_same) == 14, second_same

shutil.rmtree(data, ignore_errors=True)
print("OK")
