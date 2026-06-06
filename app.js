const state = {
  mode: "login",
  dashboard: null,
  bootstrap: null,
};

const els = {
  toast: document.querySelector("#toast"),
  authView: document.querySelector("#auth-view"),
  appView: document.querySelector("#app-view"),
  loginTab: document.querySelector("#login-tab"),
  registerTab: document.querySelector("#register-tab"),
  authForm: document.querySelector("#auth-form"),
  authSubmit: document.querySelector("#auth-submit"),
  nameField: document.querySelector("#name-field"),
  authName: document.querySelector("#auth-name"),
  authEmail: document.querySelector("#auth-email"),
  authPassword: document.querySelector("#auth-password"),
  referralInput: document.querySelector("#referral-input"),
  previewPrice: document.querySelector("#preview-price"),
  accountName: document.querySelector("#account-name"),
  accountEmail: document.querySelector("#account-email"),
  accessState: document.querySelector("#access-state"),
  accessDays: document.querySelector("#access-days"),
  subscriptionEnd: document.querySelector("#subscription-end"),
  bonusDays: document.querySelector("#bonus-days"),
  vlessLink: document.querySelector("#vless-link"),
  subscriptionLink: document.querySelector("#subscription-link"),
  copyVless: document.querySelector("#copy-vless"),
  copySubscription: document.querySelector("#copy-subscription"),
  refreshKeyButton: document.querySelector("#refresh-key-button"),
  checkConnectionButton: document.querySelector("#check-connection-button"),
  connectionResult: document.querySelector("#connection-result"),
  supportLink: document.querySelector("#support-link"),
  tariffGrid: document.querySelector("#tariff-grid"),
  paymentsBody: document.querySelector("#payments-body"),
  checkLastPaymentButton: document.querySelector("#check-last-payment-button"),
  logoutButton: document.querySelector("#logout-button"),
  createTelegramCode: document.querySelector("#create-telegram-code"),
  refreshTelegramStatus: document.querySelector("#refresh-telegram-status"),
  telegramSyncText: document.querySelector("#telegram-sync-text"),
  telegramCodeBox: document.querySelector("#telegram-code-box"),
  telegramCommand: document.querySelector("#telegram-command"),
  copyTelegramCommand: document.querySelector("#copy-telegram-command"),
  telegramBotLink: document.querySelector("#telegram-bot-link"),
};

function showToast(message) {
  els.toast.textContent = message;
  els.toast.classList.add("is-visible");
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => {
    els.toast.classList.remove("is-visible");
  }, 3200);
}

function setBusy(button, busy, text) {
  if (!button) return;
  button.setAttribute("aria-busy", busy ? "true" : "false");
  if (busy) {
    button.disabled = true;
    const label = button.querySelector("span");
    if (label) {
      button.dataset.oldText = label.textContent.trim();
      label.textContent = text || "Загрузка";
    }
  } else {
    button.disabled = false;
    const label = button.querySelector("span");
    if (label && button.dataset.oldText) {
      label.textContent = button.dataset.oldText;
      delete button.dataset.oldText;
    }
  }
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });

  let payload = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }

  if (!response.ok) {
    const detail = payload?.detail || "Не удалось выполнить запрос";
    throw new Error(detail);
  }

  return payload;
}

function switchMode(mode) {
  state.mode = mode;
  const isRegister = mode === "register";
  els.loginTab.classList.toggle("is-active", !isRegister);
  els.registerTab.classList.toggle("is-active", isRegister);
  els.nameField.classList.toggle("is-hidden", !isRegister);
  els.authSubmit.querySelector("span").textContent = isRegister ? "Создать кабинет" : "Войти";
  els.authPassword.autocomplete = isRegister ? "new-password" : "current-password";
}

function formatDate(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function paymentStatusText(status, processed) {
  if (processed || status === "succeeded") return "оплачен";
  if (status === "canceled") return "отменен";
  if (status === "waiting_for_capture") return "ожидает";
  return "ожидает";
}

function renderTariffs(tariffs) {
  els.tariffGrid.innerHTML = "";
  tariffs.forEach((tariff, index) => {
    const card = document.createElement("article");
    card.className = `tariff-card${index === 2 ? " is-featured" : ""}`;
    card.innerHTML = `
      <h3>${tariff.name}</h3>
      <p class="price">${tariff.price} ₽</p>
      <small>${tariff.days} дней доступа. Ключ создается автоматически после оплаты.</small>
      <button class="primary-button" type="button" data-tariff="${tariff.key}">
        <span>Продлить</span>
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h14M13 6l6 6-6 6" /></svg>
      </button>
    `;
    els.tariffGrid.append(card);
  });
}

function renderPayments(payments) {
  els.paymentsBody.innerHTML = "";

  if (!payments.length) {
    const row = document.createElement("tr");
    row.innerHTML = `<td colspan="5">Платежей пока нет</td>`;
    els.paymentsBody.append(row);
    return;
  }

  payments.forEach((payment) => {
    const row = document.createElement("tr");
    const statusText = paymentStatusText(payment.status, payment.processed);
    const statusClass = payment.processed || payment.status === "succeeded" ? "succeeded" : "pending";
    const canCheck = !payment.processed && payment.payment_id;
    row.innerHTML = `
      <td>${payment.tariff_name || "Тариф"}</td>
      <td>${payment.amount || 0} ₽</td>
      <td><span class="status-tag ${statusClass}">${statusText}</span></td>
      <td>${formatDate(payment.created_at)}</td>
      <td>${canCheck ? `<button class="table-action" type="button" data-payment="${payment.payment_id}">Проверить</button>` : ""}</td>
    `;
    els.paymentsBody.append(row);
  });
}

function renderDashboard(payload) {
  state.dashboard = payload;
  const { account, access, tariffs, payments, support } = payload;

  els.authView.classList.add("is-hidden");
  els.appView.classList.remove("is-hidden");

  els.accountName.textContent = account.display_name || "Аккаунт";
  els.accountEmail.textContent = account.email;

  els.accessState.textContent = access.active ? "активна" : "не активна";
  els.accessState.classList.toggle("is-active", access.active);
  els.accessDays.textContent = access.days_left_text || "Нет активной подписки";
  els.subscriptionEnd.textContent = access.subscription_end_text || "-";
  els.bonusDays.textContent = `${access.referral_bonus_days || 0} дней`;
  els.vlessLink.value = access.vpn_key || "";
  els.subscriptionLink.value = access.subscription_link || "";
  const supportName = String(support.telegram || "").replace(/^@/, "");
  els.supportLink.href = `https://t.me/${supportName}`;
  els.supportLink.textContent = `@${supportName}`;
  els.supportLink.insertAdjacentHTML(
    "afterbegin",
    `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M21 15a4 4 0 0 1-4 4H8l-5 3V7a4 4 0 0 1 4-4h10a4 4 0 0 1 4 4z" /></svg>`
  );

  renderTelegramSync(account);

  renderTariffs(tariffs);
  renderPayments(payments);
}

function renderTelegramSync(account) {
  const telegram = account.telegram || {};
  const username = telegram.username ? `@${String(telegram.username).replace(/^@/, "")}` : `ID ${telegram.user_id || ""}`;

  if (telegram.linked) {
    els.telegramSyncText.textContent = `Кабинет синхронизирован с Telegram ${username}. Сайт показывает подписку, ключ и платежи этого Telegram аккаунта.`;
    els.createTelegramCode.disabled = true;
    els.createTelegramCode.querySelector("span").textContent = "Привязано";
    els.telegramCodeBox.classList.add("is-hidden");
    return;
  }

  els.telegramSyncText.textContent = "Привяжите Telegram аккаунт, чтобы сайт показывал тот же ключ, подписку и историю оплат, что и бот.";
  els.createTelegramCode.disabled = false;
  els.createTelegramCode.querySelector("span").textContent = "Получить код";
}

async function copyValue(input, emptyText) {
  const value = input.value.trim();
  if (!value) {
    showToast(emptyText);
    return;
  }
  await navigator.clipboard.writeText(value);
  showToast("Скопировано");
}

async function createPayment(tariffKey, button) {
  try {
    setBusy(button, true, "Создаем");
    const payment = await api("/api/payments/create", {
      method: "POST",
      body: JSON.stringify({ tariff_key: tariffKey }),
    });
    localStorage.setItem("lipp:lastPaymentId", payment.payment_id);
    showToast("Платеж создан. После оплаты вернитесь в кабинет и нажмите проверку.");
    window.open(payment.confirmation_url, "_blank", "noopener,noreferrer");
    await loadDashboard();
  } catch (error) {
    showToast(error.message);
  } finally {
    setBusy(button, false);
  }
}

async function checkPayment(paymentId) {
  if (!paymentId) {
    showToast("Нет платежа для проверки");
    return;
  }
  const payload = await api(`/api/payments/${encodeURIComponent(paymentId)}/check`, {
    method: "POST",
    body: "{}",
  });
  renderDashboard(payload);
  const result = payload.payment_result;
  if (result?.processed) {
    localStorage.removeItem("lipp:lastPaymentId");
    showToast("Оплата подтверждена, ключ выдан");
  } else {
    showToast("Платеж пока не подтвержден");
  }
}

async function loadDashboard() {
  const payload = await api("/api/me");
  renderDashboard(payload);
}

async function createTelegramCode() {
  const payload = await api("/api/telegram/link-code", {
    method: "POST",
    body: "{}",
  });

  if (payload.linked) {
    await loadDashboard();
    showToast("Telegram уже привязан");
    return;
  }

  els.telegramCommand.textContent = payload.command;
  els.telegramCodeBox.classList.remove("is-hidden");

  if (payload.bot_url) {
    els.telegramBotLink.href = payload.bot_url;
    els.telegramBotLink.classList.remove("is-hidden");
  } else {
    els.telegramBotLink.classList.add("is-hidden");
  }

  showToast("Код создан. Отправьте команду боту.");
}

async function loadBootstrap() {
  try {
    state.bootstrap = await api("/api/bootstrap");
    if (state.bootstrap.tariffs?.length) {
      const minPrice = Math.min(...state.bootstrap.tariffs.map((item) => item.price));
      els.previewPrice.textContent = `от ${minPrice} ₽`;
    }
  } catch {
    els.previewPrice.textContent = "VPN";
  }
}

function bindEvents() {
  els.loginTab.addEventListener("click", () => switchMode("login"));
  els.registerTab.addEventListener("click", () => switchMode("register"));

  els.authForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const isRegister = state.mode === "register";
    const body = {
      email: els.authEmail.value,
      password: els.authPassword.value,
      ...(isRegister ? { name: els.authName.value, referral: els.referralInput.value } : {}),
    };

    try {
      setBusy(els.authSubmit, true, isRegister ? "Создаем" : "Входим");
      const payload = await api(isRegister ? "/api/auth/register" : "/api/auth/login", {
        method: "POST",
        body: JSON.stringify(body),
      });
      renderDashboard(payload);
      showToast(isRegister ? "Кабинет создан" : "Вы вошли");
    } catch (error) {
      showToast(error.message);
    } finally {
      setBusy(els.authSubmit, false);
    }
  });

  els.copyVless.addEventListener("click", () => copyValue(els.vlessLink, "Ключ появится после оплаты"));
  els.copySubscription.addEventListener("click", () => copyValue(els.subscriptionLink, "Ссылка появится после оплаты"));

  els.tariffGrid.addEventListener("click", (event) => {
    const button = event.target.closest("[data-tariff]");
    if (!button) return;
    createPayment(button.dataset.tariff, button);
  });

  els.paymentsBody.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-payment]");
    if (!button) return;
    try {
      button.disabled = true;
      await checkPayment(button.dataset.payment);
    } catch (error) {
      showToast(error.message);
    } finally {
      button.disabled = false;
    }
  });

  els.checkLastPaymentButton.addEventListener("click", async () => {
    try {
      setBusy(els.checkLastPaymentButton, true, "Проверяем");
      const paymentId = localStorage.getItem("lipp:lastPaymentId");
      await checkPayment(paymentId);
    } catch (error) {
      showToast(error.message);
    } finally {
      setBusy(els.checkLastPaymentButton, false);
    }
  });

  els.refreshKeyButton.addEventListener("click", async () => {
    try {
      setBusy(els.refreshKeyButton, true, "Обновляем");
      const payload = await api("/api/access/refresh", {
        method: "POST",
        body: "{}",
      });
      renderDashboard(payload);
      showToast("Ключ обновлен");
    } catch (error) {
      showToast(error.message);
    } finally {
      setBusy(els.refreshKeyButton, false);
    }
  });

  els.checkConnectionButton.addEventListener("click", async () => {
    try {
      els.connectionResult.textContent = "Проверяем подключение...";
      const status = await api("/api/access/check", {
        method: "POST",
        body: "{}",
      });
      els.connectionResult.textContent = `Трафик: ${status.total}. Последняя активность: ${status.last_online}.`;
    } catch (error) {
      els.connectionResult.textContent = "";
      showToast(error.message);
    }
  });

  els.logoutButton.addEventListener("click", async () => {
    try {
      await api("/api/auth/logout", {
        method: "POST",
        body: "{}",
      });
    } catch {
      // Local UI state still needs to reset even when the session is already gone.
    }
    state.dashboard = null;
    els.appView.classList.add("is-hidden");
    els.authView.classList.remove("is-hidden");
    showToast("Вы вышли");
  });

  els.createTelegramCode.addEventListener("click", async () => {
    try {
      setBusy(els.createTelegramCode, true, "Создаем");
      await createTelegramCode();
    } catch (error) {
      showToast(error.message);
    } finally {
      if (!state.dashboard?.account?.telegram?.linked) {
        setBusy(els.createTelegramCode, false);
      }
    }
  });

  els.copyTelegramCommand.addEventListener("click", async () => {
    const command = els.telegramCommand.textContent.trim();
    if (!command) {
      showToast("Сначала получите код");
      return;
    }
    await navigator.clipboard.writeText(command);
    showToast("Команда скопирована");
  });

  els.refreshTelegramStatus.addEventListener("click", async () => {
    try {
      setBusy(els.refreshTelegramStatus, true, "Обновляем");
      await loadDashboard();
      showToast("Статус обновлен");
    } catch (error) {
      showToast(error.message);
    } finally {
      setBusy(els.refreshTelegramStatus, false);
    }
  });
}

async function init() {
  const params = new URLSearchParams(window.location.search);
  const ref = params.get("ref") || params.get("start") || "";
  els.referralInput.value = ref;
  bindEvents();
  switchMode("login");
  await loadBootstrap();

  try {
    await loadDashboard();
  } catch {
    els.authView.classList.remove("is-hidden");
    els.appView.classList.add("is-hidden");
  }
}

init();
