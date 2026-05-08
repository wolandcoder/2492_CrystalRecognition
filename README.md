# АРМ автоматического захвата кристаллов — БЗПП

Программно-аппаратный комплекс для распознавания, позиционирования и захвата
полупроводниковых кристаллов вакуумной трубкой манипулятора.  
Разработан для **АО «Болховский завод полупроводниковых приборов»**.

---

## Что делает программа

1. Захватывает видеопоток с камеры над рабочим полем.
2. Обнаруживает кристаллы (~0.3 мм) в реальном времени: детектирует, трекирует, определяет нужную сторону (верх/низ).
3. Переводит координаты из пикселей в миллиметры мирового пространства.
4. Даёт Mach3 команду подъехать к ближайшему кристаллу и захватить его.

Полный GUI на Flet, офлайн-режим (видеофайл вместо камеры), горячие клавиши, ручной джог, калибровка масштаба и настройка параметров зрения из интерфейса.

---

## Стек

| Компонент | Технология |
|-----------|-----------|
| GUI | [Flet](https://flet.dev) ≥ 0.25 (десктоп, Flutter-бэкенд) |
| Компьютерное зрение | OpenCV ≥ 4.9 |
| Числа и матрицы | NumPy ≥ 1.26 |
| Управление манипулятором | Mach3 по TCP (`moch3_lib`) |
| Python | 3.10+ |

---

## Быстрый старт

```bash
# 1. Виртуальное окружение
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# 2. Зависимости
pip install -r requirements.txt

# 3. Настроить источник видео (интерактивно)
python3 main.py init

# 4. Или сразу через аргументы:
python3 main.py source camera --index 0
python3 main.py source file --path "/path/to/video.mp4" --loop true

# 5. Включить нейро-моды
python3 main.py mods enable --name __particle_centers
python3 main.py mods enable --name __particle_centers_grid
python3 main.py mods enable --name __particle_centers_nearest
python3 main.py mods enable --name __particle_centers_pin
python3 main.py mods enable --name __particle_centers_movement

# 6. Просмотр без GUI (только cv2)
python3 main.py view

# 7. Полный GUI
python3 gui.py
```

### Подключение манипулятора

1. На ПК с Mach3 запустить `python mach3_server.py` (папка `__core/__movement/moch3_lib/`).
2. Положить `Macropump.m1s` — подробности в `__core/__movement/moch3_lib/README.md`.
3. В GUI: **Настройки → Манипулятор Mach3** → задать IP, порт, смещение трубки.

---

## Структура проекта

```
BOLHOV_ZAVOD/
├── gui.py                  # основное приложение (Flet)
├── main.py                 # CLI: конфиг, моды, viewer
├── view_camera.py          # быстрый просмотр без GUI
├── config.json             # единый конфиг приложения
├── requirements.txt
├── assets/
│   └── logo.png
└── __core/
    ├── __camera/           # видеопоток и нейро-моды
    │   ├── config_models.py
    │   ├── config_service.py
    │   ├── video_source.py
    │   ├── viewer.py
    │   └── __neural/
    │       ├── base.py     # NeuralMod, FrameContext
    │       ├── manager.py  # NeuralManager: pipeline модов
    │       └── mods/
    │           ├── __hud_info.py
    │           ├── __particle_centers.py       # детекция + трекинг
    │           ├── __particle_centers_pin.py   # фиксация цели
    │           ├── __particle_centers_grid.py  # координатная сетка (px → мм)
    │           ├── __particle_centers_nearest.py
    │           └── __particle_centers_movement.py
    └── __movement/
        ├── __init__.py     # Mach3Bridge — высокоуровневая обёртка
        └── moch3_lib/      # низкоуровневый TCP-клиент к Mach3
```

---

## Архитектура

```
Камера
  └─► VideoSource ─► NeuralManager (pipeline модов)
                              │
                         FrameContext.shared
                              │
                       _StreamEngine (gui.py)
                         ├─► Flet UI (JPEG base64)
                         └─► Mach3Bridge
                                  │ G-code (TCP)
                             mach3_server
                                  │
                             Манипулятор
```

Подробные диаграммы (Mermaid) — в `ARCHITECTURE.md`.  
Описание внутреннего устройства по модулям — в `DEVELOPER.md`.  
Дорожная карта — в `ROADMAP.md`.

---

## Конфигурация

Все настройки хранятся в `config.json`:

| Секция | Что настраивает |
|--------|----------------|
| `app` | версия, режим окна, размер кристалла в мм, масштаб видео |
| `source` | тип источника (камера / файл), индекс или путь |
| `neural` | список включённых нейро-модов |
| `particle_grid` | масштаб px→мм, смещения начала координат |
| `particle_centers` | цвета отрисовки, пороги детекции |
| `movement` | IP и порт Mach3, смещение вакуумной трубки, скорости подачи |

Редактировать через GUI (**Настройки**) или через CLI (`python3 main.py init`).

---

## Нейро-моды

Каждый мод — класс `Mod(NeuralMod)` с методом `apply(frame, context)`.  
Моды общаются через `context.shared` (словарь), порядок имеет значение.

| Мод | Зависит от | Результат в shared |
|-----|-----------|-------------------|
| `__hud_info` | — | — |
| `__particle_centers` | — | `particle_centers` |
| `__particle_centers_grid` | `particle_centers` | `particle_world` |
| `__particle_centers_pin` | `particle_centers` | `particle_pin` |
| `__particle_centers_nearest` | `particle_centers`, `particle_pin` | `particle_nearest` |
| `__particle_centers_movement` | `particle_nearest`, `particle_pin` | `particle_movement` |

Добавить новый мод: создать `__core/__camera/__neural/mods/__my_mod.py`, определить `Mod(NeuralMod)`, добавить имя в `AVAILABLE_MODS` в `mods/__init__.py`.

---

## Горячие клавиши (GUI)

| Клавиши | Действие |
|---------|---------|
| `Ctrl/⌘ + Enter` | Старт / Стоп |
| `Пробел` | Пауза кадра |
| `Ctrl/⌘ + Shift + C` | Захват кадра |
| `Ctrl/⌘ + Shift + P` | ПИН (фиксация цели) |
| `Ctrl/⌘ + Shift + T` | Панель трансформации |
| `Ctrl/⌘ + Shift + M` | Панель манипулятора |
| `Ctrl/⌘ + Shift + X` | Перезапустить pipeline |
| Стрелки / PgUp / PgDn | Джог по X/Y/Z/A (панель Mach3) |
| `?` | Справка по горячим клавишам |

---

## CLI

```
python3 main.py config                             # показать текущий конфиг
python3 main.py init                               # интерактивная настройка
python3 main.py source camera --index 0 [--list]  # выбрать камеру
python3 main.py source file --path /p --loop true  # выбрать видеофайл
python3 main.py list-cameras                       # сканировать камеры
python3 main.py mods show                          # включённые моды
python3 main.py mods available                     # доступные моды
python3 main.py mods enable  --name __X            # включить мод
python3 main.py mods disable --name __X            # выключить мод
python3 main.py view                               # просмотр без GUI
```

---

## Калибровка масштаба

1. Задать `app.crystal_size_mm` — реальный размер кристалла (по умолчанию 0.3 мм).
2. Запустить поток с минимум двумя кристаллами в кадре.
3. Нажать иконку «линейка» в шапке GUI.
4. Программа вычислит `new_scale = crystal_mm / ref_px` и предложит сохранить.

Если манипулятор и камера не соосны — дополнительно поправить `origin_offset_x/y` и `vacuum_offset_x/y` в `config.json`.
