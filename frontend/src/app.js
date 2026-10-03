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

const EVENT_LABELS = {
  open: "打开联闸",
  close: "关闭联闸",
  threshold: "调整阈值",
  blocked: "挡回报送",
};

function eventTagClass(eventType) {
  if (eventType === "blocked") return "tag fail";
  if (eventType === "open") return "tag wait";
  if (eventType === "close") return "tag pass";
  return "tag";
}

function fmtTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

const state = {
  token: localStorage.getItem(TOKEN_KEY) || "",
  user: null,
  view: "report",
  loginForm: { username: "surveyor", password: "surv123456" },
  submitForm: { span_code: "", microstrain: "" },
  rows: [],
  gate: null,
  gateEvents: [],
  gateForm: { threshold_ms: "" },
  gateFormDirty: false,
  error: "",
  msg: "",
  gateError: "",
  gateMsg: "",
  loading: false,
  gateSaving: false,
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
  if (!res.ok) throw new Error(data.detail || res.statusText);
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
  m.redraw();
}

async function loadGate() {
  if (!state.token) return;
  try {
    const [gate, events] = await Promise.all([
      api("/api/wind-gate"),
      api("/api/wind-gate/events"),
    ]);
    state.gate = gate;
    state.gateEvents = events;
    if (!state.gateFormDirty) state.gateForm.threshold_ms = gate.threshold_ms;
  } catch {
    // 联闸数据加载失败时静默，下一轮轮询重试
  }
}

function startPolling() {
  if (state.timer) clearInterval(state.timer);
  if (!state.token) return;
  state.timer = setInterval(() => {
    loadReadings();
    loadGate();
  }, 3000);
}

async function saveGate(enabled, threshold) {
  state.gateError = "";
  state.gateMsg = "";
  state.gateSaving = true;
  try {
    const data = await api("/api/wind-gate", {
      method: "PUT",
      body: JSON.stringify({ enabled, threshold_ms: threshold }),
    });
    state.gate = data;
    state.gateForm.threshold_ms = data.threshold_ms;
    state.gateFormDirty = false;
    state.gateMsg = enabled
      ? `联闸已打开：风速超过 ${data.threshold_ms} m/s 时报送将被挡回`
      : "联闸已关闭，报送立即恢复";
    await loadGate();
  } catch (err) {
    state.gateError = err.message || "保存失败";
  } finally {
    state.gateSaving = false;
    m.redraw();
  }
}

function gateThresholdInput() {
  return m("label", [
    "风速阈值（m/s）",
    m("input", {
      type: "number",
      step: "0.1",
      min: "0.1",
      max: "100",
      value: state.gateForm.threshold_ms,
      oninput: (e) => {
        state.gateForm.threshold_ms = e.target.value;
        state.gateFormDirty = true;
      },
    }),
  ]);
}

function gateView(isWriter) {
  const g = state.gate;
  return [
    m("div.card", { key: "gate-control" }, [
      m("h2", { style: { marginTop: 0, fontSize: "1.1rem" } }, "联闸控制"),
      !g
        ? m("p.sub", "加载中…")
        : [
            m("div.row", [
              m("div", [
                m("div.kv-label", "联闸状态"),
                m(
                  "span",
                  { class: g.enabled ? "tag fail" : "tag pass" },
                  g.enabled ? "已开启 · 超阈值挡回" : "已关闭 · 正常报送"
                ),
              ]),
              m("div", [
                m("div.kv-label", "当前风速（服务端采样）"),
                m("strong", `${g.wind_speed} m/s`),
              ]),
              m("div", [
                m("div.kv-label", "风速阈值"),
                m("strong", `${g.threshold_ms} m/s`),
              ]),
              g.updated_by
                ? m("div", [
                    m("div.kv-label", "最近调整"),
                    m("span", `${g.updated_by} · ${fmtTime(g.updated_at)}`),
                  ])
                : null,
            ]),
            isWriter
              ? m("div.row", { style: { marginTop: "0.75rem" } }, [
                  gateThresholdInput(),
                  m(
                    "button",
                    {
                      type: "button",
                      disabled: state.gateSaving,
                      onclick: () => {
                        const t = parseFloat(state.gateForm.threshold_ms);
                        if (!Number.isFinite(t) || t <= 0 || t > 100) {
                          state.gateError = "风速阈值需在 0～100 m/s 之间";
                          m.redraw();
                          return;
                        }
                        saveGate(g.enabled, t);
                      },
                    },
                    "保存阈值"
                  ),
                  m(
                    "button",
                    {
                      type: "button",
                      class: g.enabled ? "secondary" : "",
                      disabled: state.gateSaving,
                      onclick: () => {
                        const t = parseFloat(state.gateForm.threshold_ms);
                        saveGate(
                          !g.enabled,
                          Number.isFinite(t) && t > 0 && t <= 100
                            ? t
                            : g.threshold_ms
                        );
                      },
                    },
                    g.enabled ? "关闭联闸" : "打开联闸"
                  ),
                ])
              : m(
                  "p.sub",
                  { style: { marginTop: "0.75rem", marginBottom: 0 } },
                  "复核员只读：可查看阈值与联闸流水，无法调整开关。"
                ),
            state.gateError ? m("p.err", state.gateError) : null,
            state.gateMsg ? m("p.ok", state.gateMsg) : null,
          ],
    ]),
    m("div.card", { key: "gate-events" }, [
      m("h2", { style: { marginTop: 0, fontSize: "1.1rem" } }, "联闸流水"),
      m("table", [
        m("thead", [
          m("tr", [
            m("th", "时间"),
            m("th", "事件"),
            m("th", "风速(m/s)"),
            m("th", "阈值(m/s)"),
            m("th", "操作人"),
            m("th", "说明"),
          ]),
        ]),
        m(
          "tbody",
          state.gateEvents.length
            ? state.gateEvents.map((ev) =>
                m("tr", { key: ev.id }, [
                  m("td", fmtTime(ev.created_at)),
                  m("td", [
                    m(
                      "span",
                      { class: eventTagClass(ev.event_type) },
                      EVENT_LABELS[ev.event_type] || ev.event_type
                    ),
                  ]),
                  m("td", ev.wind_speed ?? "—"),
                  m("td", ev.threshold_ms ?? "—"),
                  m("td", ev.actor),
                  m("td", ev.detail || "—"),
                ])
              )
            : [m("tr", m("td", { colspan: 6 }, "暂无流水"))]
        ),
      ]),
    ]),
  ];
}

const App = {
  oninit() {
    loadReadings();
    loadGate();
    startPolling();
  },
  onremove() {
    if (state.timer) clearInterval(state.timer);
  },
  view() {
    if (!state.token) {
      return m(
        "div.wrap",
        [
          m("h1", "桥梁应变班交台"),
          m(
            "p.sub",
            "测量员提交跨段编号与微应变读数，后台工人认领队列后判定合格或越界。"
          ),
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
                    await loadReadings();
                    await loadGate();
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
                  m("label", [
                    "用户名",
                    m("input", {
                      value: state.loginForm.username,
                      oninput: (e) => {
                        state.loginForm.username = e.target.value;
                      },
                    }),
                  ]),
                  m("label", [
                    "密码",
                    m("input", {
                      type: "password",
                      value: state.loginForm.password,
                      oninput: (e) => {
                        state.loginForm.password = e.target.value;
                      },
                    }),
                  ]),
                  m(
                    "button",
                    { type: "submit", disabled: state.loading },
                    "登录"
                  ),
                ]),
                state.error ? m("p.err", state.error) : null,
              ]
            ),
            m(
              "p.sub",
              { style: { marginBottom: 0 } },
              "测量员 surveyor / surv123456 · 复核员 reviewer / rev123456"
            ),
          ]),
        ]
      );
    }

    const isWriter = state.user?.role === "writer";

    return m("div.wrap", [
      m("div.topbar", [
        m("div", [
          m("h1", "桥梁应变班交台"),
          m("p.sub", "微应变 80～220 με 为合格，否则为越界。"),
          m("div.nav", [
            m(
              "button",
              {
                type: "button",
                class: state.view === "report" ? "nav-active" : "nav-btn",
                onclick: () => {
                  state.view = "report";
                  m.redraw();
                },
              },
              "读数报送"
            ),
            m(
              "button",
              {
                type: "button",
                class: state.view === "gate" ? "nav-active" : "nav-btn",
                onclick: async () => {
                  state.view = "gate";
                  await loadGate();
                  m.redraw();
                },
              },
              "风况联闸"
            ),
            state.gate
              ? m(
                  "span",
                  { class: state.gate.enabled ? "tag fail" : "tag pass" },
                  state.gate.enabled
                    ? `联闸开 · ${state.gate.wind_speed}/${state.gate.threshold_ms} m/s`
                    : "联闸关"
                )
              : null,
          ]),
        ]),
        m("div", [
          `${state.user?.username}（${isWriter ? "测量员" : "复核员"}） `,
          m(
            "button.secondary",
            {
              type: "button",
              onclick: () => {
                localStorage.removeItem(TOKEN_KEY);
                localStorage.removeItem(USER_KEY);
                state.token = "";
                state.user = null;
                state.rows = [];
                state.gate = null;
                state.gateEvents = [];
                state.view = "report";
                if (state.timer) clearInterval(state.timer);
                m.redraw();
              },
            },
            "退出"
          ),
        ]),
      ]),
      state.view === "report"
        ? [
            isWriter
              ? m("div.card", [
                  m(
                    "h2",
                    { style: { marginTop: 0, fontSize: "1.1rem" } },
                    "提交读数"
                  ),
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
                              microstrain: parseFloat(
                                state.submitForm.microstrain
                              ),
                            }),
                          });
                          state.msg = data.message || "已提交";
                          state.submitForm = { span_code: "", microstrain: "" };
                          await loadReadings();
                        } catch (err) {
                          state.error = err.message || "提交失败";
                          loadGate();
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
                            oninput: (e) => {
                              state.submitForm.span_code = e.target.value;
                            },
                          }),
                        ]),
                        m("label", [
                          "微应变（με）",
                          m("input", {
                            required: true,
                            type: "number",
                            step: "0.1",
                            value: state.submitForm.microstrain,
                            oninput: (e) => {
                              state.submitForm.microstrain = e.target.value;
                            },
                          }),
                        ]),
                        m(
                          "button",
                          { type: "submit", disabled: state.loading },
                          "提交"
                        ),
                      ]),
                      state.error ? m("p.err", state.error) : null,
                      state.msg ? m("p.ok", state.msg) : null,
                    ]
                  ),
                ])
              : null,
            m("div.card", [
              m(
                "h2",
                { style: { marginTop: 0, fontSize: "1.1rem" } },
                "读数列表"
              ),
              m("table", [
                m("thead", [
                  m("tr", [
                    m("th", "编号"),
                    m("th", "跨段"),
                    m("th", "微应变"),
                    m("th", "结论"),
                    m("th", "说明"),
                    m("th", "状态"),
                    m("th", "提交人"),
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
                          m("td", [
                            m(
                              "span",
                              { class: verdictClass(r.verdict, r.status) },
                              displayVerdict(r)
                            ),
                          ]),
                          m("td", r.reason || "—"),
                          m("td", r.status),
                          m("td", r.created_by),
                        ])
                      )
                    : [m("tr", m("td", { colspan: 7 }, "暂无数据"))]
                ),
              ]),
            ]),
          ]
        : gateView(isWriter),
    ]);
  },
};

export default App;
