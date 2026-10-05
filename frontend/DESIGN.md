# Finance Aggregator frontend v1: реализованный дизайн-контракт

Этот документ фиксирует фактическую реализацию утверждённого refined direction.
Исходные эталоны остаются в `docs/design-concepts/frontend-v1/`; данный файл не
создаёт новую концепцию и не заменяет backend/OpenAPI contract.

## Визуальный характер

Интерфейс — спокойный рабочий финансовый кабинет: холодно-белый canvas,
тёмно-синяя навигация, бирюзовые активные состояния и humanist sans-serif во
всех уровнях иерархии. Основная композиция следует утверждённым PNG: четыре
самостоятельные метрики, крупные рабочие модули, линейные таблицы и короткие
corrective-action блоки. Графики используются только там, где backend
предоставляет подходящую структуру.

## Tokens

Основные CSS tokens находятся в `src/styles.css`:

| Token                      | Значение                         | Назначение                      |
| -------------------------- | -------------------------------- | ------------------------------- |
| `--canvas`                 | `#ffffff`                        | фон приложения                  |
| `--paper`                  | `#ffffff`                        | рабочие поверхности             |
| `--ink`                    | `#071a42`                        | основной тёмно-синий текст      |
| `--muted`                  | `#4f6288`                        | вторичная информация            |
| `--line`                   | `#dce4f0`                        | холодные разделители            |
| `--green`                  | `#007a78`                        | primary actions и selection     |
| `--green-soft`             | `#e4f5f3`                        | complete/ready                  |
| `--amber` / `--amber-soft` | `#ec8200` / `#fff3e2`            | partial/warning/stale           |
| `--red` / `--red-soft`     | `#e52d28` / `#fff0ef`            | error/unavailable/destructive   |
| `--blue-soft`              | `#edf4ff`                        | нейтральный informational state |
| `--sidebar`                | `195px` desktop, `92px` tablet   | navigation rail                 |
| `--shadow`                 | `0 10px 28px rgba(6,30,73,.055)` | только плавающие элементы       |

Цвет никогда не является единственным сигналом: status badges содержат icon и
русскую подпись, signed values имеют знак, unavailable выводится `Н/Д`, empty —
отдельной композицией.

## Типографика и плотность

- Основной стек: Avenir Next/Avenir/Segoe UI с system fallback; значения
  используют tabular numerals.
- `h1` и `h2`: тот же humanist sans, 650 weight, умеренно отрицательный tracking.
- Desktop `h1`: fluid `30–40px`; tablet сохраняет компактную рабочую иерархию.
- Secondary metadata и eyebrows: `9–11px`, повышенный tracking, uppercase только
  для навигационных и секционных labels.
- Forms имеют минимальную высоту `44px`, таблицы и метрики сохраняют более высокую
  плотность через линии и интервалы, а не через вложенные карточки.

## Primitives и композиция

- App shell: fixed тёмно-синий desktop sidebar, context topbar, RUB/USD и
  `Добавить данные`; tablet превращает sidebar в icon rail 92 px, mobile — в
  доступное drawer-меню.
- `PageHeader`, `Button`, `QualityBadge`, `StatePanel`, `QueryState` образуют
  минимальный общий слой primitives. Base UI используется для dialog с focus
  management; Lucide — для семантических icons.
- Overview: четыре отдельные metric-поверхности, крупный category allocation,
  persistent attention list, крупнейшие holdings и доходы/расходы.
- Holdings/import review используют виртуализированные строки React Virtuoso;
  состояния строк остаются видимыми текстом и icon.
- Allocation: donut и exact-таблица фактической стоимости/weight, quality и
  denominator, затем отдельная поверхность system/custom categories и instrument
  override. Target editor не входит в MVP.
- Events: одна поверхность с четырьмя tabs, filters, chart и равноправной таблицей
  тех же buckets.

## Data states

- `complete`: значение известно и покрытие полное; check icon + текст.
- `partial`: значение известно частично; показаны included/excluded components и
  переход к corrective action.
- `unavailable`: backend вернул `null`; UI выводит `Н/Д`, но никогда `0`.
- `empty`: коллекция пуста или Ledger ещё не создан; отдельный StatePanel с первым
  действием.
- `stale`: данные доступны, но snapshot/observation устарел; отдельный warning
  label и recalculation/sync action.
- `error`: safe error envelope; raw import rows, UID, checksums и финансовые
  значения не попадают в browser logs.

Money, quantity, price, rate и weight остаются exact strings. `decimal.js`
используется только для точного presentation-форматирования. Recharts получает
`number` только для геометрии уже рассчитанных backend
values; таблица рядом сохраняет строковое значение и является доступной
альтернативой.

## Responsive contract

- `>1100px`: fixed rail 195px, desktop overview/import/allocation layouts.
- `761–1100px`: tablet icon rail 92px, переразложенные секции и
  сохранённая аналитическая иерархия без горизонтального page overflow.
- `<=760px`: одно-колоночная web adaptation; широкие финансовые таблицы получают
  локальный horizontal scroll, а не сжимают значения.
- Минимальная ширина документа — 320px. Отдельный mobile product в Phase 04 не
  заявлен.

`prefers-reduced-motion` сводит transitions/animations к практически нулевой
длительности. Focus rings видимы на inputs/selects; icon-only controls имеют
`aria-label`; tabs, dialog и pressed states используют нативную/ARIA семантику.

## Различия между references и runtime

- Демонстрационные суммы и `design-data.json` не попали в production bundle;
  композиция заполняется только public API data.
- Исторический portfolio-value/performance chart отсутствует: backend такого ряда
  не предоставляет. Ledger event series показаны только как события.
- При negative cash и малом allocation denominator backend может вернуть веса
  больше 100%; UI показывает их без скрытого clamp или пересчёта.
- В review действия определяются diagnostic codes OpenAPI, а не визуальным
  предположением: `match` появляется только для `instrument_match_required`.
- Long tables виртуализированы, поэтому точный вид количества строк зависит от
  viewport, но hierarchy и статусы соответствуют reference.

## Проверка

Фактические screenshots находятся в
`docs/design-concepts/frontend-v1/implementation/`. Desktop проверен при
`1440×1000`, tablet overview — при `1024×768`. Browser E2E дополнительно проверяет
persistent review после reload, confirm/recalculate, overview/holdings, empty
state, keyboard focus, page overflow и console errors.
