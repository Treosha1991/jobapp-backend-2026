document.addEventListener("DOMContentLoaded", () => {
  const config = window.JobHubAdmin || {};
  const isRussian = String(config.language || "").toLowerCase().startsWith("ru");
  const body = document.body;
  const header = document.getElementById("header");
  const menuToggle = document.querySelector(".jh-menu-toggle");
  const filterPanel = document.getElementById("changelist-filter");
  const choiceLabels = config.choiceLabels || {};

  // Django derives "Vacancys" from the model class name. Keep the English
  // operator chrome polished without changing model metadata (and creating a
  // database migration solely for an admin label).
  const labelsEn = {
    "delete selected vacancys": "Delete selected vacancies",
    "vacancys": "Vacancies",
  };

  const labelsRu = {
    "account deletion requests": "Удаление аккаунтов",
    "action": "Действие",
    "action time": "Время действия",
    "active": "Активно",
    "active devices": "Активные устройства",
    "active until": "Активно до",
    "add": "Добавить",
    "add another": "Добавить ещё",
    "добавить vacancy": "Добавить вакансию",
    "additional phone 1": "Дополнительный телефон 1",
    "additional phone 2": "Дополнительный телефон 2",
    "additional phone 3": "Дополнительный телефон 3",
    "alert delivery": "Доставка оповещения",
    "alert subscriptions": "Подписки на оповещения",
    "all": "Все",
    "are you sure?": "Подтвердите действие",
    "approved": "Одобрено",
    "approved at": "Одобрено",
    "approved by": "Одобрил",
    "attempts": "Попытки",
    "audience country codes": "Вакансия для граждан",
    "authentication and authorization": "Доступ и права",
    "author": "Автор",
    "balance": "Баланс",
    "blocked": "Заблокировано",
    "blocked by": "Заблокировал",
    "blocked user": "Заблокированный пользователь",
    "board code": "Код публикаций",
    "category": "Категория",
    "change": "Изменить",
    "change password": "Сменить пароль",
    "change message": "Изменения",
    "chat": "Чат",
    "chat conversations": "Диалоги",
    "chat messages": "Сообщения чатов",
    "chat reports": "Жалобы на чаты",
    "city": "Город",
    "city code": "Код города",
    "complainant": "Автор жалобы",
    "complaint": "Жалоба",
    "complaints": "Жалобы",
    "complaints filed": "Жалобы",
    "contact price": "Цена контактов",
    "content": "Содержание",
    "conversation": "Диалог",
    "country": "Страна",
    "created at": "Создано",
    "created by": "Создал",
    "date joined": "Дата регистрации",
    "date/time": "Дата и время",
    "deleted": "Удалено",
    "description": "Описание",
    "device token": "Токен устройства",
    "display name": "Имя",
    "driver license categories": "Водительские категории",
    "driver licence categories": "Водительские категории",
    "email": "Email",
    "email / phone": "Email / телефон",
    "email address": "Email",
    "email/phone": "Email / телефон",
    "filter": "Фильтр",
    "employment type": "Тип занятости",
    "enabled": "Включено",
    "event type": "Тип события",
    "expires at": "Истекает",
    "experience required": "Требуемый опыт",
    "failed at": "Ошибка",
    "failure reason": "Причина ошибки",
    "feed promotion": "Продвижение в ленте",
    "first name": "Имя",
    "handled by": "Обработал",
    "hide primary phone": "Скрыть основной телефон",
    "history": "История",
    "home": "Главная",
    "housing": "Жильё",
    "housing cost": "Стоимость жилья",
    "housing currency": "Валюта жилья",
    "housing period": "Период оплаты жилья",
    "housing type": "Тип жилья",
    "id": "ID",
    "is active": "Активно",
    "is approved": "Одобрено",
    "is staff": "Сотрудник",
    "is superuser": "Администратор",
    "jobs": "Работа",
    "language": "Язык",
    "last login": "Последний вход",
    "last name": "Фамилия",
    "last seen at": "Последняя активность",
    "location": "Локация",
    "message": "Сообщение",
    "moderation": "Модерация",
    "moderation attempt": "Попытка модерации",
    "moderator notifications": "Уведомления модераторов",
    "name": "Название",
    "note": "Примечание",
    "object": "Объект",
    "object id": "ID объекта",
    "operation": "Операция",
    "owner": "Владелец",
    "participants": "Участники",
    "password": "Пароль",
    "password change": "Смена пароля",
    "phone": "Телефон",
    "phone number": "Номер телефона",
    "phone verification": "Проверка телефона",
    "pin": "Закрепление",
    "pinned": "Закрепление",
    "pinned from": "Закреплено с",
    "pinned until": "Закреплено до",
    "platform": "Платформа",
    "price": "Цена",
    "primary phone": "Основной телефон",
    "profile": "Профиль",
    "promotion": "Продвижение",
    "promotion kind": "Тип продвижения",
    "published at": "Опубликовано",
    "purchase id": "ID покупки",
    "purchase status": "Статус покупки",
    "push devices": "Push-устройства",
    "reason": "Причина",
    "recipient": "Получатель",
    "reporter": "Автор жалобы",
    "resolved": "Решено",
    "resolved at": "Решено",
    "resolved by": "Решил",
    "revision": "Редакция",
    "role": "Роль",
    "roles": "Роли",
    "salary": "Зарплата",
    "salary currency": "Валюта зарплаты",
    "salary from": "Зарплата от",
    "salary hours month": "Часов в месяц",
    "salary tax type": "Тип налогообложения",
    "salary to": "Зарплата до",
    "sender": "Отправитель",
    "source": "Источник",
    "staff status": "Сотрудник",
    "status": "Статус",
    "store product": "Товар магазина",
    "subscription": "Подписка",
    "subscriptions": "Подписки",
    "telegram username": "Telegram 1",
    "telegram username 2": "Telegram 2",
    "telegram username 3": "Telegram 3",
    "title": "Название",
    "token": "Токен",
    "transaction id": "ID операции",
    "type": "Тип",
    "unlock requests": "Запросы на разблокировку",
    "updated at": "Обновлено",
    "user": "Пользователь",
    "user blocks": "Блокировки пользователей",
    "user id": "ID пользователя",
    "username": "Пользователь",
    "users": "Пользователи",
    "vacancies": "Вакансии",
    "vacancys": "Вакансии",
    "delete selected vacancys": "Удалить выбранные вакансии",
    "удалить выбранные vacancys": "Удалить выбранные вакансии",
    "vacancies total": "Вакансии",
    "vacancy": "Вакансия",
    "vacancy alerts": "Оповещения о вакансиях",
    "vacancy owner": "Работодатель",
    "vacancy for citizens": "Вакансия для граждан",
    "verified": "Подтверждено",
    "verified at": "Подтверждено",
    "verified employer": "Проверенный работодатель",
    "viber": "Viber",
    "wallet": "Баланс",
    "wallet balance": "Баланс",
    "whatsapp": "WhatsApp",
    "view site": "Открыть сайт",
    "log out": "Выйти",
    "old password": "Текущий пароль",
    "new password": "Новый пароль",
    "new password confirmation": "Повторите новый пароль",
    "recent actions": "Последние действия",
    "my actions": "Мои действия",
    "none available": "Нет записей",
  };

  const sectionsRu = {
    "main": "Основное",
    "salary": "Зарплата",
    "housing": "Жильё",
    "contacts": "Контакты",
    "feed promotion": "Продвижение в ленте",
    "moderation": "Модерация",
    "permissions": "Права доступа",
    "important dates": "Важные даты",
    "personal info": "Личные данные",
    "groups": "Группы",
    "authentication": "Аутентификация",
    "publishing": "Публикации",
  };

  const valuesRu = {
    "---------": "Выберите действие",
    "active": "Активно",
    "inactive": "Неактивно",
    "yes": "Да",
    "no": "Нет",
    "true": "Да",
    "false": "Нет",
    "pending": "Ожидает",
    "draft": "Черновик",
    "live": "Опубликовано",
    "paused": "На паузе",
    "rejected": "Отклонено",
    "approved": "Одобрено",
    "resolved": "Решено",
    "open": "Открыто",
    "closed": "Закрыто",
    "free": "Бесплатно",
    "paid": "Платно",
    "not provided": "Не предоставляется",
    "full time": "Полная занятость",
    "part time": "Частичная занятость",
    "contract": "Контракт",
    "temporary": "Временно",
    "internship": "Стажировка",
    "remote": "Удалённо",
    "standard": "Стандарт",
    "premium": "Premium",
    "vip": "VIP",
    "urgent": "Срочно",
    "android": "Android",
    "ios": "iOS",
    "staff": "Сотрудник",
    "superuser": "Администратор",
    "user": "Пользователь",
    "editing": "Редактируется",
    "not pinned": "Не закреплено",
    "pinned": "Закреплено",
    "verified": "Подтверждено",
    "unverified": "Не подтверждено",
    "enabled": "Включено",
    "disabled": "Выключено",
  };

  const normalize = (value) => String(value || "")
    .replace(/[\u2191\u2193]/g, "")
    .replace(/\s+/g, " ")
    .trim();

  const translate = (value, dictionaries = [labelsRu, sectionsRu]) => {
    const clean = normalize(value);
    if (!clean) return clean;
    const key = clean.toLowerCase().replace(/:$/, "");
    if (!isRussian) return labelsEn[key] || clean;
    for (const dictionary of dictionaries) {
      if (dictionary[key]) return dictionary[key];
    }
    if (/^add (.+)$/i.test(clean)) return `Добавить: ${translate(clean.replace(/^add /i, ""))}`;
    if (/^change (.+)$/i.test(clean)) return `Изменить: ${translate(clean.replace(/^change /i, ""))}`;
    if (/^delete selected (.+)$/i.test(clean)) return `Удалить выбранные: ${translate(clean.replace(/^delete selected /i, ""))}`;
    if (/^delete (.+)$/i.test(clean)) return `Удалить: ${translate(clean.replace(/^delete /i, ""))}`;
    if (/^history: (.+)$/i.test(clean)) return `История: ${translate(clean.replace(/^history: /i, ""))}`;
    if (/^select (.+) to change$/i.test(clean)) return `Выберите запись: ${translate(clean.replace(/^select /i, "").replace(/ to change$/i, ""))}`;
    if (/^select (.+) to view$/i.test(clean)) return `Выберите запись: ${translate(clean.replace(/^select /i, "").replace(/ to view$/i, ""))}`;
    return clean;
  };

  const translateValue = (value) => {
    const clean = normalize(value);
    if (!isRussian || !clean) return clean;
    const direct = valuesRu[clean.toLowerCase()];
    if (direct) return direct;
    const label = translate(clean);
    if (label !== clean) return label;
    if (/^pin selected vacancies for (\d+) days?$/i.test(clean)) {
      const days = clean.match(/(\d+)/)[1];
      return `Закрепить выбранные вакансии на ${days} дн.`;
    }
    if (/^remove pin/i.test(clean)) return "Снять закрепление";
    if (/^approve selected/i.test(clean)) return "Одобрить выбранные";
    if (/^reject selected/i.test(clean)) return "Отклонить выбранные";
    if (/^mark selected/i.test(clean)) return clean.replace(/^mark selected/i, "Отметить выбранные");
    return clean;
  };

  const renderedChoiceLookup = new Map();
  const englishChoiceLabels = choiceLabels.english || {};
  Object.keys(englishChoiceLabels).forEach((group) => {
    Object.entries(englishChoiceLabels[group] || {}).forEach(([code, sourceLabel]) => {
      const localizedLabel = choiceLabels[group]?.[code];
      if (!localizedLabel) return;
      renderedChoiceLookup.set(
        normalize(sourceLabel).toLowerCase(),
        normalize(localizedLabel),
      );
    });
  });

  const translateRenderedValue = (value) => {
    const clean = normalize(value);
    if (!isRussian || !clean) return clean;

    const breadcrumbValue = clean.match(/^›\s*(.+)$/);
    if (breadcrumbValue) {
      return `› ${translateRenderedValue(breadcrumbValue[1])}`;
    }

    const exact = renderedChoiceLookup.get(clean.toLowerCase());
    if (exact) return exact;

    const countedValue = clean.match(/^(.*?)(\s+\(\d+\))$/);
    if (countedValue) {
      const base = normalize(countedValue[1]);
      const translatedBase = renderedChoiceLookup.get(base.toLowerCase()) || translateValue(base);
      if (translatedBase !== base) return `${translatedBase}${countedValue[2]}`;
    }

    return translateValue(clean);
  };

  const choiceMapForField = (name) => {
    const fieldName = String(name || "").toLowerCase();
    if (fieldName.includes("audience_country") || fieldName === "country") return choiceLabels.countries;
    if (fieldName.includes("category")) return choiceLabels.categories;
    if (fieldName.includes("employment")) return choiceLabels.employment;
    if (fieldName.includes("experience")) return choiceLabels.experience;
    if (fieldName === "housing" || fieldName.includes("housing_type")) return choiceLabels.housing;
    if (fieldName === "source" || fieldName.endsWith("-source")) return choiceLabels.source;
    if (fieldName.includes("salary_tax")) return choiceLabels.salaryTax;
    return null;
  };

  const localizedChoice = (name, value, fallback) => {
    const mapping = choiceMapForField(name);
    return normalize(mapping?.[value] || fallback || value);
  };

  const replaceTextNode = (node, dictionaries) => {
    const original = node.nodeValue || "";
    const clean = normalize(original);
    if (!clean) return;
    const translated = translate(clean, dictionaries);
    if (translated === clean) return;
    const leading = original.match(/^\s*/)?.[0] || "";
    const trailing = original.match(/\s*$/)?.[0] || "";
    node.nodeValue = `${leading}${translated}${trailing}`;
  };

  const localizeTextNodes = (root, dictionaries = [labelsRu, sectionsRu]) => {
    if (!root) return;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode(node) {
        const parent = node.parentElement;
        if (!parent || parent.matches("script, style, textarea, input, option")) return NodeFilter.FILTER_REJECT;
        return normalize(node.nodeValue) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
      },
    });
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    nodes.forEach((node) => replaceTextNode(node, dictionaries));
  };

  const localizeRenderedValues = (root) => {
    if (!isRussian || !root) return;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode(node) {
        const parent = node.parentElement;
        if (!parent || parent.matches("script, style, textarea, input, option")) {
          return NodeFilter.FILTER_REJECT;
        }
        return normalize(node.nodeValue) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
      },
    });
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    nodes.forEach((node) => {
      const original = node.nodeValue || "";
      const clean = normalize(original);
      const translated = translateRenderedValue(clean);
      if (!translated || translated === clean) return;
      const leading = original.match(/^\s*/)?.[0] || "";
      const trailing = original.match(/\s*$/)?.[0] || "";
      node.nodeValue = `${leading}${translated}${trailing}`;
    });
  };

  const localizeAdminChrome = () => {
    if (!isRussian) {
      document.querySelectorAll(
        "h1, .breadcrumbs, #result_list thead th, .inline-group thead th, #changelist-filter",
      ).forEach((element) => localizeTextNodes(element));
      return;
    }

    document.querySelectorAll(
      ".form-row label, fieldset.module > h2, .inline-group > h2, .inline-related h3, "
      + "#result_list thead th, .inline-group thead th, #changelist-filter h2, #changelist-filter h3, "
      + ".object-tools, .submit-row, #toolbar, #changelist .actions, .breadcrumbs, .paginator",
    ).forEach((element) => localizeTextNodes(element));

    document.querySelectorAll("select option").forEach((option) => {
      const fieldName = option.closest("select")?.name || "";
      const translated = localizedChoice(
        fieldName,
        option.value,
        translateValue(option.textContent),
      );
      if (translated) option.textContent = translated;
    });

    document.querySelectorAll(".object-tools a.addlink").forEach((link) => {
      const modelName = config.modelFormLabel || config.modelLabel || "";
      link.textContent = modelName
        ? `${config.add || "Добавить"} ${modelName}`
        : (config.add || "Добавить");
    });

    localizeRenderedValues(document.querySelector(".breadcrumbs"));
    localizeRenderedValues(document.getElementById("changelist-filter"));
    document.querySelectorAll(".readonly").forEach((element) => localizeRenderedValues(element));

    const buttonLabels = {
      "save": "Сохранить",
      "save and add another": "Сохранить и добавить ещё",
      "save and continue editing": "Сохранить и продолжить",
      "save and view": "Сохранить и открыть",
      "delete": "Удалить",
      "go": "Выполнить",
      "search": "Найти",
      "log in": "Войти",
      "change my password": "Сменить пароль",
      "yes, i'm sure": "Да, удалить",
      "no, take me back": "Нет, вернуться",
    };

    document.querySelectorAll("input[type='submit'], button, .deletelink, .cancel-link").forEach((control) => {
      const original = control.matches("input") ? control.value : control.textContent;
      const clean = normalize(original);
      const translated = buttonLabels[clean.toLowerCase()] || translateValue(clean);
      if (!translated || translated === clean) return;
      if (control.matches("input")) control.value = translated;
      else control.textContent = translated;
    });

    const searchbar = document.getElementById("searchbar");
    if (searchbar) searchbar.placeholder = "Поиск";
  };

  const syncHeaderOffset = () => {
    document.documentElement.style.setProperty(
      "--jh-header-height",
      `${header?.offsetHeight || 64}px`,
    );
  };

  const closeMenu = () => {
    body.classList.remove("jh-menu-open");
    menuToggle?.setAttribute("aria-expanded", "false");
  };

  menuToggle?.addEventListener("click", () => {
    const open = body.classList.toggle("jh-menu-open");
    menuToggle.setAttribute("aria-expanded", String(open));
    requestAnimationFrame(syncHeaderOffset);
  });

  document.querySelectorAll("#jh-admin-menu a").forEach((link) => {
    link.addEventListener("click", closeMenu);
  });

  localizeAdminChrome();

  const multiChoicePanels = [];

  const enhanceMultiChoiceGroups = () => {
    document.querySelectorAll("form .form-row").forEach((row) => {
      const checkboxes = Array.from(row.querySelectorAll("input[type='checkbox']"))
        .filter((input) => !input.matches(".action-select, [name$='-DELETE']"));
      if (checkboxes.length < 8 || row.dataset.jhMultiChoice === "true") return;

      const fieldName = checkboxes[0].name;
      if (!fieldName || checkboxes.some((input) => input.name !== fieldName)) return;

      const escapedId = window.CSS?.escape
        ? CSS.escape(fieldName)
        : fieldName.replace(/[^a-zA-Z0-9_-]/g, "\\$&");
      const source = row.querySelector(`#id_${escapedId}`);
      if (!source || source.matches("input")) return;

      row.dataset.jhMultiChoice = "true";
      const isAudience = fieldName.includes("audience_country");
      const isDriver = fieldName.includes("driver_license") || fieldName.includes("driver_licence");
      const limit = isAudience ? 20 : (isDriver ? 3 : Number.POSITIVE_INFINITY);
      const shouldSearch = isAudience || checkboxes.length >= 12;

      const shell = document.createElement("div");
      shell.className = "jh-multichoice";

      const summary = document.createElement("button");
      summary.type = "button";
      summary.className = "jh-multichoice-summary";
      summary.setAttribute("aria-expanded", "false");

      const summaryText = document.createElement("span");
      const summaryIcon = document.createElement("span");
      summaryIcon.className = "jh-multichoice-chevron";
      summaryIcon.setAttribute("aria-hidden", "true");
      summary.append(summaryText, summaryIcon);

      const panel = document.createElement("div");
      panel.className = "jh-multichoice-panel";
      panel.hidden = true;

      const panelHeader = document.createElement("div");
      panelHeader.className = "jh-multichoice-head";
      const countLabel = document.createElement("strong");
      const closeButton = document.createElement("button");
      closeButton.type = "button";
      closeButton.className = "jh-multichoice-close";
      closeButton.textContent = "\u00d7";
      closeButton.setAttribute("aria-label", config.close || (isRussian ? "Закрыть" : "Close"));
      panelHeader.append(countLabel, closeButton);
      panel.appendChild(panelHeader);

      let searchInput = null;
      if (shouldSearch) {
        searchInput = document.createElement("input");
        searchInput.type = "search";
        searchInput.className = "jh-multichoice-search";
        searchInput.placeholder = isRussian ? "Поиск по списку" : "Search list";
        searchInput.autocomplete = "off";
        panel.appendChild(searchInput);
      }

      const optionItems = [];
      checkboxes.forEach((input) => {
        const optionLabel = input.closest("label");
        if (!optionLabel) return;

        const valueLabel = localizedChoice(fieldName, input.value, optionLabel.textContent);
        Array.from(optionLabel.childNodes).forEach((node) => {
          if (node !== input) node.remove();
        });
        const text = document.createElement("span");
        text.className = "jh-multichoice-option-text";
        text.textContent = valueLabel;
        optionLabel.appendChild(text);
        optionLabel.classList.add("jh-multichoice-option");

        let item = optionLabel.parentElement;
        while (item?.parentElement && item.parentElement !== source && source.contains(item.parentElement)) {
          item = item.parentElement;
        }
        if (!item || item === source) item = optionLabel;
        item.dataset.jhSearch = valueLabel.toLocaleLowerCase(config.language || undefined);
        item.dataset.jhSort = valueLabel;
        optionItems.push(item);
      });

      if (isAudience) {
        [...new Set(optionItems)]
          .sort((left, right) => left.dataset.jhSort.localeCompare(
            right.dataset.jhSort,
            config.language || undefined,
            { sensitivity: "base" },
          ))
          .forEach((item) => source.appendChild(item));
      }

      source.classList.add("jh-multichoice-options");
      source.parentNode.insertBefore(shell, source);
      shell.append(summary, panel);
      panel.appendChild(source);

      const setOpen = (open) => {
        multiChoicePanels.forEach((entry) => {
          if (entry.shell === shell || !open) return;
          entry.panel.hidden = true;
          entry.shell.classList.remove("is-open");
          entry.summary.setAttribute("aria-expanded", "false");
        });
        panel.hidden = !open;
        shell.classList.toggle("is-open", open);
        summary.setAttribute("aria-expanded", String(open));
        if (open && searchInput) requestAnimationFrame(() => searchInput.focus());
      };

      const updateState = () => {
        const selected = checkboxes.filter((input) => input.checked).length;
        const selectedWord = isRussian ? "Выбрано" : "Selected";
        const limitText = Number.isFinite(limit) ? ` / ${limit}` : "";
        summaryText.textContent = `${selectedWord}: ${selected}${limitText}`;
        countLabel.textContent = Number.isFinite(limit)
          ? `${selectedWord}: ${selected} из ${limit}`
          : `${selectedWord}: ${selected}`;

        checkboxes.forEach((input) => {
          input.disabled = !input.checked && Number.isFinite(limit) && selected >= limit;
          input.closest(".jh-multichoice-option")?.classList.toggle("is-selected", input.checked);
          input.closest(".jh-multichoice-option")?.classList.toggle("is-disabled", input.disabled);
        });
      };

      summary.addEventListener("click", () => setOpen(panel.hidden));
      closeButton.addEventListener("click", () => setOpen(false));
      checkboxes.forEach((input) => input.addEventListener("change", updateState));
      searchInput?.addEventListener("input", () => {
        const query = normalize(searchInput.value).toLocaleLowerCase(config.language || undefined);
        [...new Set(optionItems)].forEach((item) => {
          item.hidden = Boolean(query && !item.dataset.jhSearch.includes(query));
        });
      });

      if (row.querySelector(".errors, .errorlist")) setOpen(true);
      updateState();
      multiChoicePanels.push({ shell, summary, panel, setOpen });
    });
  };

  enhanceMultiChoiceGroups();

  const enhanceResultTable = (table) => {
    if (!table || table.dataset.jhEnhanced === "true") return;
    table.dataset.jhEnhanced = "true";

    const headers = Array.from(table.querySelectorAll("thead th")).map((headerCell) => {
      const labelSource = headerCell.cloneNode(true);
      labelSource.querySelectorAll(".sortoptions, .clear").forEach((element) => element.remove());
      const text = translate(labelSource.textContent);
      headerCell.dataset.jhLabel = text;
      return text;
    });

    table.querySelectorAll("tbody tr").forEach((row) => {
      const cells = Array.from(row.children).filter((cell) => cell.matches("td, th"));
      let visibleIndex = 0;

      cells.forEach((cell, index) => {
        const label = headers[index] || "";
        cell.dataset.label = label;
        localizeRenderedValues(cell);

        if (cell.classList.contains("action-checkbox")) {
          cell.classList.add("jh-card-select");
          return;
        }

        if (visibleIndex === 0) cell.classList.add("jh-cell-primary");
        if (visibleIndex >= 5) cell.classList.add("jh-cell-extra");
        visibleIndex += 1;
      });

      if (visibleIndex > 5) {
        const toggleCell = document.createElement("td");
        toggleCell.className = "jh-row-toggle-cell";
        toggleCell.colSpan = Math.max(cells.length, 1);
        const toggle = document.createElement("button");
        toggle.type = "button";
        toggle.className = "jh-row-toggle";
        toggle.textContent = config.more || (isRussian ? "Ещё" : "More");
        toggle.setAttribute("aria-expanded", "false");
        toggle.addEventListener("click", () => {
          const expanded = row.classList.toggle("jh-row-expanded");
          toggle.textContent = expanded
            ? (config.less || (isRussian ? "Скрыть" : "Less"))
            : (config.more || (isRussian ? "Ещё" : "More"));
          toggle.setAttribute("aria-expanded", String(expanded));
        });
        toggleCell.appendChild(toggle);
        row.appendChild(toggleCell);
      }
    });
  };

  enhanceResultTable(document.getElementById("result_list"));

  document.querySelectorAll(".inline-group table").forEach((table) => {
    table.classList.add("jh-inline-table");
    const headers = Array.from(table.querySelectorAll("thead th")).map((cell) => translate(cell.textContent));
    table.querySelectorAll("tbody tr").forEach((row) => {
      row.classList.add("jh-inline-row");
      Array.from(row.children).forEach((cell, index) => {
        if (cell.matches("td, th")) {
          cell.dataset.label = headers[index] || "";
          localizeRenderedValues(cell);
        }
      });
    });
  });

  const enhanceFieldsets = () => {
    const fieldsets = Array.from(document.querySelectorAll("form fieldset.module"));
    if (fieldsets.length < 2) return;

    fieldsets.forEach((fieldset, index) => {
      const heading = fieldset.querySelector(":scope > h2");
      if (!heading || fieldset.dataset.jhCollapsible === "true") return;
      fieldset.dataset.jhCollapsible = "true";
      fieldset.classList.add("jh-collapsible");
      fieldset.classList.remove("collapse", "collapsed");

      const button = document.createElement("button");
      button.type = "button";
      button.className = "jh-fieldset-toggle";
      button.setAttribute("aria-label", isRussian ? "Свернуть или развернуть раздел" : "Collapse or expand section");
      button.innerHTML = "<span aria-hidden='true'></span>";
      heading.appendChild(button);

      const hasErrors = Boolean(fieldset.querySelector(".errors, .errorlist"));
      const startsCollapsed = index > 0 && !hasErrors;
      const setExpanded = (expanded) => {
        fieldset.classList.toggle("jh-collapsed", !expanded);
        button.setAttribute("aria-expanded", String(expanded));
      };

      setExpanded(!startsCollapsed);
      heading.addEventListener("click", (event) => {
        if (event.target.closest("a, input, select")) return;
        setExpanded(fieldset.classList.contains("jh-collapsed"));
      });
      button.addEventListener("click", (event) => {
        event.stopPropagation();
        setExpanded(fieldset.classList.contains("jh-collapsed"));
      });
    });
  };

  enhanceFieldsets();

  let closeFilters = () => {};
  if (filterPanel) {
    body.classList.add("jh-has-filters");
    const changelist = document.getElementById("changelist") || document.getElementById("content-main");
    const toolbar = document.createElement("div");
    toolbar.className = "jh-list-toolbar";

    const filterButton = document.createElement("button");
    filterButton.type = "button";
    filterButton.className = "jh-filter-toggle";
    filterButton.innerHTML = `<span aria-hidden="true">&#9776;</span><span>${config.filters || (isRussian ? "Фильтры" : "Filters")}</span>`;
    filterButton.setAttribute("aria-expanded", "false");
    toolbar.appendChild(filterButton);
    changelist?.parentNode?.insertBefore(toolbar, changelist);

    const backdrop = document.createElement("button");
    backdrop.type = "button";
    backdrop.className = "jh-filter-backdrop";
    backdrop.setAttribute("aria-label", config.close || (isRussian ? "Закрыть" : "Close"));
    body.appendChild(backdrop);

    const filterHeader = document.createElement("div");
    filterHeader.className = "jh-filter-header";
    const filterTitle = document.createElement("strong");
    filterTitle.textContent = config.filters || (isRussian ? "Фильтры" : "Filters");
    const closeButton = document.createElement("button");
    closeButton.type = "button";
    closeButton.className = "jh-filter-close";
    closeButton.textContent = "\u00d7";
    closeButton.setAttribute("aria-label", config.close || (isRussian ? "Закрыть" : "Close"));
    filterHeader.append(filterTitle, closeButton);
    filterPanel.prepend(filterHeader);

    const openFilters = () => {
      body.classList.add("jh-filter-open");
      filterButton.setAttribute("aria-expanded", "true");
      closeButton.focus();
    };

    closeFilters = () => {
      body.classList.remove("jh-filter-open");
      filterButton.setAttribute("aria-expanded", "false");
    };

    filterButton.addEventListener("click", openFilters);
    closeButton.addEventListener("click", closeFilters);
    backdrop.addEventListener("click", closeFilters);
  }

  const title = document.querySelector("#content > h1");
  if (title && config.modelLabel) {
    const formLabel = config.modelFormLabel || config.modelLabel;
    if (body.classList.contains("change-list")) {
      title.textContent = config.modelLabel;
    } else if (config.isAdd || body.classList.contains("add-form")) {
      title.textContent = `${isRussian ? "Добавить" : "Add"} ${formLabel}`;
    } else if (body.classList.contains("change-form")) {
      title.textContent = `${isRussian ? "Редактировать" : "Edit"} ${formLabel}`;
    }
    document.title = `${title.textContent} | JobHub`;
  }

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    multiChoicePanels.forEach((entry) => entry.setOpen(false));
    closeFilters();
    closeMenu();
  });

  document.addEventListener("click", (event) => {
    multiChoicePanels.forEach((entry) => {
      if (!entry.panel.hidden && !entry.shell.contains(event.target)) entry.setOpen(false);
    });
  });

  window.addEventListener("resize", () => {
    if (window.innerWidth >= 960) {
      closeFilters();
      closeMenu();
    }
    syncHeaderOffset();
  });

  syncHeaderOffset();
});
