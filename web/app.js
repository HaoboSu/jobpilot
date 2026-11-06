/* JobPilot front end - vanilla JS, no build step, no external dependencies. */
(() => {
  "use strict";

  const $ = (s, root = document) => root.querySelector(s);
  const $$ = (s, root = document) => Array.from(root.querySelectorAll(s));
  const esc = (v) => String(v == null ? "" : v)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");

  // External job data is untrusted; escaping HTML alone does not block script URLs.
  const safeJobURL = (value) => {
    try {
      const url = new URL(String(value || ""));
      return ["https:", "http:"].includes(url.protocol) && !url.username && !url.password
        ? url.href : "";
    } catch (_) { return ""; }
  };

  // ---------------------------------------------------------- i18n layer
  const I18N = window.JP_I18N || { en: {}, patterns: [], title: document.title };
  const CJK_RE = /[\u4e00-\u9fff]/;
  const normSpace = (s) => String(s || "").replace(/\s+/g, " ").trim();
  const EN_MAP = new Map(Object.entries(I18N.en).map(([k, v]) => [normSpace(k), v]));
  const EN_KEYS = [...EN_MAP.keys()].sort((a, b) => b.length - a.length);

  function translateText(raw) {
    if (!raw || !CJK_RE.test(raw)) return raw;
    const norm = normSpace(raw);
    for (const [re, fn] of (I18N.patterns || [])) {
      const m = norm.match(re);
      if (m) return fn(...m);
    }
    if (EN_MAP.has(norm)) return EN_MAP.get(norm);
    let out = raw;
    for (const k of EN_KEYS) {
      if (k.length < 2 || !out.includes(k)) continue;
      out = out.split(k).join(EN_MAP.get(k));
    }
    for (const [re, fn] of (I18N.subPatterns || [])) {
      out = out.replace(re, fn);
    }
    return out;
  }

  const SKIP_TAGS = new Set(["SCRIPT", "STYLE", "TEXTAREA", "PRE", "CODE"]);
  function translateDOM(root) {
    if (S.lang !== "en" || !root) return;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode(node) {
        let p = node.parentElement;
        while (p) {
          if (SKIP_TAGS.has(p.tagName) || p.hasAttribute("data-no-i18n")) {
            return NodeFilter.FILTER_REJECT;
          }
          p = p.parentElement;
        }
        return CJK_RE.test(node.nodeValue || "")
          ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
      },
    });
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    for (const n of nodes) {
      const next = translateText(n.nodeValue);
      if (next !== n.nodeValue) n.nodeValue = next;
    }
    const els = root.querySelectorAll
      ? root.querySelectorAll("[placeholder],[title],[alt]") : [];
    els.forEach((el) => {
      if (el.hasAttribute("data-no-i18n")) return;
      for (const name of ["placeholder", "title", "alt"]) {
        const v = el.getAttribute(name);
        if (v && CJK_RE.test(v)) el.setAttribute(name, translateText(v));
      }
    });
  }

  function initI18n() {
    // ?lang=en / ?lang=zh is also accepted, which makes a language-specific
    // link shareable; it simply seeds the saved preference.
    const urlLang = new URLSearchParams(location.search).get("lang");
    if (urlLang === "en" || urlLang === "zh") {
      localStorage.setItem("jobpilot.lang", urlLang);
    }
    S.lang = localStorage.getItem("jobpilot.lang") === "en" ? "en" : "zh";
    document.documentElement.lang = S.lang === "en" ? "en" : "zh-CN";
    const btn = $("#btn-lang");
    if (btn) {
      btn.textContent = S.lang === "en" ? "中文" : "English";
      btn.title = S.lang === "en" ? "Switch to Chinese" : "Switch to English";
      btn.addEventListener("click", () => {
        const next = S.lang === "en" ? "zh" : "en";
        localStorage.setItem("jobpilot.lang", next);
        history.replaceState(null, "", location.pathname + location.hash);
        location.reload();
      });
    }
    if (S.lang !== "en") return;
    document.title = I18N.title;
    translateDOM(document.body);
    let running = false;
    const observer = new MutationObserver(() => {
      if (running) return;
      running = true;
      try { translateDOM(document.body); } finally { running = false; }
    });
    observer.observe(document.body, { childList: true, subtree: true, characterData: true });
  }

  const PAGE_META = {
    dashboard: ["总览", "简历驱动的职位监控、匹配评估与材料定制"],
    resume:    ["简历与版本", "导入、更新简历；每一次原始版本都永久留存"],
    monitor:   ["监控配置", "设置监控条件、数据源与运行方式"],
    jobs:      ["职位推荐", "按匹配度排序，逐项查看评分依据"],
    docs:      ["定制文档", "定制简历与 Cover Letter，含原文对照与事实校验"],
    logs:      ["运行日志", "每一次监控的抓取与评估记录"],
  };

  const engineLabel = (e) => ({
    rule: "规则引擎", llm: "大模型（校验通过）", workbuddy: "WorkBuddy 精写",
  }[e] || e);
  const langLabel = (l) => ({ en: "英文", zh: "中文" }[l] || "");

  const SMTP_PRESETS = {
    qq: { host: "smtp.qq.com", port: 465, tls: false,
          hint: "QQ 邮箱：网页版 → 设置 → 账户 → 开启 POP3/IMAP/SMTP 服务 → 生成 16 位「授权码」（不是 QQ 登录密码）。若之前保存过其他邮箱的密码，请先点「清空已存凭据」再重新填写。" },
    "163": { host: "smtp.163.com", port: 465, tls: false,
             hint: "163 / 126 邮箱：设置 → POP3/SMTP/IMAP → 开启服务并生成「授权码」。" },
    gmail: { host: "smtp.gmail.com", port: 587, tls: true,
             hint: "Gmail：先开启两步验证，再到 myaccount.google.com → 安全性 → 应用密码，生成 16 位专用密码。" },
    outlook: { host: "smtp.office365.com", port: 587, tls: true,
               hint: "个人 Outlook 已禁用密码式 SMTP，此通道通常不可用，建议用「预览并发送」零配置方式。" },
  };
  const providerHint = (p, user) => {
    let key = p;
    if (key === "auto") {
      const domain = String(user || "").split("@").pop().toLowerCase();
      key = ({ "qq.com": "qq", "foxmail.com": "qq", "163.com": "163",
               "126.com": "163", "gmail.com": "gmail", "outlook.com": "outlook",
               "hotmail.com": "outlook" })[domain] || "";
    }
    if (key === "custom") return "自定义：请手动填写 SMTP 主机、端口与加密方式。";
    if (SMTP_PRESETS[key]) return SMTP_PRESETS[key].hint;
    return "自动识别：按发件账号的邮箱域名自动选择 SMTP 主机与端口，切换邮箱时无需手动改主机。";
  };

  const S = {
    page: "dashboard", lang: "zh", state: null, resume: null, versions: [], recs: [],
    docs: [], runs: [], jobs: [],
    filters: { min_score: 0, status: "all", latest: "1", region: "preferred" },
  };

  // ------------------------------------------------------------------ utils
  async function api(path, method = "GET", body) {
    const opt = { method, headers: { "Content-Type": "application/json" } };
    if (body !== undefined) opt.body = JSON.stringify(body);
    const res = await fetch(path, opt);
    let json;
    try { json = await res.json(); }
    catch (_) { throw new Error("服务返回了无法解析的内容"); }
    if (!json.ok) throw new Error(json.error || "请求失败");
    return json.data;
  }

  function toast(msg, isErr = false) {
    const el = document.createElement("div");
    el.className = "toast-item" + (isErr ? " err" : "");
    el.textContent = msg;
    $("#toast").appendChild(el);
    setTimeout(() => el.remove(), 4200);
  }

  async function busy(btn, fn) {
    if (!btn) return fn();
    const old = btn.textContent;
    btn.disabled = true; btn.textContent = "处理中…";
    try { return await fn(); }
    finally { btn.disabled = false; btn.textContent = old; }
  }

  const fmt = (v) => (v ? String(v).replace("T", " ").slice(0, 19) : "—");
  const scoreColor = (s) => s >= 75 ? "#0f8f83" : s >= 55 ? "#1d4ed8" : s >= 35 ? "#d97706" : "#94a3b8";
  const statusChip = (st) => ({
    new: ["chip-blue", "待查看"], viewed: ["chip-muted", "已查看"],
    applied: ["chip-teal", "已投递"], dismissed: ["chip-red", "已忽略"],
  }[st] || ["chip-muted", st]);
  const sourceChip = (src) => ({
    linkedin: ["chip-blue", "领英真实抓取"], manual: ["chip-teal", "手动导入"],
    demo: ["chip-amber", "演示数据"],
  }[src] || ["chip-muted", src]);

  function showModal({ title, sub = "", body, foot = "", wide = false }) {
    $("#modal-root").innerHTML = `
      <div class="modal-mask">
        <div class="modal ${wide ? "wide" : ""}">
          <div class="modal-head">
            <div><h2>${title}</h2>${sub ? `<div class="sub">${sub}</div>` : ""}</div>
            <button class="modal-close" data-action="close-modal" title="关闭">×</button>
          </div>
          <div class="modal-body">${body}</div>
          ${foot ? `<div class="modal-foot">${foot}</div>` : ""}
        </div>
      </div>`;
  }
  const closeModal = () => { $("#modal-root").innerHTML = ""; };

  function ring(score) {
    const c = scoreColor(score);
    return `<div class="score-block">
      <div class="score-ring" style="background:conic-gradient(${c} ${Math.max(0, score)}%, #eef2f7 0)">
        <span class="score-num" style="color:${c}">${Number(score).toFixed(0)}<span class="score-unit">分</span></span>
      </div>
      <div class="score-caption">匹配度</div>
    </div>`;
  }

  function empty(icon, title, desc) {
    return `<div class="empty"><div class="empty-ico">${icon}</div><p><strong>${title}</strong></p><p>${desc}</p></div>`;
  }

  // ------------------------------------------------------------------- state
  async function loadState() {
    S.state = await api("/api/state");
    const r = S.state.resume;
    const chip = $("#resume-chip");
    chip.className = "chip " + (r ? (r.is_sample ? "chip-amber" : "chip-teal") : "chip-muted");
    chip.textContent = r
      ? (r.is_sample ? `示例简历 v${r.id}（演示数据）` : `当前简历 v${r.id} · ${r.name || "未填写姓名"}`)
      : "未导入简历";
    const m = S.state.monitor;
    const mc = $("#monitor-chip");
    mc.className = "chip " + (m.running ? "chip-on" : "chip-off");
    mc.textContent = m.running ? "自动监控运行中" : "自动监控未启动";
  }

  async function navigate(page) {
    if (!PAGE_META[page]) page = "dashboard";
    S.page = page;
    if (location.hash !== "#" + page) history.replaceState(null, "", "#" + page);
    $$("#nav .nav-item").forEach(b => b.classList.toggle("active", b.dataset.page === page));
    $("#page-title").textContent = PAGE_META[page][0];
    $("#page-subtitle").textContent = PAGE_META[page][1];
    $("#content").innerHTML = `<div class="loading">正在加载…</div>`;
    try {
      await loadState();
      const render = {
        dashboard: renderDashboard, resume: renderResume, monitor: renderMonitor,
        jobs: renderJobs, docs: renderDocs, logs: renderLogs,
      }[page];
      await render();
    } catch (e) {
      $("#content").innerHTML = `<div class="card"><div class="banner banner-warn">
        <span class="banner-ico">!</span><div>页面加载失败：${esc(e.message)}</div></div></div>`;
    }
  }

  // --------------------------------------------------------------- dashboard
  async function renderDashboard() {
    const st = S.state;
    const o = st.overview;
    const top = st.top_recommendations || [];
    const runs = st.recent_runs || [];
    const r = st.resume;
    const banner = !r
      ? `<div class="banner banner-info"><span class="banner-ico">i</span><div>
           <strong>先导入你的简历。</strong>应用会以简历中的真实技能与经历为依据监控职位、评估匹配度并定制材料。
           现在可以先载入示例简历体验完整流程（演示数据不会用于你的真实投递）。</div></div>`
      : r.is_sample
        ? `<div class="banner banner-warn"><span class="banner-ico">!</span><div>
             <strong>当前使用的是内置示例简历（演示数据）。</strong>其中人物、公司、项目均为虚构演示内容，
             不代表你的真实经历。请到「简历与版本」导入自己的简历后再用于实际求职。</div></div>`
        : `<div class="banner banner-ok"><span class="banner-ico">✓</span><div>
             已加载 <strong>v${r.id} ${esc(r.name || "")}</strong> 的简历。所有改写只会引用该版本中已有的经历与技能，
             原始全文已不可变留存。</div></div>`;

    $("#content").innerHTML = `
      ${banner}
      <div class="grid grid-4 mb-14">
        ${[["简历版本", o.resume_versions, "每次导入/更新均留存原始版本", "stat-accent"],
           ["职位库", o.jobs, "抓取与导入的岗位记录"],
           ["推荐结果", o.recommendations, "当前简历版本的评估留存"],
           ["定制文档", o.documents, "简历 + Cover Letter"]]
          .map(([l, v, f, cls]) => `<div class="card stat ${cls || ""}">
            <div class="stat-label">${l}</div><div class="stat-value">${v}</div>
            <div class="stat-foot">${f}</div></div>`).join("")}
      </div>

      <div class="card">
        <div class="card-head"><div><h3 class="card-title">快速开始</h3>
          <p class="card-desc">四步完成从简历到可投递材料的全流程</p></div></div>
        <div class="steps">
          ${[["1", "导入简历", "上传 PDF / Word / 文本，或粘贴文字", "resume", "前往导入"],
             ["2", "配置监控", "设置关键词、地点、匹配阈值与频率", "monitor", "前往配置"],
             ["3", "运行监控", "抓取领英职位并自动评估匹配度", "monitor", "立即运行"],
             ["4", "查看材料", "查看定制简历、Cover Letter 与原文对照", "docs", "查看文档"]]
            .map(([n, t, d, p, btn]) => `<div class="step">
              <div class="step-n">${n}</div><div class="step-t">${t}</div>
              <div class="step-d">${d}</div>
              <button class="btn btn-sm" data-action="${p === "monitor" && n === "3" ? "monitor-run" : "goto"}"
                ${p === "monitor" && n === "3" ? "" : `data-page="${p}"`}>${btn}</button>
            </div>`).join("")}
        </div>
      </div>

      <div class="grid grid-2">
        <div class="card">
          <div class="card-head"><div><h3 class="card-title">高分推荐</h3>
            <p class="card-desc">达到当前阈值 ${st.settings.min_score} 分的岗位</p></div>
            <button class="btn btn-sm" data-action="goto" data-page="jobs">查看全部</button></div>
          ${top.length ? top.map(x => `
            <div style="display:flex;gap:12px;align-items:flex-start;padding:10px 0;border-bottom:1px solid var(--border)">
              <div style="font-size:19px;font-weight:750;color:${scoreColor(x.score)};min-width:46px">${x.score.toFixed(0)}</div>
              <div style="flex:1;min-width:0">
                <div style="font-weight:650;font-size:13px">${esc(x.title)}</div>
                <div class="small muted">${esc(x.company)} · ${esc(x.location || "地点未标明")}</div>
                <div class="tiny muted">命中 ${x.matched_skills.length} 项技能</div>
              </div>
              <button class="btn btn-sm" data-action="view-rec" data-id="${x.id}">详情</button>
            </div>`).join("")
            : empty("◎", "暂无推荐", "运行一次监控后，这里会显示匹配结果")}
        </div>
        <div class="card">
          <div class="card-head"><div><h3 class="card-title">最近监控</h3>
            <p class="card-desc">抓取来源、数量与降级说明都会如实记录</p></div>
            <button class="btn btn-sm" data-action="goto" data-page="logs">全部日志</button></div>
          ${runs.length ? `<div class="table-wrap"><table><thead><tr>
              <th>时间</th><th>触发</th><th>状态</th><th>新增职位</th></tr></thead><tbody>
            ${runs.map(x => `<tr><td class="small">${fmt(x.started_at)}</td>
              <td>${x.trigger === "auto" ? "自动" : "手动"}</td>
              <td><span class="chip ${x.status === "success" ? "chip-teal" : "chip-red"}">${esc(x.status)}</span></td>
              <td>${x.jobs_new}</td></tr>`).join("")}
            </tbody></table></div>` : empty("◷", "尚无运行记录", "点击「立即运行」开始第一次监控")}
        </div>
      </div>

      <div class="card">
        <div class="card-head"><div><h3 class="card-title">真实性承诺与数据来源</h3></div></div>
        <div class="grid grid-3 small" style="line-height:1.75">
          <div><strong>① 改写可核对</strong><div class="muted">规则引擎重排与引用已有条目；可选大模型输出通过数字和技能规则检查，失败时回退。检查不能验证所有事实，投递前请人工核对。</div></div>
          <div><strong>② 原文永久留存</strong><div class="muted">每次导入或编辑生成不可变版本；每份定制文档都保存生成时的原始简历全文，可随时对照与下载。</div></div>
          <div><strong>③ 数据源如实标注</strong><div class="muted">领英公开职位接口抓取失败时会明确记录原因；降级使用的演示职位带「演示数据」标签，不会伪装成真实招聘信息。</div></div>
        </div>
      </div>`;
  }

  // ------------------------------------------------------------------ resume
  async function renderResume() {
    const [data, versions, pdfStatus] = await Promise.all([
      api("/api/resume"), api("/api/resume/versions"), api("/api/pdf/status"),
    ]);
    S.resume = data; S.versions = versions;
    const v = data.version;
    const s = v ? v.structured : null;
    $("#content").innerHTML = `
      ${v && s._sample ? `<div class="banner banner-warn"><span class="banner-ico">!</span><div>
        这是<strong>内置示例简历（演示数据）</strong>，人物与公司均为虚构，仅供体验流程。请导入你自己的简历。</div></div>` : ""}
      <div class="card">
        <div class="card-head"><div><h3 class="card-title">当前简历版本</h3>
          <p class="card-desc">所有匹配评估与材料改写都以此版本中的真实内容为依据</p></div>
          <span class="chip ${v ? "chip-blue" : "chip-muted"}">${v ? "v" + v.id : "无版本"}</span></div>
        ${v ? `<div class="grid grid-4">
          <div><div class="stat-label">姓名</div><div style="font-weight:650">${esc(s.basics.name || "未填写")}</div></div>
          <div><div class="stat-label">定位</div><div>${esc(s.basics.headline || "未填写")}</div></div>
          <div><div class="stat-label">技能 / 经历</div><div>${s.skills.length} 项 / ${s.experiences.length} 段</div></div>
          <div><div class="stat-label">可核算工作年限</div><div style="font-weight:650">${data.years} 年</div></div>
        </div>
        <div class="row mt-14">
          <button class="btn btn-sm" data-action="view-version" data-id="${v.id}">查看原始全文</button>
          <a class="btn btn-sm" href="/api/versions/${v.id}/download">下载原始简历</a>
          <span class="chip chip-teal">原始版本不可修改</span>
        </div>
        <div class="mt-14"><div class="field-label">识别到的规范化技能（仅来自简历内容）</div>
          <div class="skill-tags">${data.canonical_skills.map(x => `<span class="tag tag-hit">${esc(x)}</span>`).join("") || '<span class="muted small">未识别到技能</span>'}</div>
        </div>`
        : empty("▤", "还没有简历", "上传文件或粘贴文字，或先载入示例简历体验流程")}
      </div>

      <div class="grid grid-2">
        <div class="card">
          <div class="card-head"><div><h3 class="card-title">导入 / 更新简历</h3>
            <p class="card-desc">支持 PDF、Word、纯文本与 Markdown，PDF 开箱即用</p></div></div>
          <div class="dropzone" id="dropzone" data-action="pick-file">
            <div class="dz-ico">⇧</div>
            <div class="dz-main">点击选择，或把 PDF / Word 简历拖到此处</div>
            <div class="dz-sub">.pdf · .docx · .txt · .md · .json ｜ 单文件 ≤ 8MB ｜ 仅在本机解析</div>
          </div>
          <input type="file" id="file-input" accept=".pdf,.docx,.txt,.md,.markdown,.json,.csv,.PDF,.DOCX" style="display:none">
          <div class="row mt-14" style="gap:8px">
            <span class="chip ${pdfStatus.enhanced.length ? "chip-teal" : "chip-blue"}">
              PDF 引擎：${pdfStatus.engine === "builtin" ? "内置解析（零依赖）" : esc(pdfStatus.engine)}</span>
            ${pdfStatus.enhanced.length ? "" : '<button class="btn btn-sm" data-action="install-pdf">安装 pypdf 增强兼容</button>'}
          </div>
          <div class="small muted mt-8">
            PDF 文字层可直接解析（含中文嵌入字体）；扫描图片型 PDF 无文字层，需另存为 .docx / .txt 或先做 OCR。
            ${esc(pdfStatus.message)}
          </div>
          <div class="row mt-14">
            <button class="btn" data-action="paste-resume">粘贴文字导入</button>
            <button class="btn" data-action="load-sample">载入示例简历</button>
          </div>
          <div class="small muted mt-14">导入后会自动解析为结构化字段；若解析不准，可在右侧编辑器中修正。每次导入都会生成新的不可变版本。</div>
        </div>
        <div class="card">
          <div class="card-head"><div><h3 class="card-title">结构化编辑器</h3>
            <p class="card-desc">修正解析结果；保存后生成新版本，旧版本保留</p></div>
            <button class="btn btn-primary btn-sm" data-action="save-structured" ${s ? "" : "disabled"}>保存为新版本</button></div>
          <textarea id="structured-editor" class="mono" style="min-height:330px">${s ? esc(JSON.stringify(s, null, 2)) : ""}</textarea>
          <div class="small muted mt-8">字段：basics / summary / skills / experiences / projects / education / certifications / languages / extras。
            请勿填入任何不真实的经历或技能——它们会被当作事实用于改写。</div>
        </div>
      </div>

      <div class="card">
        <div class="card-head"><div><h3 class="card-title">版本历史（原始版本永久留存）</h3>
          <p class="card-desc">共 ${versions.length} 个版本，任何版本都不会被覆盖或删除</p></div></div>
        ${versions.length ? `<div class="table-wrap"><table><thead><tr>
          <th>版本</th><th>名称</th><th>来源</th><th>格式</th><th>创建时间</th><th>原文长度</th><th>操作</th>
        </tr></thead><tbody>${versions.map(x => `<tr>
          <td><span class="chip chip-blue">v${x.id}</span></td>
          <td>${esc(x.label)}${x.source === "sample" ? ' <span class="chip chip-amber">演示数据</span>' : ""}</td>
          <td>${({ import: "文件/文字导入", edit: "结构化编辑", sample: "内置示例" })[x.source] || x.source}</td>
          <td>${esc(x.raw_format || "—")}</td><td class="small">${fmt(x.created_at)}</td><td>${x.raw_length} 字</td>
          <td><button class="btn btn-sm" data-action="view-version" data-id="${x.id}">查看原文</button>
              <a class="btn btn-sm" href="/api/versions/${x.id}/download">下载</a></td>
        </tr>`).join("")}</tbody></table></div>` : empty("≡", "暂无版本", "导入第一份简历后会出现在这里")}
      </div>`;

    const dz = $("#dropzone");
    dz.addEventListener("dragover", e => { e.preventDefault(); dz.classList.add("dragover"); });
    dz.addEventListener("dragleave", () => dz.classList.remove("dragover"));
    dz.addEventListener("drop", e => {
      e.preventDefault(); dz.classList.remove("dragover");
      if (e.dataTransfer.files[0]) handleFile(e.dataTransfer.files[0]);
    });
    $("#file-input").addEventListener("change", e => {
      if (e.target.files[0]) handleFile(e.target.files[0]);
    });
  }

  async function handleFile(file) {
    try {
      toast(`正在解析 ${file.name} …`);
      const buf = new Uint8Array(await file.arrayBuffer());
      let binary = "";
      for (let i = 0; i < buf.length; i += 0x8000)
        binary += String.fromCharCode.apply(null, buf.subarray(i, i + 0x8000));
      const b64 = btoa(binary);
      const data = await api("/api/resume/import", "POST", { filename: file.name, b64 });
      toast(`${file.name} 导入成功 · ${data.message}`);
      await navigate("resume");
    } catch (e) { importError(e.message); }
  }

  function importError(message) {
    showModal({
      title: "文件导入失败",
      sub: "原始简历未被修改，可按下面的建议重试",
      body: `<div class="banner banner-warn"><span class="banner-ico">!</span>
               <div>${esc(message)}</div></div>
             <div class="small muted mt-14" style="line-height:1.9">
               建议按顺序尝试：<br>
               ① 用 Word / WPS 打开 PDF 后「另存为」.docx 再上传；<br>
               ② 直接粘贴简历文字或结构化 JSON 导入；<br>
               ③ 若是扫描图片型 PDF，需先用 OCR 转成可复制文字；<br>
               ④ 点击「安装 pypdf 增强兼容」提升复杂排版 PDF 的解析成功率。
             </div>`,
      foot: `<button class="btn" data-action="close-modal">关闭</button>
             <button class="btn" data-action="install-pdf">安装 pypdf 增强兼容</button>
             <button class="btn btn-primary" data-action="paste-resume">改用粘贴文字导入</button>`,
    });
  }

  // ----------------------------------------------------------------- monitor
  async function renderMonitor() {
    const s = S.state.settings;
    const m = S.state.monitor;
    let detect = {};
    try { detect = await api("/api/network/detect"); }
    catch (_) { detect = { note: "诊断接口暂不可用" }; }
    let notifyLog = [];
    try { notifyLog = await api("/api/notify/log"); } catch (_) { notifyLog = []; }
    const activeDisplay = (s.linkedin_proxy || "").trim() || detect.recommended || "直连";
    let mailBanner;
    if (s.smtp_last_ok === 1) {
      mailBanner = `<div class="banner banner-ok"><span class="banner-ico">✓</span>
        <div>SMTP 通道已验证，达到阈值后自动发送到 ${esc(s.notify_email)}。</div></div>`;
    } else if (s.smtp_last_message) {
      mailBanner = `<div class="banner banner-warn"><span class="banner-ico">!</span>
        <div>${esc(s.smtp_last_message)}</div></div>`;
    } else {
      mailBanner = `<div class="banner banner-warn"><span class="banner-ico">!</span>
        <div>个人 Outlook 已禁用密码式 SMTP，自动发信尚未启用；可用「预览并发送」零配置发出邮件，
        达标岗位同时留痕于通知日志。</div></div>`;
    }
    const listVal = (a) => (Array.isArray(a) ? a.join("、") : "");
    const sw = (key, title, desc) => `
      <div class="switch-row"><div><div class="switch-text">${title}</div>
        <div class="switch-desc">${desc}</div></div>
        <label class="switch"><input type="checkbox" data-setting="${key}" ${s[key] ? "checked" : ""}><span class="track"></span></label>
      </div>`;
    const inp = (key, label, hint, type = "text") => `
      <label class="field"><span class="field-label">${label}<span class="field-hint">${hint || ""}</span></span>
        <input type="${type}" data-setting="${key}" value="${esc(s[key])}"></label>`;
    const listInp = (key, label, hint) => `
      <label class="field"><span class="field-label">${label}<span class="field-hint">${hint || ""}</span></span>
        <input type="text" data-setting="${key}" data-type="list" value="${esc(listVal(s[key]))}"></label>`;

    $("#content").innerHTML = `
      <div class="banner banner-info"><span class="banner-ico">i</span><div>
        领英职位来自公开的 jobs guest 接口，无需登录。抓取由本地服务端发起，<strong>不会自动继承浏览器代理</strong>；
        应用会自动识别 Windows 系统代理（VPN 设置的那个），也可在下方手动指定。若仍返回 HTTP 451/403，运行日志会如实记录原因。
        勾选「演示数据降级」时才会载入带明确标签的演示职位，<strong>不会把演示数据伪装成真实招聘信息</strong>。
      </div></div>

      <div class="grid grid-2">
        <div class="card" id="settings-form">
          <div class="card-head"><div><h3 class="card-title">监控条件</h3>
            <p class="card-desc">职位检索与匹配过滤条件</p></div></div>
          <div class="grid grid-2">
            ${listInp("query_keywords", "检索关键词", "用「、」或逗号分隔，逐词检索领英")}
            ${inp("location", "目标地区", "可填中文，多个用逗号分隔：德国，法国，荷兰")}
            ${inp("interval_minutes", "监控间隔（分钟）", "最小 5 分钟", "number")}
            <label class="field"><span class="field-label">发布时间范围</span>
              <select data-setting="time_range">
                ${[["r86400", "过去 24 小时"], ["r604800", "过去 7 天"], ["r2592000", "过去 30 天"], ["", "不限"]]
                  .map(([v, l]) => `<option value="${v}" ${s.time_range === v ? "selected" : ""}>${l}</option>`).join("")}
              </select></label>
            ${inp("min_score", "推荐最低匹配分", "低于此分不生成材料", "number")}
            ${inp("max_results", "单轮最多抓取职位数", "5 - 60", "number")}
            ${listInp("required_skills", "必备技能", "未命中会在评分中扣分")}
            ${listInp("exclude_companies", "排除公司", "命中则直接过滤")}
            <label class="field"><span class="field-label">地区偏好模式</span>
              <select data-setting="region_mode">
                ${[["prefer", "优先（不符扣分）"], ["strict", "严格（直接过滤）"], ["off", "关闭地区偏好"]]
                  .map(([v, l]) => `<option value="${v}" ${s.region_mode === v ? "selected" : ""}>${l}</option>`).join("")}
              </select></label>
            <label class="field"><span class="field-label">材料书写语言</span>
              <select data-setting="letter_language">
                ${[["auto", "跟随岗位（推荐）"], ["en", "英文"], ["zh", "中文"]]
                  .map(([v, l]) => `<option value="${v}" ${s.letter_language === v ? "selected" : ""}>${l}</option>`).join("")}
              </select></label>
          </div>
          <div class="banner banner-info mt-14" style="margin-bottom:0"><span class="banner-ico">i</span><div>
            <strong>地点解析：</strong>${esc((s.region_queries || []).join(" / ") || "不限地点")}
            （${s.region_terms_count || 0} 个地区匹配词）。领英不识别中文地名，应用会把「德国」转成 Germany、
            「法国」转成 France；多个地点用逗号分隔会分别检索并合并结果。推荐列表默认只显示符合首选地区的职位。
          </div></div>
          <label class="field mt-14"><span class="field-label">领英访问代理（可选）
            <span class="field-hint">留空则自动采用 Windows 系统代理（VPN 设置的那个）；返回 451 时可手动指定</span></span>
            <input type="text" data-setting="linkedin_proxy" value="${esc(s.linkedin_proxy || "")}"
                   placeholder="例如 http://127.0.0.1:7890，留空则直连"></label>
          <div class="mt-14">
            ${sw("use_linkedin", "启用领英职位抓取", "通过公开接口按条件检索真实职位（可关闭以仅用手动导入）")}
            ${sw("remote_only", "仅远程岗位", "非远程岗位在评分中扣分")}
            ${sw("demo_fallback", "演示数据降级", "领英抓取为空时载入带标签的演示职位，便于体验完整流程")}
            ${sw("auto_generate_docs", "自动生成定制材料", "达标推荐自动生成定制简历与 Cover Letter（历史版本保留）")}
            ${sw("auto_monitor", "服务启动时自动开始监控", "保存后于下次启动应用时生效")}
          </div>
          <div class="row-end mt-14"><button class="btn btn-primary" data-action="save-settings">保存监控配置</button></div>
        </div>

        <div>
          <div class="card">
            <div class="card-head"><div><h3 class="card-title">运行控制</h3>
              <p class="card-desc">当前状态：${m.running ? '<span class="chip chip-on">自动监控运行中</span>' : '<span class="chip chip-off">未启动</span>'}
                ${m.next_run_at ? ` · 下次运行 ${fmt(m.next_run_at)}` : ""}</p></div></div>
            <div class="row">
              <button class="btn btn-primary" data-action="monitor-run">立即运行监控</button>
              <button class="btn" data-action="monitor-demo">载入演示职位</button>
              <button class="btn ${m.running ? "btn-danger" : "btn-teal"}" data-action="monitor-toggle">${m.running ? "停止自动监控" : "启动自动监控"}</button>
            </div>
            <div class="row mt-14">
              <button class="btn btn-sm" data-action="import-jd">手动导入职位描述</button>
              <button class="btn btn-sm" data-action="goto" data-page="logs">查看运行日志</button>
            </div>
            <div class="small muted mt-14">「立即运行」会抓取职位 → 对全部职位重新评估 → 留存推荐结果 → 为达标职位自动生成材料。
              手动导入支持粘贴 JD 原文，或粘贴 JSON 数组（含 title / company / description 字段）。</div>
          </div>

          <div class="card">
            <div class="card-head"><div><h3 class="card-title">领英网络诊断</h3>
              <p class="card-desc">抓取由本地服务端发起，不会自动继承浏览器／VPN 的代理设置</p></div>
              <span class="chip ${detect.registry_proxy ? "chip-teal" : "chip-amber"}">
                ${detect.registry_proxy ? "已检测到系统代理" : "未检测到系统代理"}</span></div>
            <dl class="kv small">
              <dt>Windows 系统代理</dt><dd>${esc(detect.registry_server || "未设置")}
                ${detect.registry_proxy ? `→ ${esc(detect.registry_proxy)}` : ""}</dd>
              <dt>环境变量代理</dt><dd>${esc(detect.env_proxy || "无")}</dd>
              <dt>应用实际通道</dt><dd><strong>${esc(activeDisplay)}</strong></dd>
            </dl>
            <div class="small muted mt-8">${esc(detect.note || "")}</div>
            <div class="row mt-14">
              <button class="btn btn-sm" data-action="network-diagnose">开始联网诊断</button>
              <button class="btn btn-sm btn-primary" data-action="apply-proxy"
                data-proxy="${esc(detect.recommended || "")}" ${detect.recommended ? "" : "disabled"}>
                一键应用系统代理</button>
            </div>
            <div id="network-result" class="mt-14"></div>
          </div>

          <div class="card">
            <div class="card-head"><div><h3 class="card-title">大模型改写（可选）</h3>
              <p class="card-desc">不配置也能用：默认规则引擎已可完成全部改写，且零虚构风险最低</p></div></div>
            ${sw("llm_enabled", "启用大模型润色", "启用后将简历与岗位内容发送给所配置的服务商；规则检查失败时回退，投递前仍需核对")}
            <div class="grid grid-2 mt-8">
              ${inp("llm_base_url", "接口地址", "OpenAI 兼容 /chat/completions")}
              ${inp("llm_model", "模型", "如 gpt-4o-mini")}
            </div>
            <label class="field mt-8"><span class="field-label">API Key<span class="field-hint">仅保存在本机数据库</span></span>
              <input type="password" data-setting="llm_api_key" value="${esc(s.llm_api_key || "")}" placeholder="留空则使用规则引擎"></label>
            <div class="row mt-14">
              <button class="btn btn-sm" data-action="test-llm">测试连接</button>
              <span class="small muted">${s.llm_key_configured ? "已配置密钥" : "未配置密钥"}</span>
            </div>
          </div>

          <div class="card">
            <div class="card-head"><div><h3 class="card-title">高分职位邮件通知</h3>
              <p class="card-desc">匹配度达到阈值时，自动把岗位汇总发送到你的邮箱</p></div>
              <span class="chip ${s.notify_enabled ? "chip-teal" : "chip-muted"}">
                ${s.notify_enabled ? "已启用" : "已关闭"}</span></div>
            ${mailBanner}
            <div class="grid grid-2">
              <label class="field"><span class="field-label">发件服务商
                <span class="field-hint">切换邮箱会自动带出主机/端口</span></span>
                <select data-setting="smtp_provider" id="smtp-provider">
                  ${[["auto", "自动识别（推荐）"], ["qq", "QQ 邮箱"], ["163", "163 / 126 邮箱"],
                     ["gmail", "Gmail"], ["outlook", "Outlook / Hotmail"], ["custom", "自定义 SMTP"]]
                    .map(([v, l]) => `<option value="${v}" ${s.smtp_provider === v ? "selected" : ""}>${l}</option>`).join("")}
                </select></label>
              ${inp("notify_email", "收件邮箱", "岗位汇总发到这个邮箱")}
              ${inp("notify_threshold", "匹配阈值（分）", "达到即发信，默认 80", "number")}
              ${inp("smtp_host", "SMTP 主机", "切换服务商后自动填写")}
              ${inp("smtp_port", "SMTP 端口", "QQ/163 用 465；Gmail 用 587", "number")}
              ${inp("smtp_user", "发件账号", "必须与授权码对应")}
              <label class="field"><span class="field-label">授权码 / 应用专用密码
                <span class="field-hint">仅存本机，不写日志</span></span>
                <input type="password" data-setting="smtp_password"
                       value="${esc(s.smtp_password || "")}" placeholder="QQ 邮箱填 16 位授权码"></label>
            </div>
            <div class="small muted mt-8" id="smtp-hint">
              ${esc(providerHint(s.smtp_provider || "auto", s.smtp_user))}</div>
            <div class="mt-8">
              ${sw("notify_enabled", "启用自动通知", "每轮监控后检查新增达标岗位，已通知过的不重复发送")}
              ${sw("smtp_use_tls", "使用 STARTTLS", "端口 587 时保持开启；465 用 SSL")}
            </div>
            <div class="small muted mt-8">
              微软自 2024-09 起对个人 @outlook.com 禁用第三方密码式 SMTP，应用专用密码通常也无效。
              <strong>最省事：点「预览并发送」→ 用邮件客户端打开，或下载 .eml 草稿双击打开发送，全程无需密码。</strong>
              若要无人值守自动发信，可改用 QQ / 163 / Gmail 的 SMTP 授权码作为发件通道，收件邮箱仍填你的 Outlook。
            </div>
            <div class="row mt-14">
              <button class="btn btn-sm btn-primary" data-action="save-settings">保存通知配置</button>
              <button class="btn btn-sm btn-teal" data-action="notify-preview">预览并发送</button>
              <a class="btn btn-sm" href="/api/notify/eml">下载 .eml 草稿</a>
              <button class="btn btn-sm" data-action="notify-test">测试 SMTP</button>
              <button class="btn btn-sm btn-danger" data-action="clear-smtp">清空已存凭据</button>
            </div>
            <div id="notify-log" class="mt-14"></div>
          </div>
        </div>
      </div>`;
    const logBox = $("#notify-log");
    if (logBox) {
      logBox.innerHTML = notifyLog.length
        ? `<div class="field-label">最近通知记录</div><div class="table-wrap"><table>
             <thead><tr><th>时间</th><th>岗位</th><th>分数</th><th>状态</th></tr></thead><tbody>
             ${notifyLog.slice(0, 6).map(n => `<tr>
               <td class="small">${fmt(n.sent_at)}</td><td>${esc(n.title || "—")}</td>
               <td>${Number(n.score).toFixed(1)}</td>
               <td><span class="chip ${n.status === "sent" ? "chip-teal" : "chip-red"}">${esc(n.status)}</span></td>
             </tr>`).join("")}
           </tbody></table></div>`
        : '<div class="small muted">暂无通知记录。达到阈值的岗位会在这里留痕。</div>';
    }
  }

  function renderNetworkResult(d) {
    const box = $("#network-result");
    if (!box) return;
    const row = (label, ch) => `<div class="card" style="box-shadow:none;margin:0;padding:12px">
      <div class="small"><strong>${label}</strong> · ${esc(ch.proxy)}</div>
      <div class="small muted">领英：${esc(ch.linkedin)}</div>
      <div class="small muted">出口 IP：${esc(ch.exit_ip)} ${esc(ch.exit_country)} ${esc(ch.exit_city)}</div>
    </div>`;
    box.innerHTML = `<div class="grid grid-2">
        ${row("直连", d.direct)}
        ${d.active_channel ? row("当前通道", d.active_channel) : ""}
      </div>
      <div class="banner ${d.ok ? "banner-ok" : "banner-warn"} mt-8">
        <span class="banner-ico">${d.ok ? "✓" : "!"}</span><div>${esc(d.advice)}</div></div>`;
  }

  function collectSettings() {
    const patch = {};
    $$("[data-setting]").forEach(el => {
      const k = el.dataset.setting;
      if (el.type === "checkbox") patch[k] = el.checked;
      else if (el.type === "number") patch[k] = Number(el.value);
      else if (el.dataset.type === "list")
        patch[k] = el.value.split(/[,，、]/).map(x => x.trim()).filter(Boolean);
      else patch[k] = el.value;
    });
    return patch;
  }

  // -------------------------------------------------------------------- jobs
  async function renderJobs() {
    const f = S.filters;
    S.recs = await api(
      `/api/recommendations?min_score=${f.min_score}&status=${f.status}`
      + `&latest=${f.latest}&region=${f.region}`
    );
    const threshold = S.state.settings.min_score;
    const above = S.recs.filter(x => x.score >= threshold).length;
    $("#content").innerHTML = `
      <div class="card">
        <div class="row">
          <label class="field" style="max-width:170px"><span class="field-label">最低匹配分</span>
            <input type="number" data-filter="min_score" value="${f.min_score}" min="0" max="100"></label>
          <label class="field" style="max-width:170px"><span class="field-label">处理状态</span>
            <select data-filter="status">
              ${[["all", "全部"], ["new", "待查看"], ["viewed", "已查看"], ["applied", "已投递"], ["dismissed", "已忽略"]]
                .map(([v, l]) => `<option value="${v}" ${f.status === v ? "selected" : ""}>${l}</option>`).join("")}
            </select></label>
          <label class="field" style="max-width:180px"><span class="field-label">简历版本范围</span>
            <select data-filter="latest">
              <option value="1" ${f.latest === "1" ? "selected" : ""}>仅当前版本</option>
              <option value="0" ${f.latest === "0" ? "selected" : ""}>全部历史留存</option>
            </select></label>
          <label class="field" style="max-width:170px"><span class="field-label">地区范围</span>
            <select data-filter="region">
              <option value="preferred" ${f.region === "preferred" ? "selected" : ""}>仅首选地区</option>
              <option value="all" ${f.region === "all" ? "selected" : ""}>全部地区</option>
            </select></label>
          <div class="spacer"></div>
          <button class="btn btn-sm" data-action="monitor-run">运行一次监控</button>
          <button class="btn btn-sm" data-action="refresh">刷新列表</button>
        </div>
      </div>
      <div class="banner banner-ok"><span class="banner-ico">✓</span><div>
        共 <strong>${S.recs.length}</strong> 条评估留存，其中 <strong>${above}</strong> 条达到当前推荐阈值 ${threshold} 分。
        评分维度与命中/缺失技能均可展开查看，结果不会因新一轮监控而删除（按简历版本分别留存）。</div></div>
      ${S.recs.length ? S.recs.map(x => {
        const [sc, sl] = sourceChip(x.job_source);
        const [tc, tl] = statusChip(x.status);
        return `<div class="job-card">
          ${ring(x.score)}
          <div class="job-body">
            <div class="row" style="gap:8px;margin-bottom:4px">
              <h3 class="job-title" style="margin:0">${esc(x.title)}</h3>
              <span class="chip ${sc}">${sl}</span><span class="chip ${tc}">${tl}</span>
              ${x.region_ok === false ? '<span class="chip chip-amber">地区不符</span>' : ""}
              ${x.doc_count ? `<span class="chip chip-teal">${x.doc_count} 份材料</span>` : ""}
            </div>
            <div class="job-meta">${esc(x.company)}<span class="sep">·</span>${esc(x.location || "地点未标明")}
              <span class="sep">·</span>发布 ${esc(x.posted_at || "未知")}
              <span class="sep">·</span>简历 v${x.resume_version_id}</div>
            <div class="skill-tags">
              ${x.matched_skills.slice(0, 10).map(k => `<span class="tag tag-hit">✓ ${esc(k)}</span>`).join("")}
              ${x.missing_skills.slice(0, 5).map(k => `<span class="tag tag-miss">${esc(k)}</span>`).join("")}
              ${!x.matched_skills.length && !x.missing_skills.length ? '<span class="small muted">未识别到技能关键词</span>' : ""}
            </div>
            ${x.reasons.length ? `<ul class="job-reasons" data-no-i18n>${x.reasons.slice(0, 2).map(r => `<li>${esc(r)}</li>`).join("")}</ul>` : ""}
            <div class="job-actions">
              <button class="btn btn-sm" data-action="view-rec" data-id="${x.id}">查看评分依据</button>
              <button class="btn btn-sm btn-primary" data-action="generate-docs" data-id="${x.id}">生成 / 重制材料</button>
              <select data-action="set-status" data-id="${x.id}" style="width:auto;padding:5px 9px;font-size:12px">
                ${[["new", "待查看"], ["viewed", "已查看"], ["applied", "已投递"], ["dismissed", "已忽略"]]
                  .map(([v, l]) => `<option value="${v}" ${x.status === v ? "selected" : ""}>${l}</option>`).join("")}
              </select>
              ${safeJobURL(x.url) ? `<a class="btn btn-sm" href="${esc(safeJobURL(x.url))}" target="_blank" rel="noopener noreferrer">打开职位原文</a>` : ""}
            </div>
          </div>
        </div>`;
      }).join("") : empty("◎", "没有匹配的推荐结果", "调整筛选条件，或先运行一次监控")}`;
  }

  async function viewRecommendation(id) {
    const d = await api(`/api/recommendations/${id}`);
    const r = d.recommendation;
    const b = r.breakdown || {};
    const rows = d.dimension_labels || {};
    const bars = Object.keys(rows).map(k => {
      const item = b[k] || { score: 0, max: 1, detail: "" };
      const pct = Math.min(100, (item.score / (item.max || 1)) * 100);
      return `<div class="bar-row">
        <div class="bar-label">${esc(rows[k])}</div>
        <div class="bar-track"><div class="bar-fill" style="width:${pct}%"></div></div>
        <div class="bar-value">${Number(item.score).toFixed(1)}/${item.max}</div>
        <div class="bar-detail">${esc(item.detail)}</div>
      </div>`;
    }).join("");
    const adj = (b.adjustments || []).map(a =>
      `<span class="chip ${a.delta >= 0 ? "chip-teal" : "chip-red"}">${esc(a.name)} ${a.delta > 0 ? "+" : ""}${a.delta} · ${esc(a.detail)}</span>`).join(" ");
    showModal({
      wide: true,
      title: `${esc(r.title)} · ${Number(r.score).toFixed(1)} 分`,
      sub: `${esc(r.company)} · ${esc(r.location || "地点未标明")} · 简历 v${r.resume_version_id}`,
      body: `
        <div class="card" style="box-shadow:none;margin-bottom:14px">
          <h3 class="card-title mb-8">评分分解（总分 ${Number(r.score).toFixed(1)} / 100）</h3>
          ${bars}
          ${adj ? `<div class="row mt-14">${adj}</div>` : ""}
        </div>
        <div class="grid grid-2">
          <div class="card" style="box-shadow:none;margin:0">
            <h3 class="card-title mb-8">命中技能</h3>
            <div class="skill-tags">${(r.matched_skills || []).map(x => `<span class="tag tag-hit">${esc(x)}</span>`).join("") || '<span class="muted small">无</span>'}</div>
            <h3 class="card-title mb-8 mt-14">岗位提到但简历未体现</h3>
            <div class="skill-tags">${(r.missing_skills || []).map(x => `<span class="tag tag-miss">${esc(x)}</span>`).join("") || '<span class="muted small">无</span>'}</div>
          </div>
          <div class="card" style="box-shadow:none;margin:0">
            <h3 class="card-title mb-8">推荐理由</h3>
            <ul class="job-reasons" data-no-i18n>${(r.reasons || []).map(x => `<li>${esc(x)}</li>`).join("") || "<li>—</li>"}</ul>
          </div>
        </div>
        <div class="card" style="box-shadow:none;margin-top:14px">
          <h3 class="card-title mb-8">原始职位描述</h3>
          <pre class="doc" style="max-height:220px">${esc(r.description || "（未提供 JD 正文）")}</pre>
        </div>
        <div class="card" style="box-shadow:none">
          <h3 class="card-title mb-8">已生成材料（${d.documents.length}）</h3>
          ${d.documents.length ? `<div class="table-wrap"><table><thead><tr><th>类型</th><th>引擎</th><th>生成时间</th><th>操作</th></tr></thead><tbody>
            ${d.documents.map(x => `<tr><td>${x.doc_type === "resume" ? "定制简历" : "Cover Letter"}</td>
              <td>${engineLabel(x.engine)}</td><td>${fmt(x.created_at)}</td>
              <td><button class="btn btn-sm" data-action="view-doc" data-id="${x.id}">查看</button></td></tr>`).join("")}
            </tbody></table></div>` : '<div class="small muted">尚未生成，可点击下方按钮生成。</div>'}
        </div>`,
      foot: `<button class="btn" data-action="close-modal">关闭</button>
             <button class="btn btn-primary" data-action="generate-docs" data-id="${id}">生成 / 重制材料</button>`,
    });
  }

  // -------------------------------------------------------------------- docs
  async function renderDocs() {
    S.docs = await api("/api/documents");
    $("#content").innerHTML = `
      <div class="banner banner-ok"><span class="banner-ico">✓</span><div>
        每份文档都绑定一份<strong>原始简历快照</strong>与一份<strong>事实校验报告</strong>。
        重新生成不会覆盖旧文档，而是追加新版本，因此改写前后的内容均可核对。
      </div></div>
      <div class="card">
        <div class="card-head"><div><h3 class="card-title">定制文档库（${S.docs.length}）</h3>
          <p class="card-desc">点击任一文档查看定制版、原文对照、校验报告与历史版本</p></div>
          <button class="btn btn-sm" data-action="refresh">刷新</button></div>
        ${S.docs.length ? `<div class="table-wrap"><table><thead><tr>
          <th>ID</th><th>职位 / 公司</th><th>类型</th><th>生成引擎</th><th>生成时间</th><th>操作</th>
        </tr></thead><tbody>${S.docs.map(x => `<tr>
          <td class="mono">#${x.id}</td>
          <td><strong>${esc(x.job_title || "职位")}</strong><div class="tiny muted">${esc(x.company || "")}</div></td>
          <td>${x.doc_type === "resume" ? '<span class="chip chip-blue">定制简历</span>' : '<span class="chip chip-teal">Cover Letter</span>'}</td>
          <td>${engineLabel(x.engine)}</td>
          <td class="small">${fmt(x.created_at)}</td>
          <td><button class="btn btn-sm" data-action="view-doc" data-id="${x.id}">查看 / 对照</button>
              <button class="btn btn-sm btn-danger" data-action="delete-doc" data-id="${x.id}">删除</button></td>
        </tr>`).join("")}</tbody></table></div>`
        : empty("✎", "还没有定制文档", "在「职位推荐」中生成，或开启自动生成的监控运行")}
      </div>`;
  }

  async function viewDocument(id) {
    const d = await api(`/api/documents/${id}`);
    const v = d.validation || {};
    const trace = v.trace || {};
    const original = await api(`/api/recommendations/${d.recommendation_id}`);
    const history = original.documents || [];
    const verdict = v.passed
      ? '<span class="chip chip-teal">✓ 规则检查通过 · 请人工复核</span>'
      : '<span class="chip chip-red">! 校验未通过（已回退）</span>';
    const ops = (trace.operations || []).map(x => `<li>${esc(x)}</li>`).join("");
    showModal({
      wide: true,
      title: esc(d.title || "定制文档"),
      sub: `文档 #${d.id} · ${engineLabel(d.engine)} ${langLabel(d.validation?.trace?.language)} · ${fmt(d.created_at)} · 简历原始版本 v${d.resume_version_id}`,
      body: `
        <div class="tabs">
          <button class="tab active" data-tab="tailored">定制版本</button>
          <button class="tab" data-tab="original">改写前原文对照</button>
          <button class="tab" data-tab="audit">事实校验报告</button>
          <button class="tab" data-tab="history">版本历史</button>
        </div>
        <div class="panel active" data-panel="tailored">
          <div class="row mb-8">${verdict}
            <span class="chip chip-blue">${engineLabel(d.engine)}</span>
            ${langLabel(d.validation?.trace?.language) ? `<span class="chip chip-muted">${langLabel(d.validation.trace.language)}</span>` : ""}
            <span class="chip chip-muted">${d.content.length} 字</span></div>
          <pre class="doc">${esc(d.content)}</pre>
        </div>
        <div class="panel" data-panel="original">
          <div class="banner banner-info"><span class="banner-ico">i</span><div>
            下方为生成该文档时所用简历版本的<strong>原始全文快照</strong>，独立存储、不随后续编辑改变。
            可逐条核对定制版中的每个经历、技能与数字。</div></div>
          <pre class="doc original">${esc(d.original_snapshot || "（无快照）")}</pre>
        </div>
        <div class="panel" data-panel="audit">
          <div class="card" style="box-shadow:none;margin-bottom:12px">
            <h3 class="card-title mb-8">校验结论 ${verdict}</h3>
            <dl class="kv">
              <dt>校验规则</dt><dd>${esc(v.rule || "—")}</dd>
              <dt>检查数字</dt><dd>${v.checked_numbers ?? 0} 处</dd>
              <dt>检查技能</dt><dd>${v.checked_skills ?? 0} 项</dd>
              <dt>违规项</dt><dd>${(v.violations || []).length ? esc(v.violations.join("；")) : "无"}</dd>
              <dt>回退原因</dt><dd>${esc(v.fallback_reason || v.llm_rejected || "无")}</dd>
              <dt>新增事实</dt><dd>${(trace.facts_added || []).length ? esc(trace.facts_added.join("、")) : "无（零新增）"}</dd>
            </dl>
          </div>
          <div class="card" style="box-shadow:none">
            <h3 class="card-title mb-8">改写操作留痕</h3>
            <ul class="job-reasons">${ops || "<li>规则引擎生成</li>"}</ul>
            ${trace.quoted_bullets && trace.quoted_bullets.length ? `
              <div class="field-label mt-14">逐字引用的原始要点</div>
              <ul class="job-reasons">${trace.quoted_bullets.map(x => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}
            ${trace.external_facts_used ? `<div class="small muted mt-8">允许使用的公开岗位信息：${esc(trace.external_facts_used.join("；"))}</div>` : ""}
          </div>
        </div>
        <div class="panel" data-panel="history">
          <div class="small muted mb-8">同一职位下的所有历史文档均保留，可点击切换查看。</div>
          <div class="table-wrap"><table><thead><tr><th>ID</th><th>类型</th><th>引擎</th><th>时间</th><th>操作</th></tr></thead><tbody>
            ${history.map(x => `<tr><td>#${x.id}${x.id === d.id ? ' <span class="chip chip-blue">当前</span>' : ""}</td>
              <td>${x.doc_type === "resume" ? "定制简历" : "Cover Letter"}</td>
              <td>${x.engine}</td><td>${fmt(x.created_at)}</td>
              <td><button class="btn btn-sm" data-action="view-doc" data-id="${x.id}">查看</button></td></tr>`).join("")}
          </tbody></table></div>
        </div>`,
      foot: `<button class="btn btn-danger" data-action="delete-doc" data-id="${d.id}">删除此文档</button>
             <button class="btn" data-action="close-modal">关闭</button>
             <a class="btn" href="/api/documents/${d.id}/download?format=md">下载 .md</a>
             <a class="btn" href="/api/documents/${d.id}/download?format=txt">下载 .txt</a>
             <a class="btn btn-primary" href="/api/documents/${d.id}/download?format=docx">下载 .docx</a>`,
    });
  }

  // -------------------------------------------------------------------- logs
  async function renderLogs() {
    S.runs = await api("/api/monitor/runs");
    $("#content").innerHTML = `
      <div class="card">
        <div class="card-head"><div><h3 class="card-title">监控运行日志（${S.runs.length}）</h3>
          <p class="card-desc">每轮抓取来源、数量、失败原因与降级情况都会完整记录</p></div>
          <button class="btn btn-sm" data-action="refresh">刷新</button></div>
        ${S.runs.length ? `<div class="table-wrap"><table><thead><tr>
          <th>#</th><th>开始时间</th><th>触发</th><th>数据源</th><th>状态</th>
          <th>抓取</th><th>新增职位</th><th>新增推荐</th><th>说明</th>
        </tr></thead><tbody>${S.runs.map(x => `<tr>
          <td class="mono">${x.id}</td><td class="small">${fmt(x.started_at)}</td>
          <td>${x.trigger === "auto" ? "自动调度" : "手动"}</td>
          <td>${esc(x.source || "—")}</td>
          <td><span class="chip ${x.status === "success" ? "chip-teal" : "chip-red"}">${esc(x.status)}</span></td>
          <td>${x.jobs_fetched}</td><td>${x.jobs_new}</td><td>${x.recs_new}</td>
          <td class="small" data-no-i18n style="max-width:420px;white-space:normal">${esc(x.message || "—")}</td>
        </tr>`).join("")}</tbody></table></div>`
        : empty("≡", "暂无运行记录", "在「监控配置」中点击立即运行")}
      </div>
      <div class="card"><h3 class="card-title mb-8">日志说明</h3>
        <div class="small muted" style="line-height:1.9">
          · 「领英搜索…取得 N 条」表示真实抓取成功；连接失败会写明具体错误类型。<br>
          · 出现「已降级载入演示职位数据」说明本轮没有取到真实职位，结果仅用于体验流程，请勿据此投递。<br>
          · 推荐结果按简历版本分别留存，重新运行会更新分数但不会删除历史评估。<br>
          · 服务重启后日志、职位库、推荐与文档都从本地 SQLite 数据库恢复。
        </div>
      </div>`;
  }

  // ----------------------------------------------------------------- actions
  async function runMonitor(kind, btn) {
    await loadState();
    if (!S.state.resume) { toast("请先导入或载入一份简历", true); return navigate("resume"); }
    await busy(btn, async () => {
      toast("监控运行中，正在抓取与评估职位…");
      const path = kind === "demo" ? "/api/monitor/demo" : "/api/monitor/run";
      const res = await api(path, "POST", {});
      if (!res.ok) { toast(res.message, true); return; }
      toast(`完成：抓取 ${res.jobs_fetched} 个职位，新增 ${res.jobs_new} 个，达标 ${res.above_threshold} 个，新生成 ${res.documents_new} 份材料`);
      if (res.notes && res.notes.some(n => n.includes("降级"))) {
        toast("本轮使用了演示职位数据（见运行日志）", true);
      }
      await navigate(S.page === "dashboard" ? "dashboard" : S.page);
    });
  }

  async function generateDocs(id, btn) {
    await busy(btn, async () => {
      const res = await api(`/api/recommendations/${id}/generate`, "POST", {});
      if (!res.ok) { toast(res.error || res.message, true); return; }
      toast(res.message);
      closeModal();
      await viewRecommendation(id);
    });
  }

  async function openPasteResume() {
    showModal({
      title: "粘贴简历文字导入",
      sub: "内容仅保存在本机，导入后可在结构化编辑器中修正",
      body: `<label class="field"><span class="field-label">简历全文</span>
        <textarea id="paste-text" style="min-height:340px" placeholder="姓名\n电话 | 邮箱 | 上海\n\n个人简介\n……\n\n核心技能\nPython、SQL、Tableau\n\n工作经历\n公司名称 | 数据分析师 | 2021-03 - 至今\n- 负责……"></textarea></label>
        <div class="small muted mt-8">也可以直接粘贴结构化 JSON（含 skills / experiences / basics 字段），解析最准确。</div>`,
      foot: `<button class="btn" data-action="close-modal">取消</button>
             <button class="btn btn-primary" data-action="do-paste-resume">导入并生成版本</button>`,
    });
  }

  async function openImportJD() {
    showModal({
      title: "手动导入职位描述",
      sub: "粘贴 JD 原文，或包含 title / company / description 的 JSON 数组",
      body: `<label class="field"><span class="field-label">职位内容</span>
        <textarea id="jd-text" style="min-height:320px" placeholder='[{"title":"数据分析师","company":"某公司","location":"上海","description":"岗位职责……任职要求……"}]'></textarea></label>
        <div class="small muted mt-8">导入后会与当前职位库一起重新评估，并自动为达标职位生成材料。</div>`,
      foot: `<button class="btn" data-action="close-modal">取消</button>
             <button class="btn btn-primary" data-action="do-import-jd">导入并评估</button>`,
    });
  }

  async function openVersion(id) {
    const v = await api(`/api/resume/versions/${id}`);
    showModal({
      title: `原始简历 v${v.id} · ${esc(v.label)}`,
      sub: `来源：${esc(v.source)} · 创建于 ${fmt(v.created_at)} · 此版本不可修改`,
      body: `<div class="banner banner-ok"><span class="banner-ico">✓</span><div>
          ${esc(v.note || "导入时的原始全文")}</div></div>
        <pre class="doc original">${esc(v.raw_text)}</pre>`,
      foot: `<button class="btn" data-action="close-modal">关闭</button>
             <a class="btn btn-primary" href="/api/versions/${v.id}/download">下载原始全文</a>`,
    });
  }

  async function handleAction(el) {
    const a = el.dataset.action;
    const id = el.dataset.id;
    switch (a) {
      case "goto": return navigate(el.dataset.page);
      case "refresh": return navigate(S.page);
      case "close-modal": return closeModal();
      case "pick-file": return $("#file-input") && $("#file-input").click();
      case "load-sample":
        return busy(el, async () => {
          const d = await api("/api/resume/sample", "POST", {});
          toast(d.message); await navigate("resume");
        });
      case "paste-resume": return openPasteResume();
      case "install-pdf":
        return busy(el, async () => {
          toast("正在安装 pypdf 增强解析，请稍候…");
          const d = await api("/api/pdf/install", "POST", {});
          toast(d.message, !d.ok);
          closeModal();
          await navigate("resume");
        });
      case "do-paste-resume": {
        const text = $("#paste-text").value.trim();
        if (!text) return toast("内容为空", true);
        return busy(el, async () => {
          const d = await api("/api/resume/import", "POST", { filename: "粘贴简历.txt", text });
          toast(d.message); closeModal(); await navigate("resume");
        });
      }
      case "save-structured": {
        let structured;
        try { structured = JSON.parse($("#structured-editor").value); }
        catch (e) { return toast("JSON 格式错误：" + e.message, true); }
        return busy(el, async () => {
          const d = await api("/api/resume/update", "POST", { structured, label: "结构化编辑保存" });
          toast(d.message); await navigate("resume");
        });
      }
      case "view-version": return openVersion(id);
      case "save-settings":
        return busy(el, async () => {
          await api("/api/settings", "POST", collectSettings());
          toast("监控配置已保存"); await navigate("monitor");
        });
      case "test-llm": {
        const patch = collectSettings();
        return busy(el, async () => {
          const d = await api("/api/llm/test", "POST", patch);
          toast(d.message, !d.ok);
        });
      }
      case "notify-test":
        return busy(el, async () => {
          const d = await api("/api/notify/test", "POST", collectSettings());
          toast(d.ok ? "测试邮件已发送，请查收" : "测试失败，详细原因见上方提示", !d.ok);
          await navigate("monitor");
        });
      case "clear-smtp":
        return busy(el, async () => {
          await api("/api/notify/clear-credentials", "POST", {});
          toast("已清空本机保存的 SMTP 凭据");
          await navigate("monitor");
        });
      case "notify-preview":
        return busy(el, async () => {
          const d = await api("/api/notify/preview");
          S.notifyDraft = d;
          const to = S.state.settings.notify_email || "you@example.com";
          const mailto = (
            "mailto:" + encodeURIComponent(to)
            + "?subject=" + encodeURIComponent(d.subject)
            + "&body=" + encodeURIComponent(d.body)
          ).replace(/&/g, "&amp;");
          showModal({
            title: "通知邮件预览",
            sub: d.note,
            body: `<div class="banner banner-info"><span class="banner-ico">i</span><div>
                     点「用邮件客户端打开」会在电脑上打开已登录的邮件程序，收件人与正文已填好，点发送即可。
                     <strong>手机 Outlook 只负责收件，不需要任何设置。</strong></div></div>
                   <label class="field mb-14"><span class="field-label">邮件主题</span>
                     <input type="text" readonly value="${esc(d.subject)}"></label>
                   <div class="field-label">邮件正文</div>
                   <pre class="doc" style="max-height:42vh">${esc(d.body)}</pre>`,
            foot: `<button class="btn" data-action="close-modal">关闭</button>
                   <button class="btn" data-action="copy-notify">复制内容</button>
                   <a class="btn" href="/api/notify/eml">下载 .eml 草稿</a>
                   <a class="btn btn-primary" href="${mailto}">用邮件客户端打开</a>`,
          });
        });
      case "copy-notify":
        try {
          await navigator.clipboard.writeText(
            `Subject: ${S.notifyDraft.subject}\n\n${S.notifyDraft.body}`
          );
          toast("邮件内容已复制到剪贴板");
        } catch (_) { toast("复制失败，请手动选中文本内容", true); }
        return;
      case "monitor-run": return runMonitor("real", el);
      case "monitor-demo": return runMonitor("demo", el);
      case "monitor-toggle": {
        const running = S.state.monitor.running;
        return busy(el, async () => {
          const d = await api(running ? "/api/monitor/stop" : "/api/monitor/start", "POST", {});
          toast(d.message); await loadState(); await renderMonitor();
        });
      }
      case "network-diagnose":
        return busy(el, async () => {
          $("#network-result").innerHTML =
            '<div class="small muted">正在实测直连与代理通道，请稍候…</div>';
          const d = await api("/api/network/diagnose");
          renderNetworkResult(d);
        });
      case "apply-proxy": {
        const proxy = el.dataset.proxy || "";
        return busy(el, async () => {
          const d = await api("/api/network/apply-proxy", "POST", { proxy });
          toast(d.message);
          await navigate("monitor");
        });
      }
      case "import-jd": return openImportJD();
      case "do-import-jd": {
        const text = $("#jd-text").value.trim();
        if (!text) return toast("内容为空", true);
        return busy(el, async () => {
          const d = await api("/api/monitor/import", "POST", { text });
          if (!d.ok) return toast(d.message || d.error, true);
          toast(`已导入 ${d.jobs_fetched} 个职位，达标 ${d.above_threshold} 个，新生成 ${d.documents_new} 份材料`);
          closeModal(); await navigate("jobs");
        });
      }
      case "view-rec": return viewRecommendation(id);
      case "generate-docs": return generateDocs(id, el);
      case "view-doc": return viewDocument(id);
      case "delete-doc":
        return busy(el, async () => {
          const doc = await api(`/api/documents/${id}`);
          showModal({
            title: "确认删除这份文档？",
            sub: "此操作不可撤销",
            body: `<div class="banner banner-warn"><span class="banner-ico">!</span>
                     <div>即将删除：<strong>${esc(doc.title)}</strong>（文档 #${doc.id}）。</div></div>
                   <dl class="kv mt-14">
                     <dt>类型</dt><dd>${doc.doc_type === "resume" ? "定制简历" : "Cover Letter"}</dd>
                     <dt>生成引擎</dt><dd>${engineLabel(doc.engine)}</dd>
                     <dt>生成时间</dt><dd>${fmt(doc.created_at)}</dd>
                     <dt>关联职位</dt><dd>#${doc.job_id}</dd>
                   </dl>
                   <div class="small muted mt-14" style="line-height:1.8">
                     · 只删除这一份定制文档，<strong>原始简历版本 v${doc.resume_version_id} 永久保留</strong>，可在「简历与版本」查看下载。<br>
                     · 删除后该职位不会在下一轮监控里自动生成新文档；需要时到「职位推荐」点「生成 / 重制材料」。<br>
                     · 其他历史文档版本不受影响。
                   </div>`,
            foot: `<button class="btn" data-action="close-modal">取消</button>
                   <button class="btn btn-danger" data-action="confirm-delete-doc" data-id="${id}">确认删除</button>`,
          });
        });
      case "confirm-delete-doc":
        return busy(el, async () => {
          const d = await api(`/api/documents/${id}/delete`, "POST", {});
          toast(d.message);
          closeModal();
          await navigate("docs");
        });
    }
  }

  function bindGlobal() {
    document.addEventListener("click", async (e) => {
      if (e.target.classList.contains("modal-mask")) { closeModal(); return; }
      const tab = e.target.closest("[data-tab]");
      if (tab) {
        $$(".tab").forEach(t => t.classList.toggle("active", t === tab));
        $$(".panel").forEach(p => p.classList.toggle("active", p.dataset.panel === tab.dataset.tab));
        return;
      }
      const nav = e.target.closest("#nav .nav-item");
      if (nav) { navigate(nav.dataset.page); return; }
      const el = e.target.closest("[data-action]");
      if (el && !el.matches("select")) {
        e.preventDefault();
        try { await handleAction(el); }
        catch (err) { toast("操作失败：" + err.message, true); }
      }
    });
    document.addEventListener("change", async (e) => {
      const el = e.target;
      if (el.id === "file-input") return;
      if (el.dataset.setting === "smtp_provider") {
        const preset = SMTP_PRESETS[el.value];
        if (preset) {
          const hostEl = document.querySelector('[data-setting="smtp_host"]');
          const portEl = document.querySelector('[data-setting="smtp_port"]');
          const tlsEl = document.querySelector('[data-setting="smtp_use_tls"]');
          if (hostEl) hostEl.value = preset.host;
          if (portEl) portEl.value = preset.port;
          if (tlsEl) tlsEl.checked = preset.tls;
        }
        const hintEl = $("#smtp-hint");
        if (hintEl) hintEl.textContent = providerHint(el.value, S.state.settings.smtp_user);
        return;
      }
      if (el.dataset.filter) {
        S.filters[el.dataset.filter] = el.value;
        await renderJobs();
      } else if (el.dataset.action === "set-status") {
        try {
          await api(`/api/recommendations/${el.dataset.id}/status`, "POST", { status: el.value });
          toast("状态已更新"); await renderJobs();
        } catch (err) { toast(err.message, true); }
      }
    });
    document.addEventListener("keydown", e => { if (e.key === "Escape") closeModal(); });
    $("#btn-refresh").addEventListener("click", () => navigate(S.page));
    window.addEventListener("hashchange", () => {
      const target = location.hash.slice(1);
      if (target && target !== S.page) navigate(target);
    });
  }

  initI18n();
  bindGlobal();
  navigate(location.hash.slice(1) || "dashboard");
})();
