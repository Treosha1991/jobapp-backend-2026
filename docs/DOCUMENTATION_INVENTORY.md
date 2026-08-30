# JobHub Support: реестр документации

**Проверено:** 26.08.2026
**Область:** `backend` + `mobile`, ветка `feature/jobhub-support-staging`
**Назначение:** единый `documentation inventory` («реестр документации»), который показывает, каким файлам можно доверять при продолжении разработки.

Этот файл не заменяет продуктовую и техническую документацию. Канонические
правила находятся в `backend/docs/JOBHUB_SUPPORT_SOURCE_OF_TRUTH.md`. Реестр
определяет статус остальных документов и предупреждает, где старые правила
нельзя переносить в новый код.

## Статусы

- `ACTIVE` («актуальный») — правила можно использовать сейчас. Если код и
  документ расходятся, расхождение нужно зарегистрировать и исправить.
- `PARTIAL` («частично актуальный») — часть правил верна, но документ требует
  обновления или содержит старые разделы. Нельзя использовать без сверки с
  актуальным кодом и каноническими документами.
- `LEGACY` («устаревший») — исторический материал. Нельзя использовать как
  основание для новой разработки.
- `PLANNED` («запланированный») — целевой сценарий будущего. Он не доказывает,
  что функция уже реализована.

## Порядок доверия

Продуктовые и архитектурные решения принимаются в таком порядке:

1. подтверждённые владельцем продукта правила из текущего обсуждения;
2. `backend/docs/JOBHUB_SUPPORT_SOURCE_OF_TRUTH.md`;
3. специализированные документы со статусом `ACTIVE`;
4. документы со статусом `PARTIAL` только после сверки.

Фактический код и тесты ветки `feature/jobhub-support-staging` показывают
`implementation state` («что реально реализовано»), но не меняют продуктовые
правила молча. Расхождение кода и канонического документа регистрируется как
ошибка или технический долг.

Ни один файл со статусом `LEGACY` или `PLANNED` не является `source of truth`
(«единым источником истины») для текущей реализации.

## Каноническая операционная модель

Текущая основа JobHub Support:

`project → crew → calendar date → shift → crew membership`

То есть работодатель управляет проектом, экипажем и конкретными календарными
днями. Пассажир не закрепляется навсегда за водителем. Его участие относится к
экипажу и датам. Старые `ShiftTemplate`, `RouteDraft`, recurring batches и
назначения через черновики маршрутов не должны возвращаться в новые экраны или
новый API.

## Backend

| Документ | Статус | Как использовать / что исправить |
|---|---|---|
| `backend/AGENTS.md` | `ACTIVE` | Обязательные инструкции для любых изменений backend: project-first, безопасность, staging, тесты и порядок работы с документацией. |
| `backend/docs/DOCUMENTATION_INVENTORY.md` | `ACTIVE` | Этот реестр. Обновлять при создании, архивировании или смене статуса документа. |
| `backend/docs/JOBHUB_SUPPORT_SOURCE_OF_TRUTH.md` | `ACTIVE` | Канонические продуктовые границы, сущности, статусы, инварианты и состав MVP. Первый документ для любого продолжения JobHub Support. |
| `backend/docs/jobhub_support_project_first_architecture.md` | `ACTIVE` | Специализированная архитектурная основа project-first. Использовать вместе с каноническим документом; при расхождении главнее source of truth. |
| `backend/docs/IMPORTANT_TEXT_RULES.md` | `ACTIVE` | Обязательные правила UTF-8, локализации и полей даты/времени. Применять ко всем новым текстам сайта и приложения. |
| `backend/docs/jobhub_board_delegated_publishing.md` | `ACTIVE` | Актуален для отдельного модуля публикаций JobHub Board. Не смешивать с JobHub Support. |
| `backend/docs/WEB_CURRENT_STATE.md` | `ACTIVE` | Проверенное на 26.08.2026 состояние employer web workspace, связь с mobile, границы staging и доказанные автоматическими тестами области. |
| `backend/docs/WEB_ACCEPTANCE_MATRIX.md` | `ACTIVE` | Общая web/mobile матрица: `AUTO PASS`, ручные проверки, частичное покрытие и внешние блокеры перед TestFlight/пилотом. |
| `backend/docs/TECHNICAL_DEBT.md` | `ACTIVE` | Актуальный реестр открытого и закрытого долга: legacy API, права, производительность, mobile tests, retry/idempotency, push, перевод, ошибки и device acceptance. |
| `backend/docs/employer_portal_notes.md` | `PLANNED` | Заметка о будущем кабинете и ссылке с сайта. Не использовать как описание текущего Employer Workspace. |
| `backend/docs/employer_vacancy_form_plan.md` | `PLANNED` | План формы вакансии. Реальное состояние формы и API нужно проверять отдельно; сам файл не подтверждает реализацию. |
| `backend/README.md` | `ACTIVE` | Актуальная точка входа backend: архитектура, локальный запуск, feature flags, проверки, staging и правила безопасной передачи работы. |

## Mobile: основная спецификация JobHub Support 1.0.5

| Документ | Статус | Как использовать / что исправить |
|---|---|---|
| `mobile/docs/jobhub-support-1.0.5/README.md` | `ACTIVE` | Точка входа в каталог: порядок чтения, актуальные документы, PARTIAL/LEGACY/PLANNED группы и граница готовности. |
| `00-product-foundation-and-pilot-readiness.md` | `ACTIVE` | Актуальная краткая продуктовая карта: границы сервиса, независимые состояния, путь кандидата, состав MVP и условия пилота. Нормативный приоритет явно передан source of truth. |
| `01-subscription-and-employer-access.md` | `PARTIAL` | Продуктовое правило общей подписки и фактический ручной access grant актуальны. Покупка, receipt validation, продление и отмена явно отделены как `PLANNED`. |
| `02-statuses-and-journeys.md` | `ACTIVE` | Сверен с `SupportApplication`, `ApplicationDecisionEvent`, `SupportConnection` и `EmploymentExclusivityLock`; разделяет заявку, оформление и активацию работника. |
| `03-roles-and-permissions.md` | `ACTIVE` | Сверен с текущими permission codes, членством организации, делегированием, `WorkerAccessScope`, экспортами и приватными чатами. |
| `04-employer-bot-and-application.md` | `PARTIAL` | Анкета v2, рассмотрение, уточнение, оформление и 60-минутная повторная подача актуальны. Самостоятельный конструктор фирменного бота явно остаётся `PLANNED`. |
| `05-documents-data-and-safety.md` | `ACTIVE` | Email-сценарий, отсутствие хранения файлов и понятные статусы соответствуют принятому MVP. Нужно только добавить последние уведомления и место отображения запроса в worker account. |
| `06-mvp-demo-plan.md` | `PARTIAL` | Цель демонстрации полезна, но конкретный сценарий заметно старше текущего кабинета, проектов, экипажей, оформления и табеля. |
| `07-future-employer-document-portal.md` | `PLANNED` | Отдельный безопасный портал документов — будущее развитие, не текущая функция. |
| `08-coordination-and-operations.md` | `ACTIVE` | Синхронизирован с этапами связи, фактическими правами, карточкой работника, задачами, объявлениями и уведомлениями. |
| `09-transport-and-driver.md` | `ACTIVE` | Описывает project-first экипаж, состав по датам, автопарк, отсутствие и замену водителя; RouteDraft вынесен в явный `LEGACY`. |
| `10-access-lifecycle-and-archive.md` | `ACTIVE` | Сверен с временными grant, этапами `SupportConnection`, exclusivity lock и архивом. Автоматический billing/offboarding вынесен в `PLANNED`. |
| `11-research-and-mvp-architecture.md` | `LEGACY` | Сохраняется как исследовательский контекст. Логическая модель маршрутов и ранняя MVP-архитектура заменены project-first реализацией. |
| `12-work-time-absence-and-exit-requests.md` | `ACTIVE` | Сверен с `ProjectCrewShift`, `WorkTimeEntry`, точными статусами, web-табелем, CSV и типами `WorkerRequest`. |
| `13-client-confirmation-and-payroll-preparation.md` | `PLANNED` | Презентационная перспектива подтверждения часов компанией-клиентом. Не входит в текущий MVP. |
| `14-client-company-workspace-and-staffing-requests.md` | `PLANNED` | Будущий B2B-кабинет принимающей компании. Не является текущей ролью работодателя JobHub Support. |
| `15-employer-workspace-and-registries.md` | `ACTIVE` | Синхронизирован с текущим header, заявками/оформлением, работниками, проектами, экипажами, жильём, автопарком, временем и чатами. |
| `16-mobile-workspace-mode.md` | `ACTIVE` | Сверен с текущими worker/staff workspace, Workers, Projects, Applications, Fleet, Housing, time, chats и worker card. |
| `17-templates-and-batch-actions.md` | `LEGACY` | Recurring shift batch и ShiftTemplate нельзя использовать в новых функциях. Исторические идеи массового выбора дат можно сохранить только после переписывания под project-first. |
| `18-work-chats-and-translation.md` | `ACTIVE` | Сверен с точными приватными парами, типами разговоров, read/unread, групповым push и мобильной навигацией. API перевода есть, provider отключён и честно помечен неготовым. |
| `19-subscription-status-and-sponsored-access.md` | `PARTIAL` | Ручной grant JobHub на 7/14/30 дней реализован. Apple/Google billing, менеджерский extension workflow и sponsored access остаются `PLANNED`. |
| `20-finance-information-and-advance-requests.md` | `PLANNED` | Финансы и авансы не входят в текущий основной пилот. Нельзя показывать как готовый модуль. |
| `21-manual-employer-onboarding.md` | `ACTIVE` | Ручная проверка и подключение первой фирмы соответствует принятому процессу MVP. |
| `22-first-operational-pilot-checklist.md` | `ACTIVE` | Release gate переписан под application → onboarding → worker и project-first проект/экипаж/календарь; содержит ручные device/push/offboarding проверки. |
| `23-technical-blueprint.md` | `ACTIVE` | Архитектура 2.0: каноническая модель, домены, backend layers, распределение web/mobile, надёжность записей, среды и критерии передачи. Детали endpoints остаются в `24-mobile-api-contract.md`. |
| `24-mobile-api-contract.md` | `ACTIVE` | Версионированный mobile API contract 1.0: реализованные backend/Flutter семьи, project-first команды, ошибки, idempotency и актуальный технический долг. |

## Mobile: другие документы и operational files

| Документ / файл | Статус | Как использовать / что исправить |
|---|---|---|
| `mobile/AGENTS.md` | `ACTIVE` | Обязательные инструкции для Flutter: API contract, локализация, роли, TestFlight/staging и проверки. |
| `mobile/docs/closed-testing-log.md` | `ACTIVE` | Исторический журнал закрытого тестирования. Новые проверки добавлять хронологически; не использовать как продуктовую спецификацию. |
| `mobile/docs/release-1.0.4-required.md` | `LEGACY` | Исторический checklist релиза 1.0.4. Актуальные правила нужно переносить в текущие спецификации, а не продолжать этот файл. |
| `mobile/README.md` | `ACTIVE` | Актуальная точка входа Flutter-клиента: режимы приложения, запуск с явным API URL, локализация, проверки, Codemagic и TestFlight. |
| `mobile/codemagic.yaml` | `ACTIVE` | Фактическая конфигурация CI/build. Менять только вместе с проверкой сборки и секретов окружения. |
| `mobile/docs/branding/*` и release assets | `ACTIVE` | Отдельная область визуальных и публикационных материалов. Не является источником правил JobHub Support. |

## Подтверждённые противоречия

### 1. Старые шаблоны смен

Файл `17-templates-and-batch-actions.md` описывает `ShiftTemplate` и recurring
batch как старый путь. В текущей модели график задаётся для экипажа на
выбранные календарные даты. Документы `22` и `23` уже фиксируют актуальную
project-first модель.

### 2. Старые маршруты и черновики

Файл `11-research-and-mvp-architecture.md` и legacy-разделы документов `09` и
`15` упоминают RouteDraft, остановки и публикацию маршрута только для
распознавания старого кода. Эти операции не являются новым источником истины.

### 3. Привязка пассажира к водителю или рейсу

Старые документы допускают постоянную привязку пассажиров к водителю/маршруту.
Актуальное правило: участие относится к экипажу и календарным датам. Смена
водителя не должна сама по себе переносить или удалять пассажиров вне выбранных
дат.

### 4. Кандидат ошибочно выглядит работником

Часть старых документов и экранов смешивает заявку, оформление и карточку
работника. Актуальный путь разделяет:

`application → approval → onboarding/documents → worker activation`

До перевода в workers кандидат остаётся на странице заявок/оформления.

### 5. Пакет пути кандидата синхронизирован

Документы `00`, `02` и `03` сверены с source of truth, моделями и permission
codes и переведены в `ACTIVE`. Документы `01` и `04` оставлены `PARTIAL`, потому
что коммерческий billing lifecycle и самостоятельный конструктор фирменного
бота ещё не готовы. Внутри этих файлов текущая часть и `PLANNED` теперь
разделены явно.

## Последняя синхронизация пакетов

На 26.08.2026 синхронизированы и переведены в `ACTIVE`:

- путь кандидата и права: `00`, `02`, `03`;
- работник и координация: `08`;
- транспорт и экипажи: `09`;
- время и запросы: `12`;
- веб-кабинет работодателя: `15`;
- мобильные режимы работника и сотрудника: `16`.
- доступ, связь и архив: `10`;
- рабочие чаты, read/unread и push: `18`;
- операционный release gate пилота: `22`.

На этом documentation-only этапе API, модели, миграции, web UI, mobile UI,
staging и production не изменялись. Документ `19` намеренно оставлен
`PARTIAL`: текущий ручной grant описан отдельно от ещё не реализованного
коммерческого billing lifecycle и sponsored access.
