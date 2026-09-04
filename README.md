# YAML memory tracer

Небольшой Python-пакет для трассировки памяти агента без правок внутри методов памяти. Он читает YAML, оборачивает указанные функции при запуске и пишет аргументы, результаты, ошибки и длительность в JSONL.

## Быстрое подключение к исходному стенду

Клонируйте репозиторий в корень проекта рядом с `app`:

```bash
git clone REPOSITORY_URL yaml-memory-tracer
python -m pip install -e ./yaml-memory-tracer
cp yaml-memory-tracer/examples/genai-invest-agent-memory.yaml memory-tracing.yaml
```

Добавьте установку трейcера в начало точки запуска до импортов агента:

```python
from memory_tracer import install_from_env

_memory_tracing = install_from_env()

from app.agent.runner import run_research
from app.orchestrator.graph import finalize_session
```

Задайте путь к конфигурации и запустите приложение:

```bash
export MEMORY_TRACING_CONFIG="$PWD/memory-tracing.yaml"
```

Трейсы появятся в `memory-traces/memory-traces.jsonl`.

## Другой агент

Скопируйте пример и замените импортируемые пути и методы:

```bash
cp yaml-memory-tracer/examples/generic.yaml memory-tracing.yaml
```

```yaml
boundaries:
  - target: my_agent.runner.run

memory:
  - target: my_agent.memory.MemoryStore
    methods:
      load_context: read
      save_message: write
```

Допустимые категории: `read`, `write`, `update`, `delete`, `transform`, `use`, `other`.

YAML не выполняет автоматический поиск памяти. В нём нужно перечислить реальные Python-пути. Цели должны импортироваться при запуске, а установка трейcера должна выполняться до импорта функций агента по имени.

## Проверка

```bash
python -m pip install -e './yaml-memory-tracer[dev]'
pytest yaml-memory-tracer/tests
```

Для статического HTML-отчёта:

```bash
memory-trace-report memory-traces/memory-traces.jsonl -o memory-trace-report.html
```

Отчёт знает структуру памяти исходного стенда. Для другого агента сырой JSONL работает без изменений, а человекочитаемые блоки отчёта могут потребовать адаптации.

## Docker

Установите пакет в образ, передайте `MEMORY_TRACING_CONFIG` контейнеру и примонтируйте каталог вывода. Текущая версия использует потоковую, но не межпроцессную блокировку. Для нескольких процессов задавайте отдельный JSONL каждому процессу.
