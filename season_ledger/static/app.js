"use strict";

(() => {
  const $ = (selector, root = document) => root.querySelector(selector);
  const CATEGORIES = [
    { key: "domestic_league", label: "Национальная лига", short: "Лига" },
    { key: "continental", label: "Лига чемпионов", short: "Лига чемпионов" },
    { key: "national_regular", label: "Сборная · обычные матчи", short: "Сборная" },
    { key: "national_major", label: "Сборная · финальные турниры", short: "ЧМ / Евро / Копа / КАН" },
    { key: "domestic_cup", label: "Кубки страны", short: "Кубки" },
    { key: "other", label: "Другие турниры", short: "Прочие" },
  ];
  const METRICS = [
    ["games", "И", "Матчи"], ["adjusted_rating", "Оц.", "Оценка с поправкой на позицию"],
    ["goals", "Г", "Голы"], ["assists", "П", "Голевые передачи"],
    ["place", "М", "Место команды или стадия турнира"],
  ];
  const DEFAULT_PLAYER_LIMIT = 50;
  const MIN_SEASON_GAMES = 30;
  const state = {
    seasons: [], current: null, data: null, search: "", position: "", sort: "points",
    showAllPlayers: false,
    trackedJobs: new Map(), hiddenJobs: new Set(), polling: false,
    timer: null, selectedPlayer: null, deletePlayer: null, loadSequence: 0,
  };
  function el(tag, attributes, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attributes || {})) {
      if (value === null || value === undefined || value === false) continue;
      if (key === "class") node.className = value;
      else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2).toLowerCase(), value);
      else if (key === "text") node.textContent = value;
      else if (key in node && ["disabled", "checked", "value", "hidden"].includes(key)) node[key] = value;
      else node.setAttribute(key, value === true ? "" : String(value));
    }
    for (const child of children.flat(Infinity)) {
      if (child === null || child === undefined || child === false) continue;
      node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
  }

  async function api(path, options = {}) {
    const response = await fetch(path, {
      ...options,
      headers: { Accept: "application/json", ...(options.body ? { "Content-Type": "application/json" } : {}), ...options.headers },
    });
    if (response.status === 204) return null;
    let data;
    try { data = await response.json(); }
    catch (_) { throw new Error("Сервер вернул неожиданный ответ. Проверьте, что приложение запущено."); }
    if (!response.ok) {
      let message = data.detail || data.error || "Не удалось выполнить запрос.";
      if (Array.isArray(message)) message = message.map(item => item.msg || "Некорректные данные").join(". ");
      if (typeof message !== "string") message = "Не удалось выполнить запрос.";
      throw new Error(message);
    }
    return data;
  }

  function errorMessage(error) {
    return error instanceof TypeError ? "Нет связи с приложением. Проверьте, что локальный сервер запущен." : (error.message || "Не удалось выполнить действие.");
  }

  function formError(id, message) {
    const box = document.getElementById(id);
    box.textContent = message || "";
    box.hidden = !message;
  }

  function toast(message, error = false) {
    const node = el("div", { class: `toast${error ? " toast-error" : ""}` }, message);
    $("#toast-container").append(node);
    window.setTimeout(() => node.remove(), error ? 9000 : 5500);
  }

  function number(value, digits = 0) {
    return typeof value === "number" && Number.isFinite(value)
      ? value.toLocaleString("ru-RU", { minimumFractionDigits: digits, maximumFractionDigits: digits }) : "—";
  }

  function money(value, compact = false) {
    if (typeof value !== "number" || !Number.isFinite(value)) return "—";
    if (compact && Math.abs(value) >= 1e9) return `${number(value / 1e9, value % 1e9 ? 2 : 0)} млрд €`;
    if (compact && Math.abs(value) >= 1e6) return `${number(value / 1e6, value % 1e6 ? 1 : 0)} млн €`;
    if (compact && Math.abs(value) >= 1e3) return `${number(value / 1e3, value % 1e3 ? 1 : 0)} тыс. €`;
    return `${number(value)} €`;
  }

  function date(value, includeTime = false) {
    if (!value) return "дата неизвестна";
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return String(value);
    return parsed.toLocaleString("ru-RU", {
      day: "2-digit", month: "2-digit", year: "numeric",
      ...(includeTime ? { hour: "2-digit", minute: "2-digit" } : {}),
      timeZone: "Europe/Moscow",
    });
  }

  function safeUrl(value) {
    if (typeof value !== "string") return null;
    try { const url = new URL(value); return ["http:", "https:"].includes(url.protocol) ? url.href : null; }
    catch (_) { return null; }
  }

  function externalLink(label, url, className = "source-link") {
    const target = safeUrl(url);
    return target ? el("a", { href: target, target: "_blank", rel: "noopener noreferrer", class: className }, label) : null;
  }

  function avatar(player, large = false) {
    const initials = String(player.name || "?").trim().split(/\s+/).slice(0, 2).map(part => part[0]).join("");
    const node = el("span", { class: `player-avatar${large ? " detail-avatar" : " table-avatar"}`, "aria-hidden": "true" }, initials);
    const url = safeUrl(player.photo_url);
    if (url) {
      const img = el("img", { src: url, alt: "", loading: "lazy", referrerpolicy: "no-referrer" });
      img.addEventListener("error", () => node.replaceChildren(initials), { once: true });
      node.replaceChildren(img);
    }
    return node;
  }

  function nationalityFlags(player) {
    const names = player.nationalities || [];
    const flags = player.nationality_flags || [];
    const codes = {
      England: "ENG", Scotland: "SCO", Wales: "WAL", "Northern Ireland": "NIR", Ireland: "IRL",
      Spain: "ESP", Brazil: "BRA", Portugal: "POR", Argentina: "ARG", France: "FRA", Germany: "GER",
      Italy: "ITA", Netherlands: "NED", Belgium: "BEL", Croatia: "CRO", Serbia: "SRB", Russia: "RUS",
      Ukraine: "UKR", Poland: "POL", Switzerland: "SUI", Austria: "AUT", Sweden: "SWE", Norway: "NOR",
      Denmark: "DEN", Finland: "FIN", Turkey: "TUR", Türkiye: "TUR", Morocco: "MAR", Senegal: "SEN",
      Nigeria: "NGA", Ghana: "GHA", Cameroon: "CMR", Egypt: "EGY", Algeria: "ALG", Japan: "JPN",
      "South Korea": "KOR", "Korea, South": "KOR", "United States": "USA", USA: "USA", Mexico: "MEX",
      Canada: "CAN", Australia: "AUS", Uruguay: "URU", Colombia: "COL", Chile: "CHI", Ecuador: "ECU",
      "Ivory Coast": "CIV", "Cote d'Ivoire": "CIV", "Czech Republic": "CZE", Czechia: "CZE",
    };
    return el("span", { class: "nationality-flags", "aria-label": names.length ? `Гражданство: ${names.join(", ")}` : "Гражданство не указано" }, ...names.map((name, index) => {
      const code = codes[name] || String(name).slice(0, 3).toLocaleUpperCase("ru");
      const fallback = el("span", { class: "flag-fallback" }, code);
      const holder = el("span", { class: "country-flag", title: name }, fallback);
      const url = safeUrl(flags[index]);
      if (url) {
        fallback.hidden = true;
        const image = el("img", { src: url, alt: name, loading: "lazy", referrerpolicy: "no-referrer" });
        image.addEventListener("error", () => { image.remove(); fallback.hidden = false; }, { once: true });
        holder.append(image);
      }
      return holder;
    }));
  }

  function rating(value, ratedGames, key = "rating", adjustment = null) {
    const isNumber = typeof value === "number" && Number.isFinite(value);
    const className = !isNumber ? " rating-missing" : value >= 7 ? " rating-high" : value < 6.5 ? " rating-low" : "";
    const sample = typeof ratedGames === "number" ? ` · ${ratedGames} матчей с оценкой` : "";
    const adjusted = key === "adjusted_rating";
    let title = isNumber ? `${adjusted ? "Оценка с поправкой на позицию" : "Средняя оценка FotMob"}${sample}` : adjusted ? "Нет оценки с поправкой" : "FotMob: нет оценки";
    if (adjusted) {
      if (typeof adjustment?.coefficient === "number" && adjustment.coefficient > 0) {
        title += ` · группа ${adjustment.group} · FotMob ÷ ${number(adjustment.coefficient, 4)}, максимум 10 за матч · без промежуточного округления`;
      } else {
        title = `Поправка недоступна: позиция «${adjustment?.position || "не указана"}» не сопоставлена с группой.`;
      }
    }
    return el("span", { class: `rating-badge${className}`, title }, number(value, 2));
  }

  function statCell(stats, key, extraClass = "", adjustment = null) {
    const value = stats?.[key];
    const node = el("td", { class: `${extraClass}${key === "place" ? " place-cell" : ""}${value === null || value === undefined ? " missing-value" : " metric-value"}`, title: key === "place" && value != null ? String(value) : null });
    node.append(key === "rating" || key === "adjusted_rating" ? rating(value, stats?.rated_games, key, adjustment) : key === "place" ? String(value ?? "—") : number(value));
    return node;
  }

  function currentSeason() { return state.seasons.find(season => season.key === state.current) || state.data?.season; }

  function renderSeasons() {
    const list = $("#season-list");
    list.replaceChildren(...state.seasons.map(season => el("button", {
      type: "button", class: `season-link${season.key === state.current ? " active" : ""}`,
      "aria-current": season.key === state.current ? "page" : null,
      onclick: () => selectSeason(season.key),
    }, el("span", {}, season.label))));
    const selectedImportSeason = $("#add-player-dialog").open ? $("#import-season").value : state.current;
    $("#import-season").replaceChildren(...state.seasons.map(season => el("option", { value: season.key }, season.label)));
    $("#import-season").value = selectedImportSeason || state.current || "";
    const season = currentSeason();
    $("#season-title").textContent = season?.label || "—";
    document.title = `${season?.label || "Сезон"} · REAL B’DOR`;
  }

  async function loadSeasons() {
    const data = await api("/api/seasons");
    state.seasons = data.seasons || [];
    if (!state.current || !state.seasons.some(season => season.key === state.current)) {
      const stored = localStorage.getItem("real-bdor-season");
      state.current = state.seasons.some(season => season.key === stored) ? stored : data.current || state.seasons[0]?.key || null;
    }
    renderSeasons();
  }

  async function selectSeason(key) {
    if (state.current === key && state.data) return;
    state.current = key;
    state.data = null;
    state.search = "";
    state.position = "";
    $("#player-search").value = "";
    localStorage.setItem("real-bdor-season", key);
    renderSeasons();
    renderCount();
    renderTable();
    renderJobs();
    await loadSeason();
  }

  async function loadSeason() {
    if (!state.current) return;
    const requested = state.current;
    const sequence = ++state.loadSequence;
    $(".collection-panel").setAttribute("aria-busy", "true");
    try {
      const data = await api(`/api/seasons/${encodeURIComponent(requested)}`);
      if (sequence !== state.loadSequence || requested !== state.current) return;
      state.data = data;
      formError("page-error", "");
      renderSeasons();
      renderCount();
      renderPositionFilter();
      renderTable();
      for (const job of data.jobs || []) {
        if (["queued", "running"].includes(job.status)) state.trackedJobs.set(String(job.id), job);
      }
      renderJobs();
      schedulePoll();
      return data;
    } catch (error) { if (sequence === state.loadSequence) formError("page-error", errorMessage(error)); }
    finally { if (sequence === state.loadSequence) $(".collection-panel").setAttribute("aria-busy", "false"); }
  }

  function renderCount() {
    const players = state.data?.players || [];
    $("#player-count").textContent = `${number(players.length)} ${playerWord(players.length)}`;
    const season = state.seasons.find(item => item.key === state.current);
    if (season && season.player_count !== players.length) { season.player_count = players.length; renderSeasons(); }
  }

  function playerWord(count) {
    const lastTwo = count % 100;
    if (lastTwo >= 11 && lastTwo <= 14) return "игроков";
    const last = count % 10;
    return last === 1 ? "игрок" : last >= 2 && last <= 4 ? "игрока" : "игроков";
  }

  function renderPositionFilter() {
    const positions = [...new Set((state.data?.players || []).map(player => player.position).filter(Boolean))].sort((a, b) => String(a).localeCompare(String(b), "ru"));
    const select = $("#position-filter");
    select.replaceChildren(el("option", { value: "" }, "Все позиции"), ...positions.map(position => el("option", { value: position }, position)));
    if (positions.includes(state.position)) select.value = state.position;
    else { state.position = ""; select.value = ""; }
  }

  function visibleCategories() {
    const players = state.data?.players || [];
    return CATEGORIES.filter(category => ["domestic_league", "continental", "national_regular", "national_major"].includes(category.key)
      || players.some(player => (player.competitions || []).some(item => item.category === category.key)));
  }

  function categoryMetrics(category) {
    return ["national_regular", "other"].includes(category.key) ? METRICS.slice(0, 4) : METRICS;
  }

  function clubLogos(player) {
    return el("span", { class: "club-logos" }, ...(player.clubs || []).map(club => {
      const url = safeUrl(club.logo_url);
      return url ? el("img", { class: "club-logo", src: url, alt: club.name || "Клуб", title: club.name || "Клуб", loading: "lazy", referrerpolicy: "no-referrer", onerror: event => event.target.remove() }) : null;
    }));
  }

  function pointNumber(value) {
    return number(value, typeof value === "number" && !Number.isInteger(value) ? 2 : 0);
  }

  function bonusNumber(value) { return `${typeof value === "number" && value > 0 ? "+" : ""}${pointNumber(value)}`; }

  function bonusOutcome(value) {
    return ({ winner: "Победа", champion: "Чемпион", finalist: "Финал", runner_up: "Финалист", third: "3-е место", semifinalist: "Полуфинал", semi_final: "Полуфинал", semifinal: "Полуфинал", quarterfinalist: "Четвертьфинал", quarter_final: "Четвертьфинал", quarterfinal: "Четвертьфинал", no_appearances: "Нет сыгранных матчей", outside_top_five: "Вне топ-5 лиг", outside_medium_leagues: "Вне топ-5 и средних лиг" })[value] || value || "";
  }

  function pointsAvailability(player) {
    const weighted = player.weighted_rating;
    if (weighted?.status === "missing_history") return "Обновите данные игрока, чтобы загрузить историю матчей.";
    if (weighted?.status === "missing_position") return "Взвешенная оценка недоступна: позиция игрока не сопоставлена с группой поправки.";
    if (weighted?.status === "incomplete_context") return "Взвешенная оценка пока недоступна: не хватает данных о контексте отдельных матчей.";
    if (player.season_points?.status === "awaiting_results") return "Итоговые баллы пока не рассчитаны: результаты некоторых турниров ещё не подтверждены.";
    return player.season_points?.total == null ? "Для итоговых баллов пока недостаточно данных." : "";
  }

  function seasonPointsTitle(player) {
    const points = player.season_points;
    if (!points) return pointsAvailability(player);
    const lines = [`Баллы за сезон: ${pointNumber(points.total)}`, `База: ${pointNumber(points.base_points)} · Бонусы / штрафы: ${bonusNumber(points.bonus_points)}`];
    const availability = pointsAvailability(player);
    if (availability) lines.push(availability);
    lines.push("База = взвешенная оценка × 100. Итог = база + бонусы − штрафы.");
    for (const bonus of points.bonuses || []) lines.push(`${[bonus.competition, bonus.team, bonusOutcome(bonus.outcome)].filter(Boolean).join(" · ")}: ${bonusNumber(bonus.points)}`);
    for (const item of points.pending || []) lines.push(`${item.competition || "Турнир"}: ${item.reason || "бонус пока не определён"}`);
    return lines.join("\n");
  }

  function seasonPointsCell(player) {
    const points = player.season_points;
    return el("td", { class: "group-start season-points-col", title: seasonPointsTitle(player) },
      el("span", { class: `season-points-total${points?.total == null ? " missing-value" : ""}` }, pointNumber(points?.total)),
      typeof points?.bonus_points === "number" && points.bonus_points !== 0 ? el("span", { class: "season-points-bonus" }, `${bonusNumber(points.bonus_points)} к базе`) : null);
  }

  function tablePlayers() {
    const players = state.data?.players || [];
    const query = state.search.trim().toLocaleLowerCase("ru");
    const filtered = players.filter(player => (state.showAllPlayers || (player.total?.games ?? 0) >= MIN_SEASON_GAMES)
      && (!state.position || player.position === state.position)
      && (!query || [player.name, ...(player.clubs || []).map(club => club.name)].filter(Boolean).join(" ").toLocaleLowerCase("ru").includes(query)));
    filtered.sort((a, b) => {
      if (state.sort === "name") return String(a.name || "").localeCompare(String(b.name || ""), "ru");
      const sortKey = state.sort === "rating" ? "adjusted_rating" : state.sort;
      const av = state.sort === "value" ? a.market_value?.amount : state.sort === "points" ? a.season_points?.total : a.total?.[sortKey];
      const bv = state.sort === "value" ? b.market_value?.amount : state.sort === "points" ? b.season_points?.total : b.total?.[sortKey];
      const an = typeof av === "number" ? av : -Infinity;
      const bn = typeof bv === "number" ? bv : -Infinity;
      return (bn - an) || String(a.name || "").localeCompare(String(b.name || ""), "ru");
    });
    return filtered;
  }

  function renderTable() {
    const players = state.data?.players || [];
    const hasPlayers = players.length > 0;
    $("#empty-state").hidden = hasPlayers;
    $("#table-toolbar").hidden = !hasPlayers;
    const matching = tablePlayers();
    const filtered = state.showAllPlayers ? matching : matching.slice(0, DEFAULT_PLAYER_LIMIT);
    $("#no-results").hidden = !hasPlayers || filtered.length !== 0;
    $("#no-results").textContent = state.showAllPlayers
      ? "Никого не нашли. Попробуйте другое имя или позицию."
      : "Никого не нашли. Измените фильтры или включите «Все игроки», чтобы увидеть игроков с менее чем 30 матчами.";
    $("#table-container").hidden = !hasPlayers || filtered.length === 0;
    const categories = visibleCategories();
    const row1 = el("tr", {}, el("th", { rowspan: "2", class: "number-col", scope: "col", "aria-label": "Номер строки", title: "Номер строки в текущем порядке" }, "№"), el("th", { rowspan: "2", class: "player-col", scope: "col" }, "Игрок"));
    row1.append(el("th", { rowspan: "2", class: "group-start season-points-col", scope: "col", title: "Баллы за сезон: базовая часть по взвешенной оценке, бонусы за результаты и штрафы. «—» — итог пока не рассчитан." }, "Баллы"));
    for (const category of categories) row1.append(el("th", { colspan: String(categoryMetrics(category).length), class: "group-start", scope: "colgroup", title: category.label }, category.short));
    row1.append(el("th", { colspan: "4", class: "group-start total-col", scope: "colgroup" }, "Всего за сезон"), el("th", { rowspan: "2", class: "group-start market-col", scope: "col", title: "Последняя известная стоимость Transfermarkt на конец сезона" }, "Стоимость"));
    const row2 = el("tr");
    for (const category of categories) for (const [i, [, label, title]] of categoryMetrics(category).entries()) row2.append(el("th", { scope: "col", class: i === 0 ? "group-start" : "", title }, label));
    for (const [i, [, label, title]] of METRICS.slice(0, 4).entries()) row2.append(el("th", { scope: "col", class: `total-col${i === 0 ? " group-start" : ""}`, title }, label));
    $("#table-head").replaceChildren(row1, row2);
    const rows = filtered.map((player, index) => {
      const row = el("tr", { tabindex: "0", "aria-label": `Статистика: ${player.name}`, onclick: () => openPlayer(player.id), onkeydown: event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); openPlayer(player.id); } } });
      const clubNames = (player.clubs || []).map(item => item.name).filter(Boolean).join(", ");
      const ageText = typeof player.age === "number" ? `${number(player.age)} г.` : "—";
      const ageTitle = `Возраст на ${date(player.age_as_of)}: ${typeof player.age === "number" ? ageText : "неизвестен"}`;
      const rowNumber = index + 1;
      row.append(el("td", { class: "number-col" }, el("span", { class: rowNumber <= 3 ? `rank-badge rank-${rowNumber}` : "rank-number" }, rowNumber)), el("td", { class: "player-col" }, el("div", { class: "player-cell" }, avatar(player), el("div", { class: "player-info" },
        el("div", { class: "player-first-line" }, el("span", { class: "player-name", title: player.name }, player.name), player.error ? el("span", { class: "player-alert", title: player.error, "aria-label": "Ошибка последнего обновления" }, "!") : null, el("span", { class: "age-label", title: ageTitle, "aria-label": ageTitle }, ageText), el("span", { class: "position-tag" }, player.position || "—")),
        el("div", { class: "player-secondary" }, nationalityFlags(player), clubLogos(player), el("span", { class: "club-name", title: clubNames }, clubNames))))));
      row.append(seasonPointsCell(player));
      for (const category of categories) for (const [i, [key]] of categoryMetrics(category).entries()) row.append(statCell(player.groups?.[category.key], key, i === 0 ? "group-start" : "", player.rating_adjustment));
      for (const [i, [key]] of METRICS.slice(0, 4).entries()) row.append(statCell(player.total, key, `total-col${i === 0 ? " group-start" : ""}`, player.rating_adjustment));
      row.append(el("td", { class: "group-start market-col", title: player.market_value ? `Стоимость от ${date(player.market_value.observed_on)}; срез на ${date(player.market_value.as_of)}` : "Историческая стоимость недоступна" }, el("span", { class: player.market_value ? "market-amount" : "missing-value" }, money(player.market_value?.amount, true))));
      return row;
    });
    $("#table-body").replaceChildren(...rows);
    $("#player-count").textContent = filtered.length === players.length ? `${number(players.length)} ${playerWord(players.length)}` : `${filtered.length} из ${players.length}`;
    const table = $("#season-table");
    table.querySelector("colgroup")?.remove();
    const columns = el("colgroup", {}, el("col", { style: "width:32px" }), el("col", { style: "width:220px" }), el("col", { style: "width:75px" }));
    const widths = [28, 40, 26, 26, 60];
    let categoryWidth = 0;
    for (const category of categories) {
      const groupWidths = widths.slice(0, categoryMetrics(category).length);
      categoryWidth += groupWidths.reduce((sum, width) => sum + width, 0);
      for (const width of groupWidths) columns.append(el("col", { style: `width:${width}px` }));
    }
    for (const width of widths.slice(0, 4)) columns.append(el("col", { style: `width:${width}px` }));
    columns.append(el("col", { style: "width:90px" }));
    table.insertBefore(columns, $("#table-head"));
    table.style.minWidth = `${32 + 220 + 75 + categoryWidth + 120 + 90}px`;
  }

  function renderJobs() {
    const jobs = new Map();
    for (const job of state.data?.jobs || []) jobs.set(String(job.id), job);
    for (const [id, job] of state.trackedJobs) if (!(job.season || job.season_key) || (job.season || job.season_key) === state.current) jobs.set(id, job);
    const visible = [...jobs.values()].filter(job => !state.hiddenJobs.has(String(job.id)) && ["queued", "running", "failed"].includes(job.status));
    $("#jobs-section").hidden = visible.length === 0;
    $("#jobs-list").replaceChildren(...visible.map(job => el("div", { class: `job-card job-${job.status}` },
      el("span", { class: "job-icon", "aria-hidden": "true" }, job.status === "failed" ? "!" : job.status === "queued" ? "◷" : ""),
      el("div", { class: "job-body" }, el("div", { class: "job-title" }, job.status === "failed" ? `Не удалось загрузить${job.player_name ? `: ${job.player_name}` : " игрока"}` : job.status === "queued" ? "Ожидает загрузки" : `Загружаем${job.player_name ? `: ${job.player_name}` : " игрока"}`),
        el("div", { class: "job-message" }, job.error || job.message || (job.status === "queued" ? "Импорт начнётся после предыдущего игрока." : "Проверяем Transfermarkt и FotMob. Загрузка может занять некоторое время."))),
      job.status === "failed" ? el("button", { type: "button", class: "job-close", title: "Скрыть сообщение", "aria-label": "Скрыть ошибку", onclick: () => { state.hiddenJobs.add(String(job.id)); renderJobs(); } }, "×") : null)));
  }

  function schedulePoll() {
    if (state.timer || state.polling || ![...state.trackedJobs.values()].some(job => ["queued", "running"].includes(job.status))) return;
    state.timer = window.setTimeout(pollJobs, 1800);
  }

  function completedJobMessage(job, loadedData) {
    const jobSeason = job.season || job.season_key;
    const currentData = loadedData && loadedData === state.data
      && loadedData.season?.key === state.current && jobSeason === state.current;
    const player = currentData && job.player_id != null
      ? (loadedData.players || []).find(row => String(row.id) === String(job.player_id)) : null;
    const name = job.player_name || player?.name;
    const message = name ? `${name}: данные сохранены.` : "Игрок добавлен в сезон.";
    if (!player) return message;
    const index = tablePlayers().findIndex(row => row.id === player.id);
    return `${message} ${index < 0 ? "Игрок скрыт фильтрами таблицы." : `Место в таблице сезона: ${index + 1}.`}`;
  }

  async function pollJobs() {
    state.timer = null;
    state.polling = true;
    let changed = false;
    const completed = [];
    const active = [...state.trackedJobs.entries()].filter(([, job]) => ["queued", "running"].includes(job.status));
    await Promise.all(active.map(async ([id, previous]) => {
      try {
        const response = await api(`/api/jobs/${encodeURIComponent(id)}`);
        const job = { ...previous, ...(response.job || response) };
        state.trackedJobs.set(id, job);
        if (["completed", "failed"].includes(job.status)) {
          changed = true;
          if (job.status === "completed") completed.push(job);
          else toast(job.error || "Загрузка игрока завершилась с ошибкой.", true);
        }
      } catch (error) {
        // A dropped connection does not cancel the server-side import. Keep polling.
        console.warn("Не удалось проверить импорт:", errorMessage(error));
      }
    }));
    state.polling = false;
    renderJobs();
    if (changed) {
      let loadedData;
      try { await loadSeasons(); loadedData = await loadSeason(); }
      catch (error) { formError("page-error", errorMessage(error)); }
      for (const job of completed) toast(completedJobMessage(job, loadedData));
      if (state.selectedPlayer && $("#player-dialog").open) {
        const player = (state.data?.players || []).find(row => row.id === state.selectedPlayer);
        if (player) renderPlayer(player);
      }
    }
    schedulePoll();
  }

  function trackJob(response, season) {
    const job = response.job || response;
    if (job?.id !== undefined) state.trackedJobs.set(String(job.id), { ...job, season: job.season || season });
    renderJobs();
    schedulePoll();
  }

  function openAddPlayer() {
    if (!state.current) { openSeason(); return; }
    formError("add-player-error", "");
    $("#import-season").value = state.current;
    $("#add-player-dialog").showModal();
    $("#player-url").focus();
  }

  function openSeason() {
    formError("season-error", "");
    $("#season-year").value = String(currentSeason()?.start_year || new Date().getFullYear());
    $("#season-format").value = currentSeason()?.calendar ? "calendar" : "split";
    previewSeasonDates();
    $("#season-dialog").showModal();
  }

  function previewSeasonDates() {
    const year = Number($("#season-year").value);
    const calendar = $("#season-format").value === "calendar";
    $("#season-date-preview").textContent = Number.isInteger(year) && year >= 1900 && year <= 2100 ? calendar ? `Период: 1 января — 31 декабря ${year} года.` : `Клубы — сезон ${year}/${String(year + 1).slice(-2)} по данным источника. Обычные матчи сборных: 1 июля ${year} — 30 июня ${year + 1}. Летние финальные турниры сборных учитываются целиком, до 31 августа ${year + 1}.` : "";
  }

  function openPlayer(id) {
    const player = (state.data?.players || []).find(item => item.id === id);
    if (!player) return;
    state.selectedPlayer = id;
    renderPlayer(player);
    if (!$("#player-dialog").open) $("#player-dialog").showModal();
  }

  function detailStat(label, value, caption) {
    return el("div", { class: "detail-stat" }, el("span", { class: "detail-stat-label" }, label), el("span", { class: "detail-stat-value" }, value), el("span", { class: "detail-stat-caption" }, caption));
  }

  function adjustmentFormula(player) {
    const adjustment = player.rating_adjustment;
    const total = player.total || {};
    if (typeof adjustment?.coefficient !== "number" || adjustment.coefficient <= 0) {
      return `Поправка недоступна: позиция «${adjustment?.position || player.position || "не указана"}» не сопоставлена с группой. FotMob: ${number(total.rating, 2)}.`;
    }
    const sample = typeof total.rated_games === "number" ? ` ${number(total.rated_games)} матчей с оценкой.` : "";
    return `Поправка ${adjustment.group}: FotMob ÷ ${number(adjustment.coefficient, 4)}, максимум 10 за матч. Расчёт без промежуточного округления.${sample}`;
  }

  function entityName(value) {
    return typeof value === "string" ? value : value?.name || value?.label || "";
  }

  function matchWeightTitle(match) {
    const rules = ["Базовый вес: 1"];
    for (const rule of match.weight_rules || []) {
      if (rule.key === "base") continue;
      rules.push(`${rule.label || rule.key || "Надбавка"}: ${bonusNumber(rule.addition)}`);
    }
    rules.push(`Итоговый вес: ${pointNumber(match.weight)}`);
    if (match.included === false) rules.push("Матч не включён в расчёт.");
    return rules.join("\n");
  }

  function matchWeightsDetail(player) {
    const weighted = player.weighted_rating;
    const matches = weighted?.matches || [];
    const warnings = weighted?.warnings || [];
    if (!matches.length && !warnings.length) return null;
    const body = el("tbody");
    for (const match of matches) {
      const competition = entityName(match.competition) || entityName(match.context?.competition) || "—";
      const opponent = entityName(match.opponent) || "Соперник не указан";
      const rules = matchWeightTitle(match);
      const row = el("tr", { class: match.included === false ? "match-excluded" : null },
        el("td", {}, date(match.date)),
        el("td", {}, externalLink(opponent, match.url, "match-source-link") || opponent,
          match.team ? el("span", { class: "match-team" }, entityName(match.team)) : null),
        el("td", {}, competition));
      row.append(statCell({ rating: match.rating }, "rating"), statCell(match, "adjusted_rating", "", player.rating_adjustment),
        el("td", { class: "match-weight", title: rules, "aria-label": rules },
          pointNumber(match.weight), match.included === false ? el("span", { class: "match-excluded-label" }, "не учтён") : null));
      body.append(row);
    }
    return el("details", { class: "match-weights" },
      el("summary", {}, `Веса матчей (${number(matches.length)})`),
      warnings.length ? el("ul", { class: "weighted-warnings" }, ...warnings.map(warning => el("li", {}, warning))) : null,
      matches.length ? el("div", { class: "weights-table-wrap" }, el("table", { class: "weights-table" },
        el("thead", {}, el("tr", {},
          ...["Дата", "Соперник / команда", "Турнир", "FotMob", "С поправкой", "Вес"].map(label => el("th", {
            scope: "col", title: label === "С поправкой" ? "Оценка после поправки на позицию, не более 10" : label === "Вес" ? "1 + сумма надбавок; наведите на значение для расшифровки" : null,
          }, label)))), body)) : null);
  }

  function seasonPointsDetail(player) {
    const points = player.season_points;
    const weighted = player.weighted_rating;
    const bonuses = points?.bonuses || [];
    const pending = points?.pending || [];
    const availability = pointsAvailability(player);
    const matchSummary = [
      typeof weighted?.rated_games === "number" ? `Матчей с оценкой: ${number(weighted.rated_games)}` : null,
      typeof weighted?.unrated_games === "number" ? `без оценки: ${number(weighted.unrated_games)}` : null,
      typeof weighted?.total_weight === "number" ? `сумма весов: ${pointNumber(weighted.total_weight)}` : null,
    ].filter(Boolean).join(" · ");
    const rows = bonuses.map(bonus => el("tr", {},
      el("td", {}, bonus.competition || "Турнир", bonus.team ? el("span", { class: "bonus-team" }, bonus.team) : null),
      el("td", {}, bonusOutcome(bonus.outcome) || "—"), el("td", { class: "bonus-amount" }, bonusNumber(bonus.points))));
    return el("section", { class: "season-points-section", "aria-labelledby": "season-points-title" },
      el("h3", { class: "detail-section-heading", id: "season-points-title" }, "Баллы за сезон"),
      el("div", { class: "points-summary" },
        el("span", {}, "Взвешенная оценка: ", el("strong", {}, number(points?.weighted_rating, 2))),
        el("span", {}, "База: ", el("strong", {}, pointNumber(points?.base_points))),
        el("span", {}, "Бонусы / штрафы: ", el("strong", {}, bonusNumber(points?.bonus_points))),
        el("span", {}, "Всего: ", el("strong", {}, pointNumber(points?.total)))),
      el("p", { class: "points-formula" }, "Взвешенная оценка = Σ(оценка с поправкой × вес) ÷ Σ(весов) по учтённым матчам. Вес = 1 + сумма надбавок. Каждая оценка с поправкой ограничена 10. База = взвешенная оценка × 100; итог = база + бонусы − штрафы."),
      availability ? el("p", { class: "points-explanation" }, availability) : null,
      matchSummary ? el("p", { class: "weights-metadata" }, matchSummary) : null,
      matchWeightsDetail(player),
      rows.length ? el("div", { class: "bonus-table-wrap" }, el("table", { class: "bonus-table" },
        el("thead", {}, el("tr", {}, ...["Турнир / команда", "Результат", "Бонус / штраф"].map(label => el("th", { scope: "col" }, label)))), el("tbody", {}, ...rows))) : null,
      pending.length ? el("div", { class: "points-pending" }, el("span", {}, "Бонусы пока не определены:"),
        el("ul", {}, ...pending.map(item => el("li", {}, `${item.competition || "Турнир"}: ${item.reason || "нет данных о результате"}`)))) : null);
  }

  function renderPlayer(player) {
    const total = player.total || {};
    const table = el("table", { class: "detail-table" });
    const head = el("thead", {}, el("tr", {}, ...["Турнир / команда", "Матчи", "FotMob", "С поправкой", "Голы", "Передачи", "Минуты", "Место / стадия"].map(label => el("th", { scope: "col", title: label === "С поправкой" ? "Оценка FotMob после деления на коэффициент позиции, не более 10 за матч" : null }, label))));
    const body = el("tbody");
    for (const competition of player.competitions || []) {
      const category = competition.is_supercup ? "Суперкубок"
        : competition.category === "continental"
          ? (competition.is_champions_league ? "Лига чемпионов" : "Другие международные клубные турниры")
          : CATEGORIES.find(item => item.key === competition.category)?.label || "Другие турниры";
      const sources = (competition.sources || []).map(source => ({ transfermarkt: "Transfermarkt", fotmob: "FotMob" }[source] || source)).join(" + ");
      const row = el("tr", {}, el("td", {}, el("span", { class: "competition-name" }, competition.name || "Турнир", el("span", { class: "competition-meta" }, [competition.team, category].filter(Boolean).join(" · ")), el("span", { class: "competition-meta" }, sources || "Источник не указан"))));
      for (const key of ["games", "rating", "adjusted_rating", "goals", "assists", "minutes", "place"]) row.append(statCell(competition, key, "", player.rating_adjustment));
      body.append(row);
    }
    if (!(player.competitions || []).length) body.append(el("tr", {}, el("td", { colspan: "8" }, "Статистика турниров недоступна у источников.")));
    const totalRow = el("tr", { class: "detail-total" }, el("td", {}, "Всего за сезон"));
    for (const key of ["games", "rating", "adjusted_rating", "goals", "assists", "minutes"]) totalRow.append(statCell(total, key, "", player.rating_adjustment));
    totalRow.append(el("td", {}, "—"));
    body.append(totalRow);
    table.append(head, body);
    const value = player.market_value;
    const detail = el("div", {},
      el("header", { class: "detail-header" },
        el("div", { class: "detail-identity" }, avatar(player, true), el("div", {}, el("span", { class: "eyebrow" }, `Сезон ${currentSeason()?.label || ""}`), el("h2", { id: "player-dialog-title", class: "detail-name" }, player.name), el("div", { class: "detail-subtitle" }, [player.position, (player.nationalities || []).join(", "), (player.clubs || []).map(item => item.name).join(", ")].filter(Boolean).join(" · ")))),
        el("button", { type: "button", class: "close-button", "aria-label": "Закрыть", onclick: () => $("#player-dialog").close() }, "×")),
      el("div", { class: "detail-body" },
        el("div", { class: "detail-overview" }, detailStat("Матчей", number(total.games), "во всех турнирах"), detailStat("Голов", number(total.goals), "за выбранный сезон"), detailStat("Голевых передач", number(total.assists), "за выбранный сезон"), detailStat("Оценка с поправкой", number(total.adjusted_rating, 2), `FotMob ${number(total.rating, 2)} · ${player.rating_adjustment?.group || "позиция не сопоставлена"}`)),
        el("p", { class: "adjustment-formula" }, adjustmentFormula(player)),
        seasonPointsDetail(player),
        el("h3", { class: "detail-section-heading" }, "Выступления по турнирам"),
        el("div", { class: "detail-table-wrap" }, table),
        el("p", { class: "detail-footnote" }, "«—» означает отсутствие данных. Оценка FotMob — среднее доступных опубликованных оценок за матчи; из-за округления оно может отличаться от сезонной карточки FotMob. Показатели сопоставляются по турниру и команде. Место в лиге или достигнутая стадия кубка показаны по данным источника."),
        el("div", { class: "detail-value-row" }, el("div", {}, el("span", { class: "detail-market-label" }, "Стоимость на конец сезона"), el("span", { class: "detail-market-note" }, value ? `Transfermarkt · оценка от ${date(value.observed_on)} · срез на ${date(value.as_of)}. Для незавершённого сезона — на дату загрузки.` : "Transfermarkt · историческая стоимость для этого сезона недоступна")), el("span", { class: "detail-market-value" }, money(value?.amount, true))),
        el("div", { class: "source-links" }, externalLink("Профиль Transfermarkt", player.transfermarkt_url), externalLink("Профиль FotMob", player.fotmob_url), el("span", { class: `source-status${player.fotmob_status === "matched" ? " source-status-ok" : ""}` }, ({ matched: "FotMob: профиль связан", ambiguous: "FotMob: нужно выбрать профиль", not_found: "FotMob: профиль не найден", unavailable: "FotMob: источник недоступен" }[player.fotmob_status] || "FotMob: данные отсутствуют"))),
        player.error ? el("div", { class: "detail-warning detail-error", role: "alert" }, `Последнее обновление не удалось. Показаны ранее сохранённые данные. ${player.error}`) : null,
        (player.warnings || []).length ? el("div", { class: "detail-warning" }, el("ul", {}, ...(player.warnings || []).map(warning => el("li", {}, warning)))) : null,
        player.fotmob_status !== "matched" ? fotmobMatchPanel(player) : null,
        el("div", { class: "detail-actions" }, el("span", { class: "detail-updated" }, `Последняя загрузка: ${date(player.updated_at, true)} МСК`), el("div", { class: "detail-action-buttons" }, el("button", { class: "button button-quiet delete-link", type: "button", onclick: () => confirmDelete(player) }, "Удалить из сезона"), el("button", { class: "button button-secondary", type: "button", onclick: event => refreshPlayer(player, undefined, event.currentTarget) }, "Обновить данные")))),
    );
    $("#player-detail").replaceChildren(detail);
  }

  function fotmobMatchPanel(player) {
    const candidates = player.fotmob_candidates || [];
    const manualInput = el("input", { class: "text-input", type: "text", placeholder: "ID или ссылка на профиль FotMob", "aria-label": "ID или ссылка на профиль FotMob", required: true, autocomplete: "off" });
    const form = el("form", { class: "fotmob-manual", onsubmit: event => {
      event.preventDefault();
      const value = manualInput.value.trim();
      const id = /^\d+$/.test(value) ? value : value.match(/^https:\/\/(?:www\.)?fotmob\.com\/(?:[a-z]{2}\/)?players\/(\d+)(?:\/|$|\?)/i)?.[1];
      if (!id) { manualInput.setCustomValidity("Укажите числовой ID или ссылку вида https://www.fotmob.com/players/194165/harry-kane"); manualInput.reportValidity(); return; }
      manualInput.setCustomValidity("");
      refreshPlayer(player, id, $("button", form));
    } }, manualInput, el("button", { type: "submit", class: "button button-secondary" }, "Связать профиль"));
    manualInput.addEventListener("input", () => manualInput.setCustomValidity(""));
    return el("section", { class: "fotmob-match" }, el("h3", {}, candidates.length ? "Уточните профиль FotMob" : "Связать с FotMob"), el("p", {}, candidates.length ? "Есть несколько подходящих игроков. Выберите нужный профиль, чтобы загрузить оценки." : "Автоматически связать профиль не удалось. Можно указать ID или ссылку на профиль FotMob; данные Transfermarkt уже сохранены."), candidates.length ? el("div", { class: "candidate-list" }, ...candidates.map(candidate => el("button", { class: "candidate-button", type: "button", onclick: event => refreshPlayer(player, String(candidate.id), event.currentTarget) }, `${candidate.name || "Игрок"} · ${candidate.team || "клуб не указан"}`, el("br"), `ID ${candidate.id}${candidate.birth_date ? ` · ${date(candidate.birth_date)}` : ""}`))) : null, form);
  }

  async function refreshPlayer(player, fotmobId, button) {
    if (button) button.disabled = true;
    try {
      const result = await api(`/api/players/${player.id}/refresh`, { method: "POST", body: JSON.stringify(fotmobId ? { fotmob_id: fotmobId } : {}) });
      trackJob(result, state.current);
      $("#player-dialog").close();
      toast("Обновление поставлено в очередь.");
      await loadSeason();
    } catch (error) { toast(errorMessage(error), true); }
    finally { if (button) button.disabled = false; }
  }

  function confirmDelete(player) {
    state.deletePlayer = player;
    formError("delete-error", "");
    $("#delete-description").textContent = `${player.name} будет удалён только из сезона ${currentSeason()?.label || ""}. Игрока можно будет добавить заново.`;
    $("#delete-dialog").showModal();
  }

  document.addEventListener("click", event => {
    const close = event.target.closest("[data-close]");
    if (close) document.getElementById(close.dataset.close)?.close();
    if (event.target.closest(".add-player-trigger")) openAddPlayer();
  });
  document.querySelectorAll("dialog").forEach(dialog => {
    dialog.addEventListener("click", event => {
      if (event.target !== dialog) return;
      const bounds = dialog.getBoundingClientRect();
      if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) dialog.close();
    });
  });
  $("#player-dialog").addEventListener("close", () => { state.selectedPlayer = null; });
  $("#new-season-button").addEventListener("click", openSeason);
  $("#season-year").addEventListener("input", previewSeasonDates);
  $("#season-format").addEventListener("change", previewSeasonDates);
  $("#player-search").addEventListener("input", event => { state.search = event.target.value; renderTable(); });
  $("#position-filter").addEventListener("change", event => { state.position = event.target.value; renderTable(); });
  $("#sort-select").addEventListener("change", event => { state.sort = event.target.value; renderTable(); });
  $("#show-all-players").addEventListener("change", event => { state.showAllPlayers = event.target.checked; renderTable(); });

  $("#add-player-form").addEventListener("submit", async event => {
    event.preventDefault();
    const submit = $("#import-submit");
    submit.disabled = true;
    formError("add-player-error", "");
    try {
      const season = $("#import-season").value;
      const result = await api("/api/players", { method: "POST", body: JSON.stringify({ url: $("#player-url").value.trim(), season }) });
      $("#add-player-dialog").close();
      $("#player-url").value = "";
      if (season !== state.current) await selectSeason(season);
      trackJob(result, season);
      toast(result.duplicate ? "Этот игрок уже добавлен в выбранный сезон." : "Игрок поставлен в очередь на загрузку.");
      await loadSeason();
    } catch (error) { formError("add-player-error", errorMessage(error)); }
    finally { submit.disabled = false; }
  });

  $("#season-form").addEventListener("submit", async event => {
    event.preventDefault();
    const submit = $("button[type=submit]", event.currentTarget);
    submit.disabled = true;
    formError("season-error", "");
    try {
      const result = await api("/api/seasons", { method: "POST", body: JSON.stringify({ start_year: Number($("#season-year").value), calendar: $("#season-format").value === "calendar" }) });
      const season = result.season || result;
      $("#season-dialog").close();
      await loadSeasons();
      await selectSeason(season.key);
    } catch (error) { formError("season-error", errorMessage(error)); }
    finally { submit.disabled = false; }
  });

  $("#delete-confirm").addEventListener("click", async event => {
    if (!state.deletePlayer) return;
    const button = event.currentTarget;
    button.disabled = true;
    formError("delete-error", "");
    try {
      await api(`/api/players/${state.deletePlayer.id}`, { method: "DELETE" });
      $("#delete-dialog").close();
      $("#player-dialog").close();
      state.deletePlayer = null;
      toast("Игрок удалён из сезона.");
      await loadSeasons();
      await loadSeason();
    } catch (error) { formError("delete-error", errorMessage(error)); }
    finally { button.disabled = false; }
  });

  window.addEventListener("beforeunload", () => { if (state.timer) window.clearTimeout(state.timer); });
  (async () => {
    try { await loadSeasons(); await loadSeason(); }
    catch (error) { formError("page-error", errorMessage(error)); }
  })();
})();
