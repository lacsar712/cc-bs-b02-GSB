import m from "mithril";

const TOKEN_KEY = "bridge_strain_token";
const USER_KEY = "bridge_strain_user";

function verdictClass(verdict, status) {
  if (verdict === "合格") return "tag pass";
  if (verdict === "越界") return "tag fail";
  if (status === "pending" || status === "processing") return "tag wait";
  return "tag wait";
}

function displayVerdict(row) {
  if (row.verdict) return row.verdict;
  if (row.status === "pending") return "待处理";
  if (row.status === "processing") return "处理中";
  return "—";
}

const state = {
  token: localStorage.getItem(TOKEN_KEY) || "",
  user: null,
  route: location.hash.replace(/^#/, "") || "/",
  loginForm: { username: "surveyor", password: "surv123456" },
  submitForm: { span_code: "", microstrain: "" },
  rows: [],
  gate: null, // {enabled, threshold_ms, latest_wind, gate_blocking, ...}
  events: [],
  thresholdDraft: "",
  sampleDraft: "8.5",
  error: "",
  msg: "",
  loading: false,
  timer: null,
};

try {
  state.user = JSON.parse(localStorage.getItem(USER_KEY) || "null");
} catch {
  state.user = null;
}

async function api(path, opts = {}) {
  const headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  const res = await fetch(path, { ...opts, headers });
  const text = await res.text();
  let data = {};
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    data = { detail: text };
  }
  if (!res.ok) throw Object.assign(new Error(data.detail || res.statusText), { status: res.status, data });
  return data;
}

async function loadReadings() {
  if (!state.token) return;
  try {
    state.rows = await api("/api/readings");
    state.error = "";
  } catch {
    state.error = "加载列表失败，请重新登录";
  }
}

async function loadGate() {
  if (!state.token) return;
  try {
    state.gate = await api("/api/wind-gate");
    if (state.thresholdDraft === "" && state.gate) {
      state.thresholdDraft = String(state.gate.threshold_ms);
    }
  } catch {
    /* 顶栏芯片静默 */
  }
}

async function loadEvents() {
  if (!state.token) return;
  try {
    state.events = await api("/api/wind-gate/events");
  } catch {
    /* 流水静默 */
  }
}

async function refreshActive() {
  if (!state.token) return;
  await loadGate();
  if (state.route === "/wind-gate") {
    await loadEvents();
  } else {
    await loadReadings();
  }
  m.redraw();
}

function startPolling() {
  if (state.timer) clearInterval(state.timer);
  if (!state.token) return;
  state.timer = setInterval(refreshActive, 3000);
}

function syncRoute() {
  state.route = location.hash.replace(/^#/, "") || "/";
  refreshActive();
  m.redraw();
}
window.addEventListener("hashchange", syncRoute);

function logout() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
  state.token = "";
  state.user = null;
  state.rows = [];
  state.gate = null;
  state.events = [];
  if (state.timer) clearInterval(state.timer);
  location.hash = "";
}

/* ---------------- 顶栏风况芯片 ---------------- */

function WindChip() {
  const g = state.gate;
  let cls = "windchip";
  let label = "风况联闸 · 未加载";
  let href = "#/wind-gate";
  if (g) {
    const w = g.latest_wind ? `${g.latest_wind.wind_ms} m/s` : "无采样";
    if (g.gate_blocking) {
      cls += " block";
      label = `大风挡回中 · ${w}`;
    } else if (g.enabled) {
      cls += " on";
      label = `联闸开 · ${w} / 阈值 ${g.threshold_ms}`;
    } else {
      label = `联闸关 · ${w} / 阈值 ${g.threshold_ms}`;
    }
  }
  return m("a", { class: cls, href }, label);
}

function Topbar() {
  const isWriter = state.user?.role === "writer";
  return m("div.topbar", [
    m("div", [
      m("h1", "桥梁应变班交台"),
      m("p.sub", "微应变 80～220 με 为合格，否则为越界。"),
    ]),
    m("div", { style: { display: "flex", gap: "0.6rem", alignItems: "center" } }, [
      WindChip(),
      m("span", { class: "muted" },
        `${state.user?.username}（${isWriter ? "测量员" : "复核员"}）`),
      m("button.secondary", { type: "button", onclick: logout }, "退出"),
    ]),
  ]);
}

/* ---------------- 读数上报 + 列表 ---------------- */

function SubmitCard() {
  return m("div.card", [
    m("h2", { style: { marginTop: 0, fontSize: "1.1rem" } }, "提交读数"),
    m(
      "form",
      {
        onsubmit: async (e) => {
          e.preventDefault();
          state.error = "";
          state.msg = "";
          state.loading = true;
          try {
            const data = await api("/api/readings", {
              method: "POST",
              body: JSON.stringify({
                span_code: state.submitForm.span_code,
                microstrain: parseFloat(state.submitForm.microstrain),
              }),
            });
            state.msg = data.message || "已提交";
            state.submitForm = { span_code: "", microstrain: "" };
            await loadReadings();
          } catch (err) {
            state.error = err.message || "提交失败";
          } finally {
            state.loading = false;
            m.redraw();
          }
        },
      },
      [
        m("div.row", [
          m("label", [
            "跨段编号",
            m("input", {
              required: true,
              placeholder: "例如 跨中S3",
              value: state.submitForm.span_code,
              oninput: (e) => { state.submitForm.span_code = e.target.value; },
            }),
          ]),
          m("label", [
            "微应变（με）",
            m("input", {
              required: true,
              type: "number",
              step: "0.1",
              value: state.submitForm.microstrain,
              oninput: (e) => { state.submitForm.microstrain = e.target.value; },
            }),
          ]),
          m("button", { type: "submit", disabled: state.loading }, "提交"),
        ]),
        state.error ? m("p.err", state.error) : null,
        state.msg ? m("p.ok", state.msg) : null,
      ]
    ),
  ]);
}

function ReadingsCard() {
  return m("div.card", [
    m("h2", { style: { marginTop: 0, fontSize: "1.1rem" } }, "读数列表"),
    m("table", [
      m("thead", [
        m("tr", [
          m("th", "编号"), m("th", "跨段"), m("th", "微应变"), m("th", "结论"),
          m("th", "说明"), m("th", "状态"), m("th", "提交人"),
        ]),
      ]),
      m(
        "tbody",
        state.rows.length
          ? state.rows.map((r) =>
              m("tr", { key: r.id }, [
                m("td", r.id),
                m("td", r.span_code),
                m("td", r.microstrain),
                m("td", [m("span", { class: verdictClass(r.verdict, r.status) }, displayVerdict(r))]),
                m("td", r.reason || "—"),
                m("td", r.status),
                m("td", r.created_by),
              ])
            )
          : [m("tr", m("td", { colspan: 7 }, "暂无数据"))]
      ),
    ]),
  ]);
}

/* ---------------- 风况联闸专页 ---------------- */

function GateStatusBanner() {
  const g = state.gate;
  if (!g) return null;
  if (g.gate_blocking) {
    return m("div.banner.block",
      `江面大风：最新风速 ${g.latest_wind.wind_ms} m/s ＞ 阈值 ${g.threshold_ms} m/s，读数上报一律挡回中。`);
  }
  if (g.enabled) {
    const w = g.latest_wind ? `${g.latest_wind.wind_ms} m/s` : "暂无服务端采样";
    return m("div.banner.info",
      `联闸已开启（阈值 ${g.threshold_ms} m/s），当前服务端风速 ${w}，上报正常放行。`);
  }
  return m("div.banner.info", "联闸已关闭，读数上报正常放行。");
}

async function saveGate(patch, okMsg) {
  state.error = "";
  state.msg = "";
  try {
    state.gate = await api("/api/wind-gate", { method: "PUT", body: JSON.stringify(patch) });
    state.thresholdDraft = String(state.gate.threshold_ms);
    state.msg = okMsg;
    await loadEvents();
  } catch (err) {
    state.error = err.message || "保存失败";
  }
  m.redraw();
}

function WindGatePage() {
  const isWriter = state.user?.role === "writer";
  const g = state.gate;
  const wind = g?.latest_wind;

  return m("div", [
    m("p", { style: { marginTop: 0 } }, [
      m("a", { href: "#/" }, "← 返回读数台"),
    ]),

    isWriter
      ? null
      : m("div.banner.readonly",
          "复核员为只读权限：可查看风速阈值、联闸开关状态与联闸流水，不能切换开关或调整阈值。"),

    GateStatusBanner(),

    m("div.card", [
      m("h2", { style: { marginTop: 0, fontSize: "1.1rem" } }, "联闸开关与风速阈值"),
      m("div.gate-line", [
        m("label", { class: "switch", title: isWriter ? "切换联闸" : "只读" }, [
          m("input", {
            type: "checkbox",
            checked: !!g?.enabled,
            disabled: !isWriter,
            onchange: (e) => saveGate({ enabled: e.target.checked },
              e.target.checked ? "联闸已开启" : "联闸已关闭"),
          }),
          m("span.slider"),
        ]),
        m("strong", g?.enabled ? "联闸：开（大风挡回）" : "联闸：关（放行）"),
      ]),
      m("div.row", { style: { marginTop: "0.9rem" } }, [
        m("label", [
          "风速阈值（m/s）",
          m("input", {
            type: "number",
            step: "0.1",
            min: "0",
            max: "100",
            value: state.thresholdDraft,
            disabled: !isWriter,
            oninput: (e) => { state.thresholdDraft = e.target.value; },
          }),
        ]),
        isWriter
          ? m("button", {
              type: "button",
              onclick: () => {
                const v = parseFloat(state.thresholdDraft);
                if (Number.isNaN(v)) { state.error = "阈值必须是数字"; state.msg = ""; m.redraw(); return; }
                saveGate({ threshold_ms: v }, `阈值已保存为 ${v} m/s`);
              },
            }, "保存阈值")
          : null,
      ]),
      g?.updated_by
        ? m("p.muted", { style: { marginBottom: 0 } },
            `最近由 ${g.updated_by} 于 ${(g.updated_at || "").replace("T", " ").slice(0, 19)} 更新`)
        : null,
      state.error ? m("p.err", state.error) : null,
      state.msg ? m("p.ok", state.msg) : null,
    ]),

    m("div.card", [
      m("h2", { style: { marginTop: 0, fontSize: "1.1rem" } }, "服务端风速采样"),
      m("div.gate-line", [
        m("span.wind-now", wind ? `${wind.wind_ms} m/s` : "—"),
        wind
          ? m("span",
              { class: wind.over_threshold ? "ev-blocked" : "ev-enabled" },
              wind.over_threshold ? `超过阈值 ${g.threshold_ms} m/s` : `低于阈值 ${g.threshold_ms} m/s`)
          : m("span.muted", "尚无服务端风速样本"),
      ]),
      m("p.muted",
        "风速只能由服务端采集（岸基风速仪／服务端演练接口），读数上报接口不接受任何前端风速字段，无法伪造风速蒙混。"),
      isWriter
        ? m("div.row", [
            m("label", [
              "录入服务端风速（m/s，演练）",
              m("input", {
                type: "number",
                step: "0.1",
                min: "0",
                max: "100",
                value: state.sampleDraft,
                oninput: (e) => { state.sampleDraft = e.target.value; },
              }),
            ]),
            m("button", {
              type: "button",
              onclick: async () => {
                state.error = "";
                state.msg = "";
                try {
                  const v = parseFloat(state.sampleDraft);
                  const d = await api("/api/wind-gate/samples", {
                    method: "POST",
                    body: JSON.stringify({ wind_ms: v, source: "drill-simulator" }),
                  });
                  state.msg = d.message || "风速已记录";
                  await loadGate();
                  await loadEvents();
                } catch (err) {
                  state.error = err.message || "录入失败";
                }
                m.redraw();
              },
            }, "录入风速"),
            m("button.secondary", {
              type: "button",
              onclick: async () => {
                state.sampleDraft = "8.5";
                state.error = ""; state.msg = "";
                try {
                  await api("/api/wind-gate/samples", {
                    method: "POST",
                    body: JSON.stringify({ wind_ms: 8.5, source: "drill-simulator" }),
                  });
                  state.msg = "已模拟超阈值大风 8.5 m/s（请先把阈值调到极低）";
                  await loadGate(); await loadEvents();
                } catch (err) { state.error = err.message; }
                m.redraw();
              },
            }, "模拟大风 8.5"),
          ])
        : m("p.muted", { style: { marginBottom: 0 } }, "复核员不可录入风速采样。"),
    ]),

    m("div.card", [
      m("h2", { style: { marginTop: 0, fontSize: "1.1rem" } }, "联闸流水"),
      m("table", [
        m("thead", [
          m("tr", [
            m("th", "#"), m("th", "时间"), m("th", "类型"), m("th", "风速"),
            m("th", "阈值"), m("th", "跨段"), m("th", "操作人"), m("th", "说明"),
          ]),
        ]),
        m(
          "tbody",
          state.events.length
            ? state.events.map((e) =>
                m("tr", { key: e.id }, [
                  m("td", e.id),
                  m("td", (e.created_at || "").replace("T", " ").slice(0, 19)),
                  m("td", [m("span", { class: "ev-" + e.event_type }, eventLabel(e.event_type))]),
                  m("td", e.wind_ms == null ? "—" : `${e.wind_ms} m/s`),
                  m("td", e.threshold_ms == null ? "—" : `${e.threshold_ms} m/s`),
                  m("td", e.span_code || "—"),
                  m("td", e.operator),
                  m("td", e.detail || "—"),
                ])
              )
            : [m("tr", m("td", { colspan: 8 }, "暂无流水"))]
        ),
      ]),
    ]),
  ]);
}

function eventLabel(t) {
  return {
    enabled: "开启联闸",
    disabled: "关闭联闸",
    threshold_changed: "调整阈值",
    blocked: "挡回上报",
  }[t] || t;
}

/* ---------------- 根组件 ---------------- */

const App = {
  oninit() {
    if (state.token) {
      refreshActive();
      startPolling();
    }
  },
  onremove() {
    if (state.timer) clearInterval(state.timer);
  },
  view() {
    if (!state.token) {
      return m("div.wrap", [
        m("h1", "桥梁应变班交台"),
        m("p.sub", "测量员提交跨段编号与微应变读数，江面大风时联闸可联动挡回上报。"),
        m("div.card", [
          m(
            "form",
            {
              onsubmit: async (e) => {
                e.preventDefault();
                state.error = "";
                state.loading = true;
                try {
                  const data = await api("/api/auth/login", {
                    method: "POST",
                    body: JSON.stringify(state.loginForm),
                  });
                  state.token = data.access_token;
                  state.user = { username: data.username, role: data.role };
                  localStorage.setItem(TOKEN_KEY, state.token);
                  localStorage.setItem(USER_KEY, JSON.stringify(state.user));
                  await refreshActive();
                  startPolling();
                } catch {
                  state.error = "用户名或密码错误";
                } finally {
                  state.loading = false;
                  m.redraw();
                }
              },
            },
            [
              m("div.row", [
                m("label", ["用户名", m("input", {
                  value: state.loginForm.username,
                  oninput: (e) => { state.loginForm.username = e.target.value; },
                })]),
                m("label", ["密码", m("input", {
                  type: "password",
                  value: state.loginForm.password,
                  oninput: (e) => { state.loginForm.password = e.target.value; },
                })]),
                m("button", { type: "submit", disabled: state.loading }, "登录"),
              ]),
              state.error ? m("p.err", state.error) : null,
            ]
          ),
          m("p.sub", { style: { marginBottom: 0 } },
            "测量员 surveyor / surv123456 · 复核员 reviewer / rev123456"),
        ]),
      ]);
    }

    const isWriter = state.user?.role === "writer";
    return m("div.wrap", [
      Topbar(),
      state.route === "/wind-gate"
        ? WindGatePage()
        : [
            isWriter ? SubmitCard() : null,
            ReadingsCard(),
          ],
    ]);
  },
};

export default App;
