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
    filter: null,         // null = "Все", otherwise member position
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
    return d.getFullYear() + "-" + (m < 10 ? "0" : m) + "-" + (day < 10 ? "0" : day);
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
  function setHintHtml(html) { $("hint").innerHTML = html; }

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
        var t = parseIso(data.today);
        state.viewYear = t.getFullYear();
        state.viewMonth = t.getMonth();
        renderMembers();
        renderCalendar();
        updateUi();
        if (hintAfter) setHint(hintAfter);
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

  /* ---------- members chips ---------- */

  function renderMembers() {
    var box = $("members");
    box.innerHTML = "";
    box.appendChild(makeChip(null, "Все", state.filter === null));
    state.members.forEach(function (m) {
      box.appendChild(makeChip(m, m.name, state.filter === m.position));
    });
  }

  function makeChip(member, label, selected) {
    var el = document.createElement("button");
    el.className = "chip" + (selected ? " selected" : "");
    var dot = document.createElement("span");
    dot.className = "dot";
    dot.style.background = member ? colorOf(member.position) : "var(--muted)";
    el.appendChild(dot);
    el.appendChild(document.createTextNode(label));
    el.addEventListener("click", function () {
      state.filter = member ? member.position : null;
      state.selStart = null;
      state.selEnd = null;
      renderMembers();
      renderCalendar();
      updateUi();
    });
    return el;
  }

  /* ---------- calendar ---------- */

  function renderCalendar() {
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
      if (state.filter !== null && m.position !== state.filter) return;
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

  function onDayClick(ev) {
    var iso = ev.currentTarget.dataset.iso;
    if (!iso) return;
    var today = state.today;

    var bar = ev.target.closest(".mark");
    if (bar && !state.selStart) {
      askDelete(bar.dataset.vacationId, iso);
      return;
    }
    if (iso < today) { setHint("Прошедшие дни изменить нельзя"); return; }

    if (state.filter === null) {
      setHint("Выберите участника сверху, чтобы добавить отпуск");
      return;
    }

    if (!state.selStart) {
      state.selStart = iso;
      state.selEnd = null;
      setHint("Начало отпуска: " + ruDate(iso) + ". Выберите последний день.");
      renderCalendar();
      updateUi();
      return;
    }

    if (iso < state.selStart) {  // restarted selection
      state.selStart = iso;
      state.selEnd = null;
      setHint("Начало отпуска: " + ruDate(iso) + ". Выберите последний день.");
      renderCalendar();
      updateUi();
      return;
    }

    state.selEnd = iso;
    var name = memberName(state.filter);
    showPopup(
      "Добавить отпуск",
      name + ": с " + ruDate(state.selStart) + " по " + ruDate(state.selEnd),
      "Добавить",
      confirmAdd,
      false
    );
  }

  function confirmAdd() {
    var payload = {
      chat: Number(chatId),
      position: state.filter,
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

  function askDelete(vacationId, iso) {
    var m = state.members.filter(function (mm) {
      return mm.vacations.some(function (v) { return String(v.id) === String(vacationId); });
    })[0];
    var v = m.vacations.filter(function (vv) { return String(vv.id) === String(vacationId); })[0];
    var label = (m ? m.name : "") + ": " + (v.start === null ? "до" : "с " + ruDate(v.start) + " по") + " " + ruDate(v.end);
    showPopup("Удалить отпуск", label, "Удалить", function () {
      apiCall("DELETE", "/api/vacations/" + vacationId + "?chat=" + encodeURIComponent(chatId))
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
    var memberChip = state.filter !== null;
    $("add-btn").disabled = !(memberChip && state.selStart);
    if (state.selStart) {
      setHintHtml("Выбрано: с " + ruDate(state.selStart) + (state.selEnd ? " по " + ruDate(state.selEnd) : ""));
    }
  }

  $("prev-month").addEventListener("click", function () {
    var m = state.viewMonth - 1;
    if (m < 0) { m = 11; state.viewYear--; }
    state.viewMonth = m;
    state.selStart = null;
    state.selEnd = null;
    renderCalendar();
  });
  $("next-month").addEventListener("click", function () {
    var m = state.viewMonth + 1;
    if (m > 11) { m = 0; state.viewYear++; }
    state.viewMonth = m;
    state.selStart = null;
    state.selEnd = null;
    renderCalendar();
  });

  $("add-btn").addEventListener("click", function () {
    if (state.selStart && state.filter !== null) {
      var name = memberName(state.filter);
      showPopup(
        "Добавить отпуск",
        name + ": с " + ruDate(state.selStart) + " по " + ruDate(state.selEnd === null ? state.selStart : state.selEnd),
        "Добавить",
        function () { state.selEnd = state.selEnd === null ? state.selStart : state.selEnd; confirmAdd(); },
        false
      );
    }
  });

  if (!chatId) {
    setHint("Откройте календарь из чата команды (кнопка «Отпуска» в меню /daily)");
  } else if (!initHeader) {
    setHint("Откройте календарь из Telegram — без initData сервер не пустит. Прямая ссылка в браузере не подходит.");
  } else {
    loadState();
  }
})();
