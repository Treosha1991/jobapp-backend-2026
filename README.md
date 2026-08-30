# JobHub backend

Backend JobHub и JobHub Support на Django. Здесь находятся публичный API
приложения, web-кабинет работодателя, административные страницы и проектная
логика JobHub Support.

## Перед началом работы

Прочитайте документы в таком порядке:

1. [AGENTS.md](AGENTS.md) — правила работы в репозитории.
2. [JobHub Support: единый источник истины](docs/JOBHUB_SUPPORT_SOURCE_OF_TRUTH.md) — актуальная продуктовая и техническая модель.
3. [Реестр документации](docs/DOCUMENTATION_INVENTORY.md) — статус остальных документов.
4. [Правило проверки текстов и переводов](docs/IMPORTANT_TEXT_RULES.md) — обязательная защита от появления `???` и смешанных языков.

## Стек

- Python 3.11;
- Django 5 и Django REST Framework;
- PostgreSQL на staging/production;
- SQLite допустим только для локальной разработки без `DATABASE_URL`;
- WhiteNoise для статических файлов;
- Render для развёртывания.

Точные версии зависимостей находятся в [requirements.txt](requirements.txt).

## Основные модули

- `jobs/` — основной JobHub: пользователи, вакансии, чаты и существующие API;
- `support/` — JobHub Support: кандидаты, работники, проекты, экипажи, жильё,
  транспорт, графики, часы, документы и права доступа;
- `config/` — настройки Django и корневые маршруты;
- `templates/` — web-интерфейс работодателя;
- `docs/` — документация и правила проекта.

Ключевые маршруты:

- `/api/` — существующий API JobHub;
- `/api/v2/support/` — API JobHub Support;
- `/employer/` — существующий кабинет работодателя;
- `/employer/support/` — рабочий web-кабинет JobHub Support;
- `/admin/` — Django admin.

## Актуальная модель JobHub Support

Основное управление построено по схеме project-first («от проекта»):

`организация → проект → экипаж → опубликованные дни → участники`

Проект задаёт объект работы. Экипаж объединяет водителя, автомобиль и
пассажиров. График публикуется для экипажа по выбранным календарным дням и
транслируется участникам. Точечные исключения работника не должны незаметно
переписывать график всего экипажа.

Подробные правила зафиксированы в
[JOBHUB_SUPPORT_SOURCE_OF_TRUTH.md](docs/JOBHUB_SUPPORT_SOURCE_OF_TRUTH.md).

## Локальный запуск

Из каталога `backend`:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

Локально приложение откроется на `http://127.0.0.1:8000/`.

### Feature flags

JobHub Support и project-first режим включаются явно:

```powershell
$env:SUPPORT_FEATURE_ENABLED = "1"
$env:SUPPORT_PROJECT_FIRST_ENABLED = "1"
python manage.py runserver
```

`SUPPORT_PROJECT_FIRST_RESET_ALLOWED=1` разрешает опасные тестовые операции
очистки. Не включайте его на production и не используйте без понятного плана
восстановления данных.

## Переменные окружения

| Переменная | Назначение |
| --- | --- |
| `SECRET_KEY` | секрет Django |
| `DEBUG` | режим отладки |
| `DATABASE_URL` | подключение к PostgreSQL |
| `ALLOWED_HOSTS` | разрешённые домены |
| `SUPPORT_FEATURE_ENABLED` | включает JobHub Support |
| `SUPPORT_PROJECT_FIRST_ENABLED` | включает актуальную project-first механику |
| `SUPPORT_PROJECT_FIRST_RESET_ALLOWED` | разрешает тестовую очистку данных |
| `SUPPORT_TRANSLATION_PROVIDER` | провайдер перевода; по умолчанию отключён |

Не добавляйте реальные секреты и файлы `.env` в Git.

## Проверка изменений

```powershell
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test support
```

Для небольшой правки допустим targeted test («целевой тест»), но перед
объединением большой функции нужен полный релевантный набор тестов.

## Развёртывание

Render использует [build.sh](build.sh): устанавливает зависимости, собирает
статику и выполняет миграции. Staging и production — разные среды. Правка в
репозитории не означает автоматическое разрешение на production deploy
(«производственное развёртывание»).

Перед staging deploy:

1. проверить миграции и тесты;
2. проверить feature flags;
3. убедиться, что изменения не затрагивают основной JobHub;
4. выполнить manual acceptance test («ручную приёмку») в staging.

## Тексты и переводы

Любой пользовательский текст обязан пройти проверку UTF-8 и всех поддерживаемых
языков. Нельзя оставлять сырые ключи, английские внутренние значения или `???`.
Главное правило: [IMPORTANT_TEXT_RULES.md](docs/IMPORTANT_TEXT_RULES.md).

## Технический долг

Technical debt («технический долг») ведётся явно и не маскируется случайными
рефакторингами. Известные ограничения и незавершённые области смотрите в
[реестре документации](docs/DOCUMENTATION_INVENTORY.md) и профильных документах
со статусом `PARTIAL`.

## Передача работы

В handoff («передаче работы») укажите:

- что изменено;
- какие миграции и feature flags нужны;
- какие проверки выполнены;
- что осталось непроверенным;
- затрагивались ли staging, TestFlight или production.
