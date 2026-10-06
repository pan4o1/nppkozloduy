const DATA_URL = "data/consultations.json";
const PAGE_SIZE = 15;

const state = {
  items: [],
  filtered: [],
  page: 1,
  status: "all",
  quickFilter: "all",
};

const el = (id) => document.getElementById(id);
const resultList = el("resultList");
const detailDialog = el("detailDialog");
const collator = new Intl.Collator("bg", { sensitivity: "base", numeric: true });
const dateFormatter = new Intl.DateTimeFormat("bg-BG", { day: "2-digit", month: "short", year: "numeric" });

function toDate(value) {
  if (!value) return null;
  const date = new Date(`${value}T12:00:00`);
  return Number.isNaN(date.getTime()) ? null : date;
}

function today() {
  const now = new Date();
  return new Date(now.getFullYear(), now.getMonth(), now.getDate(), 12);
}

function dateLabel(value) {
  const date = toDate(value);
  return date ? dateFormatter.format(date) : "Не е посочена";
}

function daysBetween(a, b) {
  return Math.ceil((b - a) / 86400000);
}

function getStatus(item) {
  const now = today();
  const from = toDate(item.valid_from);
  const deadline = toDate(item.valid_to);
  if (from && from > now) return { key: "upcoming", label: "Предстояща", days: daysBetween(now, from) };
  if (!deadline) return { key: "active", label: "Без краен срок", days: null };
  const days = daysBetween(now, deadline);
  if (days < 0) return { key: "closed", label: "Приключила", days };
  if (days <= 7) return { key: "closing", label: "Приключва скоро", days };
  return { key: "active", label: "Активна", days };
}

function getCategory(item) {
  const text = `${item.title} ${item.description}`.toLocaleLowerCase("bg");
  if (/софтуер|система|информацион|компют|лиценз|абонамент|мреж|сървър|кибер|електронна поща|firewall|paloalto|it\b/.test(text)) return "it";
  if (/строител|ремонт|подмяна|изграждан|реконструк|монтаж|демонтаж/.test(text)) return "construction";
  if (/поддръж|услуг|обслужване|контрол|анализ|изпитване|проектиране|обучение|оценка|консулт/.test(text)) return "service";
  if (/доставка|резервни части|консуматив|оборудване|материал|закупуване/.test(text)) return "delivery";
  return "other";
}

function remainingLabel(status) {
  if (status.key === "closed") return "Приключила";
  if (status.key === "upcoming") return `след ${status.days} дни`;
  if (status.days === null) return "не е посочен срок";
  if (status.days === 0) return "днес";
  if (status.days === 1) return "1 ден";
  return `${status.days} дни`;
}

function updateStats() {
  const now = today();
  const active = state.items.filter((item) => ["active", "closing"].includes(getStatus(item).key)).length;
  const closing = state.items.filter((item) => getStatus(item).key === "closing").length;
  const year = state.items.filter((item) => toDate(item.valid_from)?.getFullYear() === now.getFullYear()).length;
  const withOffers = state.items.filter((item) => Number(item.offer_count || 0) > 0).length;
  const publishedOffers = state.items.reduce((sum, item) => sum + Number(item.offer_count || 0), 0);
  el("activeCount").textContent = active.toLocaleString("bg-BG");
  el("closingCount").textContent = closing.toLocaleString("bg-BG");
  el("yearCount").textContent = year.toLocaleString("bg-BG");
  el("offersCount").textContent = withOffers.toLocaleString("bg-BG");
  el("publishedOffersCount").textContent = publishedOffers.toLocaleString("bg-BG");
  el("totalCount").textContent = state.items.length.toLocaleString("bg-BG");
  el("currentYearLabel").textContent = `за ${now.getFullYear()} година`;
}

function filterItems() {
  const query = el("searchInput").value.trim().toLocaleLowerCase("bg");
  const category = el("categorySelect").value;
  const offersFilter = el("offersSelect").value;
  const from = el("dateFrom").value;
  const to = el("dateTo").value;

  state.filtered = state.items.filter((item) => {
    const status = getStatus(item).key;
    const offerText = (item.offers || []).map((offer) => `${offer.participant || ""} ${offer.name || ""}`).join(" ");
    const searchable = `${item.title} ${item.reference} ${item.description} ${offerText}`.toLocaleLowerCase("bg");
    const statusMatch =
      state.status === "all" ||
      (state.status === "active" && ["active", "closing"].includes(status)) ||
      state.status === status;
    const quickMatch =
      state.quickFilter === "all" ||
      (state.quickFilter === "active" && ["active", "closing"].includes(status)) ||
      (state.quickFilter === "closing" && status === "closing") ||
      (state.quickFilter === "offers" && Number(item.offer_count || 0) > 0) ||
      (state.quickFilter === "year" && toDate(item.valid_from)?.getFullYear() === today().getFullYear());
    const offersMatch =
      offersFilter === "all" ||
      (offersFilter === "has" && Number(item.offer_count || 0) > 0) ||
      (offersFilter === "none" && item.details_checked && Number(item.offer_count || 0) === 0) ||
      (offersFilter === "pending" && !item.details_checked);
    return (
      (!query || searchable.includes(query)) &&
      statusMatch && quickMatch &&
      offersMatch &&
      (category === "all" || getCategory(item) === category) &&
      (!from || (item.valid_from && item.valid_from >= from)) &&
      (!to || (item.valid_from && item.valid_from <= to))
    );
  });

  const sort = el("sortSelect").value;
  state.filtered.sort((a, b) => {
    if (sort === "oldest") return String(a.valid_from || "").localeCompare(String(b.valid_from || ""));
    if (sort === "deadline") {
      const aTime = toDate(a.valid_to)?.getTime() ?? Number.MAX_SAFE_INTEGER;
      const bTime = toDate(b.valid_to)?.getTime() ?? Number.MAX_SAFE_INTEGER;
      const aClosed = getStatus(a).key === "closed";
      const bClosed = getStatus(b).key === "closed";
      if (aClosed !== bClosed) return aClosed ? 1 : -1;
      return aTime - bTime;
    }
    const dateOrder = String(b.valid_from || "").localeCompare(String(a.valid_from || ""));
    return dateOrder || collator.compare(String(b.id), String(a.id));
  });

  const maxPage = Math.max(1, Math.ceil(state.filtered.length / PAGE_SIZE));
  state.page = Math.min(state.page, maxPage);
  renderResults();
}

function renderResults() {
  resultList.replaceChildren();
  const start = (state.page - 1) * PAGE_SIZE;
  const visible = state.filtered.slice(start, start + PAGE_SIZE);
  el("resultCount").textContent = state.filtered.length.toLocaleString("bg-BG");
  el("resultNoun").textContent = state.filtered.length === 1 ? "консултация" : "консултации";
  el("emptyState").hidden = visible.length > 0;

  const fragment = document.createDocumentFragment();
  visible.forEach((item) => {
    const status = getStatus(item);
    const card = el("resultTemplate").content.firstElementChild.cloneNode(true);
    card.dataset.status = status.key;
    const badge = card.querySelector(".status-badge");
    badge.textContent = status.label;
    badge.dataset.status = status.key;
    card.querySelector(".reference").textContent = item.reference ? `№ ${item.reference}` : "Без референтен номер";
    const offerBadge = card.querySelector(".offer-badge");
    if (Number(item.offer_count || 0) > 0) {
      offerBadge.hidden = false;
      offerBadge.textContent = item.offer_count === 1 ? "1 предложение" : `${item.offer_count} предложения`;
    }
    card.querySelector("h3").textContent = item.title;
    card.querySelector(".result-description").textContent = item.description || "Пазарна консултация";
    card.querySelector(".date-from").textContent = dateLabel(item.valid_from);
    card.querySelector(".date-to").textContent = dateLabel(item.valid_to);
    card.querySelector(".days-left strong").textContent = remainingLabel(status);
    card.querySelector(".open-detail").addEventListener("click", () => openDetail(item));
    fragment.append(card);
  });
  resultList.append(fragment);
  renderPagination();
}

function renderPagination() {
  const nav = el("pagination");
  nav.replaceChildren();
  const totalPages = Math.ceil(state.filtered.length / PAGE_SIZE);
  if (totalPages <= 1) return;

  const addButton = (label, page, options = {}) => {
    const button = document.createElement("button");
    button.className = "page-button";
    button.type = "button";
    button.textContent = label;
    button.disabled = options.disabled || false;
    if (options.current) button.setAttribute("aria-current", "page");
    if (options.label) button.setAttribute("aria-label", options.label);
    button.addEventListener("click", () => {
      state.page = page;
      renderResults();
      el("results").scrollIntoView({ behavior: "smooth", block: "start" });
    });
    nav.append(button);
  };

  addButton("‹", Math.max(1, state.page - 1), { disabled: state.page === 1, label: "Предишна страница" });
  const pages = [...new Set([1, state.page - 1, state.page, state.page + 1, totalPages])].filter((p) => p > 0 && p <= totalPages).sort((a, b) => a - b);
  let previous = 0;
  pages.forEach((page) => {
    if (previous && page - previous > 1) {
      const dots = document.createElement("span");
      dots.textContent = "…";
      dots.className = "page-button";
      dots.style.display = "grid";
      dots.style.placeItems = "center";
      nav.append(dots);
    }
    addButton(String(page), page, { current: page === state.page, label: `Страница ${page}` });
    previous = page;
  });
  addButton("›", Math.min(totalPages, state.page + 1), { disabled: state.page === totalPages, label: "Следваща страница" });
}

function openDetail(item) {
  const status = getStatus(item);
  const badge = el("dialogStatus");
  badge.textContent = status.label;
  badge.dataset.status = status.key;
  el("dialogTitle").textContent = item.title;
  el("dialogReference").textContent = item.reference || "Не е посочен";
  el("dialogFrom").textContent = dateLabel(item.valid_from);
  el("dialogTo").textContent = dateLabel(item.valid_to);
  el("dialogRemaining").textContent = remainingLabel(status);
  el("dialogDescription").textContent = item.description || "Пазарна консултация по чл. 44 от ЗОП.";
  renderDialogOffers(item);
  el("dialogLink").href = item.url;
  detailDialog.showModal();
}

function renderDialogOffers(item) {
  const section = el("dialogOffersSection");
  const list = el("dialogOffersList");
  const offers = Array.isArray(item.offers) ? item.offers : [];
  list.replaceChildren();
  section.hidden = offers.length === 0;
  if (!offers.length) return;
  el("dialogOfferCount").textContent = offers.length === 1 ? "1 файл" : `${offers.length} файла`;
  offers.forEach((offer, index) => {
    const row = document.createElement("div");
    row.className = "offer-row";
    const info = document.createElement("div");
    const participant = document.createElement("strong");
    participant.textContent = offer.participant || `Участник ${index + 1} — името не е посочено`;
    const filename = document.createElement("small");
    filename.textContent = offer.name || "Индикативно предложение";
    const link = document.createElement("a");
    link.href = offer.url;
    link.target = "_blank";
    link.rel = "noreferrer";
    link.textContent = "Отвори файла";
    info.append(participant, filename);
    row.append(info, link);
    list.append(row);
  });
}

function resetFilters() {
  el("searchInput").value = "";
  el("categorySelect").value = "all";
  el("offersSelect").value = "all";
  el("dateFrom").value = "";
  el("dateTo").value = "";
  document.querySelector('input[name="status"][value="all"]').checked = true;
  state.status = "all";
  state.quickFilter = "all";
  state.page = 1;
  document.querySelectorAll(".stat-card").forEach((card) => card.classList.toggle("active-stat", card.dataset.quickFilter === "all"));
  filterItems();
}

async function loadData() {
  el("loadingState").hidden = false;
  el("errorState").hidden = true;
  resultList.hidden = true;
  try {
    const response = await fetch(`${DATA_URL}?v=${Date.now()}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    state.items = Array.isArray(payload.items) ? payload.items : [];
    const generated = new Date(payload.generated_at);
    el("asOfDate").textContent = Number.isNaN(generated.getTime()) ? "неизвестна дата" : new Intl.DateTimeFormat("bg-BG", { dateStyle: "medium", timeStyle: "short" }).format(generated);
    el("syncState").classList.remove("is-error");
    el("syncState").lastElementChild.textContent = `Синхронизирано · ${state.items.length.toLocaleString("bg-BG")} записа`;
    updateStats();
    state.page = 1;
    filterItems();
    el("loadingState").hidden = true;
    resultList.hidden = false;
  } catch (error) {
    console.error(error);
    el("loadingState").hidden = true;
    el("errorState").hidden = false;
    el("syncState").classList.add("is-error");
    el("syncState").lastElementChild.textContent = "Няма връзка с данните";
  }
}

let searchTimer;
el("searchInput").addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => { state.page = 1; filterItems(); }, 120);
});
["categorySelect", "offersSelect", "dateFrom", "dateTo", "sortSelect"].forEach((id) => el(id).addEventListener("change", () => { state.page = 1; filterItems(); }));
document.querySelectorAll('input[name="status"]').forEach((radio) => radio.addEventListener("change", (event) => {
  state.status = event.target.value;
  state.quickFilter = "all";
  state.page = 1;
  document.querySelectorAll(".stat-card").forEach((card) => card.classList.remove("active-stat"));
  filterItems();
}));
document.querySelectorAll(".stat-card").forEach((card) => card.addEventListener("click", () => {
  state.quickFilter = card.dataset.quickFilter;
  state.status = "all";
  document.querySelector('input[name="status"][value="all"]').checked = true;
  document.querySelectorAll(".stat-card").forEach((item) => item.classList.toggle("active-stat", item === card));
  state.page = 1;
  filterItems();
  el("results").scrollIntoView({ behavior: "smooth", block: "start" });
}));
el("resetFilters").addEventListener("click", resetFilters);
el("refreshButton").addEventListener("click", loadData);
el("retryButton").addEventListener("click", loadData);
el("dialogClose").addEventListener("click", () => detailDialog.close());
detailDialog.addEventListener("click", (event) => { if (event.target === detailDialog) detailDialog.close(); });

loadData();
