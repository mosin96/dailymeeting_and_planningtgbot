/* Team vacation calendar mini-app. Vanilla JS, no frameworks. */
(function () {
  "use strict";

  var tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
  if (tg) {
    tg.ready();
    tg.expand();
    try {
      var th = tg.themeParams || {};
      var root = document.documentElement.style;
      if (th.bg_color) root.setProperty("--bg", th.bg_color);
      if (th.text_color) root.setProperty("--text", th.text_color);
      if (th.secondary_bg_color) {
        root.setProperty("--card", th.secondary_bg_color);
        root.setProperty("--card-2", th.secondary_bg_color);
      }
      if (th.hint_color) root.setProperty("--muted", th.hint_color);
      if (th.button_color) root.setProperty("--accent", th.button_color);
      if (th.destructive_text_color) root.setProperty("--danger", th.destructive_text_color);
      if (tg.setHeaderColor && th.bg_color) tg.setHeaderColor(th.bg_color);
    } catch (e) {}
  }

  var PALETTE = [
    "#62a3e2", "#67c587", "#ecc367", "#ea8a77", "#c585e0",
    "#5fd0cc", "#e5a0f0", "#9eef53", "#f6a821", "#65b1e7",
    "#f57f9f", "#8f87e6"
  ];
  var WD = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];
  var MONTHS = [
    "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"
  ];

  var state = {
    today: null,          // ISO string from the server
    members: [],
    me: null,             // position of the member who opened the app
    selected: [],         // positions shown on the calendar (multi-select)
    selectionInitialized: false,
    viewInitialized: false,
    viewYear: 2016,
    viewMonth: 0,         // 0..11
    selStart: null,       // ISO string
    selEnd: null
  };

  var chatId = (function () {
    var fromStart = "";
    try {
      if (tg && tg.initDataUnsafe && tg.initDataUnsafe.start_param) {
        fromStart = String(tg.initDataUnsafe.start_param);
      }
    } catch (e) {}
    var qs = new URLSearchParams(window.location.search);
    var fromQuery = qs.get("chat") || qs.get("tgWebAppStartParam") || "";
    return fromStart || fromQuery;
  })();
  var initHeader = tg ? (tg.initData || "") : "";
  var $ = function (id) { return document.getElementById(id); };

  function isoOf(d) {
    var m = d.getMonth() + 1, day = d.getDate();
    return d.getFullYear() + "-" +
      (m < 10 ? "0" + m : m) + "-" +
      (day < 10 ? "0" + day : day);
  }
  function parseIso(s) {
    var parts = s.split("-");
    return new Date(+parts[0], +parts[1] - 1, +parts[2]);
  }
  function ruDate(iso) {
    var p = iso.split("-");
    return p[2] + "." + p[1] + "." + p[0];
  }
  function colorOf(position) {
    return PALETTE[position % PALETTE.length];
  }
  function memberByPosition(pos) {
    for (var i = 0; i < state.members.length; i++) {
      if (state.members[i].position === pos) return state.members[i];
    }
    return null;
  }
  function memberName(pos) {
    var m = memberByPosition(pos);
    return m ? m.name : "?";
  }

  function setHint(msg) { $("hint").textContent = msg; }

  function defaultHint() {
    if (state.selected.length === 1) {
      return "Показаны отпуска: " + memberName(state.selected[0]) + ". Нажмите на день, чтобы добавить отпуск.";
    }
    if (state.selected.length === 0) return "Выберите участников в списке сверху.";
    return "Показаны отпуска " + state.selected.length + " участников. Для добавления выберите одного (или нажмите «Я»).";
  }

  function loadState(hintAfter) {
    setHint("Загрузка…");
    fetch("/api/state?chat=" + encodeURIComponent(chatId), {
      headers: { "X-Telegram-Init-Data": initHeader }
    })
      .then(function (r) {
        if (r.status === 401) throw new Error("Откройте календарь кнопкой «Отпуска» в чате Telegram");
        if (r.status === 403) throw new Error("Вы не участник этой команды");
        if (!r.ok) throw new Error("Ошибка сервера " + r.status);
        return r.json();
      })
      .then(function (data) {
        state.today = data.today;
        state.members = data.members;
        state.me = data.me && typeof data.me.position === "number" ? data.me.position : null;
        if (!state.selectionInitialized) {
          // Default: the member who opened the app is selected.
          state.selectionInitialized = true;
          state.selected = state.me !== null
            ? [state.me]
            : state.members.map(function (m) { return m.position; });
        }
        // Drop positions of members removed from the roster.
        state.selected = state.selected.filter(function (pos) {
          return memberByPosition(pos) !== null;
        });
        if (state.selected.length === 0 && state.me !== null) state.selected = [state.me];
        if (!state.viewInitialized) {
          // Start on the current month; later reloads (after add/delete) keep
          // the month the user is browsing.
          state.viewInitialized = true;
          var t = parseIso(data.today);
          state.viewYear = t.getFullYear();
          state.viewMonth = t.getMonth();
        }
        renderMembers();
        renderCalendar();
        updateUi();
        if (hintAfter) setHint(hintAfter);
        else if (!state.selStart) setHint(defaultHint());
      })
      .catch(function (err) { setHint(err.message || "Не удалось загрузить данные"); });
  }

  function apiCall(method, url, body) {
    var opts = {
      method: method,
      headers: { "X-Telegram-Init-Data": initHeader }
    };
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    return fetch(url, opts).then(function (r) {
      return r.text().then(function (raw) {
        var j = null;
        try { j = raw ? JSON.parse(raw) : null; } catch (e) {}
        if (!r.ok) {
          throw new Error((j && j.message) || raw || ("Ошибка сервера " + r.status));
        }
        return j || {};
      });
    });
  }

  /* ---------- members dropdown (multi-select) ---------- */

  function isSelected(pos) {
    return state.selected.indexOf(pos) !== -1;
  }

  function renderMembers() {
    var label = $("members-label");
    var dot = $("members-dot");
    if (state.selected.length === 0) {
      label.textContent = "Никто не выбран";
      dot.style.background = "var(--muted)";
    } else if (state.selected.length === state.members.length) {
      label.textContent = "Все участники (" + state.members.length + ")";
      dot.style.background = "var(--muted)";
    } else if (state.selected.length === 1) {
      label.textContent = memberName(state.selected[0]);
      dot.style.background = colorOf(state.selected[0]);
    } else {
      label.textContent = memberName(state.selected[0]) + " +" + (state.selected.length - 1);
      dot.style.background = "var(--muted)";
    }
    $("me-btn").disabled = state.me === null;

    var list = $("members-list");
    list.innerHTML = "";
    state.members.forEach(function (m) {
      var selected = isSelected(m.position);
      var row = document.createElement("button");
      row.className = "member-row" + (selected ? " selected" : "");
      row.dataset.position = m.position;

      var check = document.createElement("span");
      check.className = "check";
      check.textContent = selected ? "✓" : "";

      var dot = document.createElement("span");
      dot.className = "dot";
      dot.style.background = colorOf(m.position);

      var name = document.createElement("span");
      name.className = "name";
      name.textContent = m.position === state.me ? m.name + " (я)" : m.name;

      row.appendChild(check);
      row.appendChild(dot);
      row.appendChild(name);
      row.addEventListener("click", function () { toggleMember(m.position); });
      list.appendChild(row);
    });
    renderVacationsList();
  }

  function toggleMember(pos) {
    var i = state.selected.indexOf(pos);
    if (i === -1) state.selected.push(pos);
    else state.selected.splice(i, 1);
    state.selStart = null;
    state.selEnd = null;
    renderMembers();
    renderCalendar();
    updateUi();
    setHint(defaultHint());
  }

  function selectOnly(pos) {
    if (pos === null) return;
    state.selected = [pos];
    state.selStart = null;
    state.selEnd = null;
    renderMembers();
    renderCalendar();
    updateUi();
    setHint(defaultHint());
  }

  function setMembersPanel(open) {
    $("members-panel").classList.toggle("hidden", !open);
    $("members-toggle").setAttribute("aria-expanded", open ? "true" : "false");
    $("members-toggle").querySelector(".caret").textContent = open ? "▴" : "▾";
  }

  /* ---------- vacations list for the selected members ---------- */

  function renderVacationsList() {
    var box = $("vacations-list");
    box.innerHTML = "";

    if (state.selected.length === 0) {
      var pick = document.createElement("div");
      pick.className = "vacation-empty";
      pick.textContent = "Выберите участников в списке сверху.";
      box.appendChild(pick);
      return;
    }

    var entries = [];
    state.members.forEach(function (m) {
      if (!isSelected(m.position)) return;
      m.vacations.forEach(function (v) { entries.push({ member: m, vacation: v }); });
    });
    entries.sort(function (a, b) {
      var as = a.vacation.start === null ? "0000-01-01" : a.vacation.start;
      var bs = b.vacation.start === null ? "0000-01-01" : b.vacation.start;
      if (as !== bs) return as < bs ? -1 : 1;
      if (a.vacation.end !== b.vacation.end) return a.vacation.end < b.vacation.end ? -1 : 1;
      return a.member.position - b.member.position;
    });

    if (!entries.length) {
      var empty = document.createElement("div");
      empty.className = "vacation-empty";
      empty.textContent = "У выбранных участников отпусков нет.";
      box.appendChild(empty);
      return;
    }

    entries.forEach(function (e) {
      var row = document.createElement("div");
      row.className = "vacation-row";

      var dot = document.createElement("span");
      dot.className = "dot";
      dot.style.background = colorOf(e.member.position);

      var name = document.createElement("span");
      name.className = "v-name";
      name.textContent = e.member.name;

      var period = document.createElement("span");
      period.className = "v-period";
      period.textContent = periodLabel(e.vacation);

      var del = document.createElement("button");
      del.className = "v-del";
      del.textContent = "✕";
      del.setAttribute("aria-label", "Удалить отпуск");
      del.addEventListener("click", function () {
        confirmDeleteVacation(e.member, e.vacation);
      });

      row.appendChild(dot);
      row.appendChild(name);
      row.appendChild(period);
      row.appendChild(del);
      box.appendChild(row);
    });
  }

  /* ---------- calendar ---------- */

  function renderCalendar() {
    hideDayTooltip();
    var y = state.viewYear, mo = state.viewMonth;
    $("month-title").textContent = MONTHS[mo].charAt(0).toUpperCase() + MONTHS[mo].slice(1) + " " + y;

    var wdBox = $("weekdays");
    wdBox.innerHTML = "";
    WD.forEach(function (w) {
      var el = document.createElement("div");
      el.className = "weekday";
      el.textContent = w;
      wdBox.appendChild(el);
    });

    var first = new Date(y, mo, 1);
    var offset = (first.getDay() + 6) % 7;  // Monday-first
    var daysInMonth = new Date(y, mo + 1, 0).getDate();
    var today = state.today;

    var box = $("days");
    box.innerHTML = "";
    for (var i = 0; i < offset; i++) {
      var blank = document.createElement("div");
      blank.className = "day blank";
      box.appendChild(blank);
    }
    for (var d = 1; d <= daysInMonth; d++) {
      var dateObj = new Date(y, mo, d);
      var iso = isoOf(dateObj);
      var cls = "day";
      if (dateObj.getMonth() !== mo) cls += " other";
      if (iso === today) cls += " today";
      if (iso < today) cls += " past";
      if (state.selStart && state.selEnd && iso >= state.selStart && iso <= state.selEnd) cls += " in-range";
      if (iso === state.selStart || iso === state.selEnd) cls += " sel-" + (iso === state.selStart ? "start" : "end");
      var cell = document.createElement("div");
      cell.className = cls;
      cell.dataset.iso = iso;
      cell.appendChild(document.createTextNode(d));
      cell.appendChild(buildMarks(iso));
      cell.addEventListener("click", onDayClick);
      box.appendChild(cell);
    }
  }

  function buildMarks(iso) {
    var marks = document.createElement("div");
    marks.className = "marks";
    state.members.forEach(function (m) {
      if (!isSelected(m.position)) return;
      m.vacations.forEach(function (v) {
        var from = v.start === null ? "0000-01-01" : v.start;  // legacy: ongoing
        var to = v.end;
        if (iso >= from && iso <= to) {
          var bar = document.createElement("div");
          bar.className = "mark";
          bar.style.background = colorOf(m.position);
          bar.dataset.vacationId = v.id;
          bar.dataset.position = m.position;
          marks.appendChild(bar);
        }
      });
    });
    return marks;
  }

  /* ---------- day tooltip: who is on vacation that day ---------- */

  function vacationsOn(iso) {
    var entries = [];
    state.members.forEach(function (m) {
      var covering = null;
      m.vacations.forEach(function (v) {
        var from = v.start === null ? "0000-01-01" : v.start;  // legacy: ongoing
        if (iso >= from && iso <= v.end) {
          if (!covering || v.end < covering.end) covering = v;
        }
      });
      if (covering) entries.push({ member: m, vacation: covering });
    });
    return entries;
  }

  function periodLabel(v) {
    return v.start === null ? "до " + ruDate(v.end) : ruDate(v.start) + " – " + ruDate(v.end);
  }

  function buildTooltip(iso) {
    var box = document.createElement("div");
    var title = document.createElement("div");
    title.className = "tt-title";
    title.textContent = ruDate(iso) + (iso === state.today ? " · сегодня" : "");
    box.appendChild(title);

    var entries = vacationsOn(iso);
    if (!entries.length) {
      var empty = document.createElement("div");
      empty.className = "tt-empty";
      empty.textContent = "Никто не в отпуске";
      box.appendChild(empty);
      return box;
    }
    entries.forEach(function (e) {
      var row = document.createElement("div");
      row.className = "tt-row";

      var dot = document.createElement("span");
      dot.className = "dot";
      dot.style.background = colorOf(e.member.position);

      var name = document.createElement("span");
      name.className = "tt-name";
      name.textContent = e.member.name + (isSelected(e.member.position) ? "" : " · скрыт");

      var period = document.createElement("span");
      period.className = "tt-period";
      period.textContent = periodLabel(e.vacation);

      row.appendChild(dot);
      row.appendChild(name);
      row.appendChild(period);
      box.appendChild(row);
    });
    return box;
  }

  function positionDayTooltip(x, y) {
    var el = $("day-tooltip");
    var rect = el.getBoundingClientRect();
    var left = Math.max(8, Math.min(x + 14, window.innerWidth - rect.width - 8));
    var top = y - rect.height - 12;
    if (top < 8) top = Math.min(window.innerHeight - rect.height - 8, y + 18);
    el.style.left = left + "px";
    el.style.top = top + "px";
  }

  var tooltipIso = null;

  function showDayTooltip(iso, x, y) {
    var el = $("day-tooltip");
    if (tooltipIso !== iso) {
      el.innerHTML = "";
      el.appendChild(buildTooltip(iso));
      tooltipIso = iso;
    }
    el.classList.remove("hidden");
    positionDayTooltip(x, y);
  }

  function hideDayTooltip() {
    tooltipIso = null;
    $("day-tooltip").classList.add("hidden");
  }

  function onDayClick(ev) {
    if (longPressFired) {  // long press already showed the tooltip
      longPressFired = false;
      return;
    }
    var iso = ev.currentTarget.dataset.iso;
    if (!iso) return;
    var today = state.today;

    var bar = ev.target.closest(".mark");
    if (bar && !state.selStart) {
      askDelete(bar.dataset.vacationId);
      return;
    }
    if (iso < today) { setHint("Прошедшие дни изменить нельзя"); return; }

    if (state.selected.length !== 1) {
      setHint("Чтобы добавить отпуск, выберите ровно одного участника (или нажмите «Я»)");
      setMembersPanel(true);
      return;
    }

    if (!state.selStart) {
      // first tap: range start
      state.selStart = iso;
      state.selEnd = null;
    } else if (state.selEnd !== null) {
      // range already chosen: this tap starts a new range
      state.selStart = iso;
      state.selEnd = null;
    } else if (iso < state.selStart) {
      // picked an earlier day: restart from it
      state.selStart = iso;
      state.selEnd = null;
    } else {
      // second tap: range end; confirming is done via «Добавить отпуск»
      state.selEnd = iso;
    }
    renderCalendar();
    updateUi();
  }

  function confirmAdd() {
    var target = state.selected.length === 1 ? state.selected[0] : null;
    if (target === null || !state.selStart) {
      setHint("Выберите одного участника и даты отпуска");
      return;
    }
    var payload = {
      chat: Number(chatId),
      position: target,
      start: state.selStart,
      end: state.selEnd === null ? state.selStart : state.selEnd
    };
    apiCall("POST", "/api/vacations", payload)
      .then(function () {
        if (tg && tg.HapticFeedback) tg.HapticFeedback.impactOccurred("medium");
        state.selStart = null;
        state.selEnd = null;
        loadState("Отпуск добавлен");
      })
      .catch(function (err) { setHint(err.message); });
  }

  function findVacation(vacationId) {
    for (var i = 0; i < state.members.length; i++) {
      var m = state.members[i];
      for (var j = 0; j < m.vacations.length; j++) {
        if (String(m.vacations[j].id) === String(vacationId)) {
          return { member: m, vacation: m.vacations[j] };
        }
      }
    }
    return null;
  }

  function askDelete(vacationId) {
    var found = findVacation(vacationId);
    if (!found) return;
    confirmDeleteVacation(found.member, found.vacation);
  }

  function confirmDeleteVacation(member, vacation) {
    var label = member.name + ": " + periodLabel(vacation);
    showPopup("Удалить отпуск", label, "Удалить", function () {
      apiCall("DELETE", "/api/vacations/" + vacation.id + "?chat=" + encodeURIComponent(chatId))
        .then(function () {
          if (tg && tg.HapticFeedback) tg.HapticFeedback.impactOccurred("medium");
          loadState("Отпуск удалён");
        })
        .catch(function (err) { setHint(err.message); });
    }, true);
  }

  /* ---------- popup (fallback when Telegram popup is unavailable) ---------- */

  var popupAction = null;

  function showPopup(title, text, okLabel, onOk, destructive) {
    if (tg && tg.showPopup) {
      var buttons = [
        { id: "ok", type: "destructive", text: okLabel },
        { id: "cancel", type: "cancel", text: "Отмена" }
      ];
      if (!destructive) buttons = [{ id: "ok", type: "default", text: okLabel }, { id: "cancel", type: "cancel", text: "Отмена" }];
      try {
        tg.showPopup({ title: title, message: text, buttons: buttons }, function (btnId) {
          if (btnId === "ok") onOk();
        });
        return;
      } catch (e) { /* fall through to the inline popup */ }
    }
    popupAction = onOk;
    $("popup-title").textContent = title;
    $("popup-text").textContent = text;
    $("popup-ok").textContent = okLabel;
    $("popup-ok").className = "primary" + (destructive ? " destructive" : "");
    $("popup-ok").onclick = function () { hidePopup(); if (popupAction) popupAction(); };
    $("popup-cancel").onclick = hidePopup;
    $("popup").classList.remove("hidden");
  }

  function hidePopup() { $("popup").classList.add("hidden"); }

  /* ---------- toolbar ---------- */

  function updateUi() {
    var single = state.selected.length === 1;
    $("add-btn").disabled = !(single && state.selStart);
    if (!state.selStart) return;
    if (state.selEnd !== null) {
      setHint(
        "Выбрано: " + ruDate(state.selStart) + " – " + ruDate(state.selEnd) +
        ". Нажмите «Добавить отпуск», чтобы сохранить."
      );
    } else {
      setHint(
        "Начало отпуска " + memberName(state.selected[0]) + ": " + ruDate(state.selStart) +
        ". Выберите последний день или сразу нажмите «Добавить отпуск»."
      );
    }
  }

  $("prev-month").addEventListener("click", function () {
    var m = state.viewMonth - 1;
    if (m < 0) { m = 11; state.viewYear--; }
    state.viewMonth = m;
    // Keep the range selection: a vacation may start in one month and end in
    // another, so switching months must not discard the chosen start date.
    renderCalendar();
    updateUi();
  });
  $("next-month").addEventListener("click", function () {
    var m = state.viewMonth + 1;
    if (m > 11) { m = 0; state.viewYear++; }
    state.viewMonth = m;
    // Keep the range selection (see prev-month).
    renderCalendar();
    updateUi();
  });

  $("add-btn").addEventListener("click", function () {
    if (state.selStart && state.selected.length === 1) {
      var name = memberName(state.selected[0]);
      showPopup(
        "Добавить отпуск",
        name + ": с " + ruDate(state.selStart) + " по " + ruDate(state.selEnd === null ? state.selStart : state.selEnd),
        "Добавить",
        function () { state.selEnd = state.selEnd === null ? state.selStart : state.selEnd; confirmAdd(); },
        false
      );
    }
  });

  $("members-toggle").addEventListener("click", function () {
    setMembersPanel($("members-panel").classList.contains("hidden"));
  });
  $("members-done").addEventListener("click", function () { setMembersPanel(false); });
  $("members-all").addEventListener("click", function () {
    state.selected = state.members.map(function (m) { return m.position; });
    state.selStart = null;
    state.selEnd = null;
    renderMembers();
    renderCalendar();
    updateUi();
    setHint(defaultHint());
  });
  $("me-btn").addEventListener("click", function () {
    selectOnly(state.me);
    setMembersPanel(false);
    setHint("Показаны только ваши отпуска. Нажмите на день, чтобы добавить отпуск.");
  });

  /* Hover tooltip: whose vacations cover the day. On touch devices the same
     info appears on a long press, without interfering with day taps. */
  var longPressTimer = null;
  var longPressFired = false;

  $("days").addEventListener("mouseover", function (ev) {
    var cell = ev.target.closest(".day");
    if (!cell || !cell.dataset.iso) return;
    showDayTooltip(cell.dataset.iso, ev.clientX, ev.clientY);
  });
  $("days").addEventListener("mouseout", function (ev) {
    if (!ev.target.closest(".day")) return;
    if (ev.relatedTarget && ev.relatedTarget.closest && ev.relatedTarget.closest(".day")) return;
    hideDayTooltip();
  });
  window.addEventListener("scroll", hideDayTooltip, true);
  window.addEventListener("resize", hideDayTooltip);

  $("days").addEventListener("touchstart", function (ev) {
    var cell = ev.target.closest(".day");
    if (!cell || !cell.dataset.iso) return;
    longPressFired = false;
    var touch = ev.touches[0];
    var iso = cell.dataset.iso;
    longPressTimer = setTimeout(function () {
      longPressFired = true;
      showDayTooltip(iso, touch.clientX, touch.clientY);
    }, 450);
  }, { passive: true });
  $("days").addEventListener("touchmove", function () {
    clearTimeout(longPressTimer);
    hideDayTooltip();
  }, { passive: true });
  $("days").addEventListener("touchend", function () {
    clearTimeout(longPressTimer);
    if (longPressFired) setTimeout(hideDayTooltip, 2500);
  });
  $("days").addEventListener("touchcancel", function () {
    clearTimeout(longPressTimer);
    hideDayTooltip();
  });

  if (!chatId) {
    setHint("Откройте календарь из чата команды (кнопка «Отпуска» в меню /daily)");
  } else if (!initHeader) {
    setHint("Откройте календарь из Telegram — без initData сервер не пустит. Прямая ссылка в браузере не подходит.");
  } else {
    loadState();
  }
})();
