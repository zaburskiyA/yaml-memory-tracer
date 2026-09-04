"""Generate a small interactive HTML report from memory tracer JSONL."""

from __future__ import annotations

import argparse
import json
from collections import OrderedDict
from pathlib import Path
from typing import Any


class ReportError(ValueError):
    """Raised when a JSONL trace cannot be converted into a report."""


def load_events(path: str | Path) -> list[dict[str, Any]]:
    source = Path(path)
    events: list[dict[str, Any]] = []
    try:
        lines = source.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ReportError(f"Cannot read {source}: {exc}") from exc

    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ReportError(f"Invalid JSON at {source}:{line_number}: {exc.msg}") from exc
        if not isinstance(event, dict) or not event.get("trace_id"):
            raise ReportError(f"Missing trace_id at {source}:{line_number}")
        events.append(event)
    return events


def _first_argument(events: list[dict[str, Any]], key: str) -> str | None:
    for event in events:
        arguments = event.get("arguments") or {}
        value = arguments.get(key)
        if value is not None:
            return str(value)
        for collection_key in ("episodes", "facts", "policies", "messages"):
            collection = arguments.get(collection_key)
            if isinstance(collection, list) and collection and isinstance(collection[0], dict):
                value = collection[0].get(key)
                if value is not None:
                    return str(value)
    return None


def _items_for_method(
    events: list[dict[str, Any]], method: str, argument: str
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for event in events:
        if event.get("method") != method or event.get("status") != "ok":
            continue
        value = (event.get("arguments") or {}).get(argument)
        if isinstance(value, list):
            items.extend(item for item in value if isinstance(item, dict))
    return items


def group_traces(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
    for event in events:
        grouped.setdefault(str(event["trace_id"]), []).append(event)

    traces: list[dict[str, Any]] = []
    for trace_id, trace_events in grouped.items():
        trace_events.sort(key=lambda event: str(event.get("started_at", "")))
        working_messages: list[dict[str, Any]] = []
        for event in trace_events:
            result = event.get("result")
            if event.get("method") == "get_working" and isinstance(result, dict):
                messages = result.get("messages")
                if isinstance(messages, list):
                    working_messages = [item for item in messages if isinstance(item, dict)]
                    break

        persisted_messages = _items_for_method(trace_events, "persist_dialog", "messages")
        episodes = _items_for_method(trace_events, "save_episodes", "episodes")
        facts = _items_for_method(trace_events, "save_semantics", "facts")
        policies = _items_for_method(trace_events, "save_agent_policy", "policies")
        cleared = any(
            event.get("method") == "clear_working" and event.get("status") == "ok"
            for event in trace_events
        )
        status = "error" if any(event.get("status") == "error" for event in trace_events) else "ok"
        traces.append(
            {
                "trace_id": trace_id,
                "user_id": _first_argument(trace_events, "user_id"),
                "session_id": _first_argument(trace_events, "session_id"),
                "started_at": trace_events[0].get("started_at"),
                "status": status,
                "duration_ms": round(
                    sum(float(event.get("duration_ms") or 0) for event in trace_events), 3
                ),
                "working_messages": working_messages,
                "persisted_messages": persisted_messages,
                "episodes": episodes,
                "facts": facts,
                "policies": policies,
                "cleared": cleared,
                "events": trace_events,
            }
        )
    return traces


_HTML = r"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Трейсы памяти агента</title>
  <style>
    :root { color-scheme: light dark; --bg:#f5f6f8; --panel:#fff; --text:#17212b; --muted:#65717d; --line:#d9dfe5; --read:#2563eb; --write:#138a52; --delete:#c13a32; --error:#b42318; --code:#eef1f4; }
    @media (prefers-color-scheme: dark) { :root { --bg:#111418; --panel:#1a1f25; --text:#edf2f7; --muted:#a8b2bd; --line:#343c45; --read:#6da2ff; --write:#55c98a; --delete:#ff7b72; --error:#ff7b72; --code:#111418; } }
    * { box-sizing:border-box; }
    body { margin:0; background:var(--bg); color:var(--text); font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }
    main { max-width:1180px; margin:auto; padding:28px 18px 48px; }
    h1,h2,h3,p { margin-top:0; }
    h1 { margin-bottom:5px; font-size:28px; }
    h2 { font-size:18px; }
    h3 { font-size:15px; margin-bottom:8px; }
    code,pre { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; }
    select,button { font:inherit; color:inherit; }
    select { width:100%; padding:9px 10px; border:1px solid var(--line); border-radius:7px; background:var(--panel); }
    button { padding:7px 10px; border:1px solid var(--line); border-radius:7px; background:var(--panel); cursor:pointer; }
    .muted { color:var(--muted); }
    .livebar { display:flex; gap:12px; align-items:center; flex-wrap:wrap; margin-top:18px; }
    .livebar label { display:flex; gap:6px; align-items:center; }
    .live-status.error { color:var(--error); }
    .toolbar { display:grid; grid-template-columns:minmax(220px,1fr) minmax(220px,1fr) auto; gap:14px; align-items:end; margin:22px 0; }
    .toolbar label { display:grid; gap:6px; }
    .meta { text-align:right; color:var(--muted); }
    .states { display:grid; grid-template-columns:1fr 42px 1fr; gap:12px; align-items:stretch; }
    .card { background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:16px; min-width:0; }
    .arrow { display:grid; place-items:center; color:var(--muted); font-size:24px; }
    .big { font-size:23px; font-weight:650; margin-bottom:10px; }
    .memory-list { margin:0; padding-left:20px; }
    .memory-list li + li { margin-top:5px; }
    .events { display:flex; gap:8px; overflow-x:auto; padding:18px 0 8px; }
    .event { flex:0 0 auto; padding:9px 11px; border:1px solid var(--line); border-radius:8px; background:var(--panel); cursor:pointer; text-align:left; }
    .event[aria-pressed="true"] { outline:2px solid var(--read); outline-offset:1px; }
    .event.error { color:var(--error); }
    .dot { display:inline-block; width:8px; height:8px; border-radius:50%; margin-right:6px; background:var(--read); }
    .dot.write { background:var(--write); }
    .dot.delete { background:var(--delete); }
    .event small { display:block; margin-top:3px; color:var(--muted); }
    .detail { margin-top:10px; }
    .detail-head { display:flex; justify-content:space-between; gap:12px; align-items:start; }
    .status { color:var(--write); }
    .status.error { color:var(--error); }
    .detail-grid { display:grid; grid-template-columns:1fr 1fr; gap:14px; }
    pre { max-height:330px; overflow:auto; margin:6px 0 0; padding:12px; border-radius:7px; background:var(--code); white-space:pre-wrap; overflow-wrap:anywhere; }
    .empty { color:var(--muted); font-style:italic; }
    @media (max-width:720px) { .toolbar,.states,.detail-grid { grid-template-columns:1fr; } .meta { text-align:left; } .arrow { transform:rotate(90deg); } }
  </style>
</head>
<body>
<main>
  <h1>Память агента</h1>
  <p class="muted">Что было прочитано, сохранено и очищено в каждом трейсе</p>

  <div class="livebar" id="live-controls" hidden>
    <button type="button" id="refresh">Обновить</button>
    <label><input type="checkbox" id="auto-refresh" checked> Автообновление</label>
    <span class="muted live-status" id="live-status">Загрузка данных...</span>
  </div>

  <div class="toolbar">
    <label>Сессия<select id="session-select"></select></label>
    <label>Трейс<select id="trace-select"></select></label>
    <div class="meta" id="trace-meta"></div>
  </div>

  <section class="states">
    <div class="card">
      <h2>Рабочая память до</h2>
      <div class="big" id="before-count"></div>
      <ul class="memory-list" id="before-list"></ul>
    </div>
    <div class="arrow" aria-hidden="true">→</div>
    <div class="card">
      <h2>Долговременная память после</h2>
      <div class="big" id="after-count"></div>
      <ul class="memory-list" id="after-list"></ul>
    </div>
  </section>

  <div class="events" id="events" aria-label="Операции памяти"></div>

  <section class="card detail" aria-live="polite">
    <div class="detail-head">
      <div><h2 id="event-name"></h2><div class="muted" id="event-target"></div></div>
      <div class="status" id="event-status"></div>
    </div>
    <div class="detail-grid">
      <div><h3>Аргументы</h3><pre id="event-arguments"></pre></div>
      <div><h3>Результат или ошибка</h3><pre id="event-result"></pre></div>
    </div>
  </section>
</main>
<script>
let traces = __TRACE_DATA__;
const snapshotUrl = __SNAPSHOT_URL__;
let currentRevision = null;
let refreshTimer = null;
const $ = id => document.getElementById(id);
const sessionSelect = $('session-select');
const traceSelect = $('trace-select');

function short(value, limit=90) {
  const text = String(value ?? '');
  return text.length > limit ? text.slice(0, limit - 1) + '…' : text;
}
function addListItem(list, text) {
  const item = document.createElement('li');
  item.textContent = text;
  list.append(item);
}
function showEmpty(list, text) {
  const item = document.createElement('li');
  item.className = 'empty';
  item.textContent = text;
  list.append(item);
}
function eventOutput(event) {
  if (event.error) return event.error;
  return event.result;
}
function renderEvent(trace, index) {
  const event = trace.events[index];
  $('event-name').textContent = `${index + 1}. ${event.method}`;
  $('event-target').textContent = event.target || '';
  $('event-status').textContent = `${event.status} · ${Number(event.duration_ms || 0).toFixed(3)} ms`;
  $('event-status').className = `status ${event.status === 'error' ? 'error' : ''}`;
  $('event-arguments').textContent = JSON.stringify(event.arguments ?? null, null, 2);
  $('event-result').textContent = JSON.stringify(eventOutput(event), null, 2);
  document.querySelectorAll('.event').forEach((button, buttonIndex) => {
    button.setAttribute('aria-pressed', String(buttonIndex === index));
  });
}
function renderTrace(index) {
  const trace = traces[index];
  $('trace-meta').textContent = `${trace.status} · ${trace.events.length} операций · ${trace.duration_ms.toFixed(3)} ms`;
  $('before-count').textContent = `${trace.working_messages.length} сообщений`;
  const before = $('before-list');
  before.replaceChildren();
  if (!trace.working_messages.length) showEmpty(before, 'Рабочая память не читалась или была пуста');
  trace.working_messages.forEach(message => addListItem(before, `${message.role || 'message'}: ${short(message.content)}`));

  const after = $('after-list');
  after.replaceChildren();
  const parts = [];
  if (trace.persisted_messages.length) parts.push(`диалог: ${trace.persisted_messages.length} сообщений`);
  if (trace.episodes.length) parts.push(`эпизоды: ${trace.episodes.length}`);
  if (trace.facts.length) parts.push(`факты: ${trace.facts.length}`);
  if (trace.policies.length) parts.push(`правила: ${trace.policies.length}`);
  $('after-count').textContent = parts.join(' · ') || 'Нет записей';
  trace.episodes.forEach(item => addListItem(after, `Эпизод: ${short(item.summary, 140)}`));
  trace.facts.forEach(item => addListItem(after, `Факт: ${short(item.fact, 140)}${item.confidence == null ? '' : ` · ${item.confidence}`}`));
  trace.policies.forEach(item => addListItem(after, `Правило: ${short(item.statement, 140)}`));
  if (trace.cleared) addListItem(after, 'Рабочая память очищена');
  if (!after.children.length) showEmpty(after, 'Этот трейс ничего не записал');

  const events = $('events');
  events.replaceChildren();
  trace.events.forEach((event, eventIndex) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = `event ${event.status === 'error' ? 'error' : ''}`;
    button.setAttribute('aria-pressed', String(eventIndex === 0));
    const dot = document.createElement('span');
    dot.className = `dot ${event.category || ''}`;
    const method = document.createTextNode(event.method || 'unknown');
    const timing = document.createElement('small');
    timing.textContent = `${event.category || 'other'} · ${Number(event.duration_ms || 0).toFixed(3)} ms`;
    button.append(dot, method, timing);
    button.addEventListener('click', () => renderEvent(trace, eventIndex));
    events.append(button);
  });
  renderEvent(trace, 0);
}

function sessionName(trace) {
  return trace.session_id || 'без session_id';
}
function renderSession(session, preferredTraceId=null) {
  traceSelect.replaceChildren();
  const matchingIndexes = [];
  traces.forEach((trace, index) => {
    if (sessionName(trace) !== session) return;
    matchingIndexes.push(index);
    const option = document.createElement('option');
    option.value = String(index);
    option.textContent = `${trace.status === 'error' ? 'ошибка · ' : ''}${trace.trace_id.slice(0, 8)} · ${trace.events.length} операций`;
    traceSelect.append(option);
  });
  const preferredIndex = matchingIndexes.find(index => traces[index].trace_id === preferredTraceId);
  const selectedIndex = preferredIndex ?? matchingIndexes[matchingIndexes.length - 1];
  traceSelect.value = String(selectedIndex);
  renderTrace(selectedIndex);
}

function renderEmpty() {
  sessionSelect.replaceChildren();
  traceSelect.replaceChildren();
  sessionSelect.disabled = true;
  traceSelect.disabled = true;
  $('trace-meta').textContent = 'В JSONL пока нет событий';
  $('before-count').textContent = '0 сообщений';
  $('before-list').replaceChildren();
  showEmpty($('before-list'), 'Рабочая память пока не читалась');
  $('after-count').textContent = 'Нет записей';
  $('after-list').replaceChildren();
  showEmpty($('after-list'), 'Долговременная память пока не менялась');
  $('events').replaceChildren();
  $('event-name').textContent = 'Событий пока нет';
  $('event-target').textContent = '';
  $('event-status').textContent = '';
  $('event-arguments').textContent = 'null';
  $('event-result').textContent = 'null';
}

function applyTraces(nextTraces, keepSelection=true) {
  const previousSession = keepSelection ? sessionSelect.value : null;
  const previousIndex = Number(traceSelect.value);
  const previousTraceId = keepSelection && traces[previousIndex] ? traces[previousIndex].trace_id : null;
  traces = nextTraces;
  if (!traces.length) {
    renderEmpty();
    return;
  }

  sessionSelect.disabled = false;
  traceSelect.disabled = false;
  sessionSelect.replaceChildren();
  const sessions = [...new Set(traces.map(sessionName))];
  sessions.forEach(session => {
    const option = document.createElement('option');
    option.value = session;
    const count = traces.filter(trace => sessionName(trace) === session).length;
    option.textContent = `${session} · ${count} ${count === 1 ? 'трейс' : 'трейсов'}`;
    sessionSelect.append(option);
  });
  const selectedSession = sessions.includes(previousSession)
    ? previousSession
    : sessionName(traces[traces.length - 1]);
  sessionSelect.value = selectedSession;
  renderSession(selectedSession, previousTraceId);
}

function formatUpdatedAt(value) {
  if (!value) return 'файл пока не создан';
  return `обновлено ${new Date(value).toLocaleTimeString('ru-RU')}`;
}

async function refreshTraces() {
  if (!snapshotUrl) return;
  const headers = currentRevision ? {'If-None-Match': `"${currentRevision}"`} : {};
  try {
    const response = await fetch(snapshotUrl, {headers, cache:'no-store'});
    if (response.status === 304) {
      $('live-status').textContent = 'Новых событий нет';
      $('live-status').className = 'muted live-status';
      return;
    }
    const snapshot = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(snapshot.detail || `HTTP ${response.status}`);
    if (snapshot.revision !== currentRevision) {
      applyTraces(snapshot.traces || [], currentRevision !== null);
      currentRevision = snapshot.revision;
    }
    $('live-status').textContent = snapshot.error || `${formatUpdatedAt(snapshot.updated_at)} · ${snapshot.event_count} событий`;
    $('live-status').className = `live-status ${snapshot.error ? 'error' : 'muted'}`;
  } catch (error) {
    $('live-status').textContent = `Ошибка обновления: ${error.message}`;
    $('live-status').className = 'live-status error';
  }
}

sessionSelect.addEventListener('change', () => renderSession(sessionSelect.value));
traceSelect.addEventListener('change', () => renderTrace(Number(traceSelect.value)));

if (snapshotUrl) {
  $('live-controls').hidden = false;
  $('refresh').addEventListener('click', refreshTraces);
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && $('auto-refresh').checked) refreshTraces();
  });
  refreshTraces();
  refreshTimer = setInterval(() => {
    if (!document.hidden && $('auto-refresh').checked) refreshTraces();
  }, 2000);
} else {
  applyTraces(traces, false);
}
</script>
</body>
</html>
"""


def render_report(events: list[dict[str, Any]]) -> str:
    traces = group_traces(events)
    data = json.dumps(traces, ensure_ascii=False, separators=(",", ":"))
    data = data.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return _render_html(data, None)


def render_dynamic_report(snapshot_url: str) -> str:
    return _render_html("[]", snapshot_url)


def _render_html(trace_data: str, snapshot_url: str | None) -> str:
    url = json.dumps(snapshot_url, ensure_ascii=False).replace("<", "\\u003c")
    return _HTML.replace("__TRACE_DATA__", trace_data).replace("__SNAPSHOT_URL__", url)


def write_report(input_path: str | Path, output_path: str | Path) -> Path:
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(render_report(load_events(input_path)), encoding="utf-8")
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description="Create an interactive memory trace report")
    parser.add_argument("input", type=Path, help="Path to memory trace JSONL")
    parser.add_argument("--output", "-o", type=Path, default=Path("memory-trace-report.html"))
    args = parser.parse_args()
    try:
        destination = write_report(args.input, args.output)
    except ReportError as exc:
        parser.error(str(exc))
    print(destination.resolve())


if __name__ == "__main__":
    main()
