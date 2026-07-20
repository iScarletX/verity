// Verity local Web MVP frontend.
// Rules:
//   * No innerHTML. All user/model content is inserted via textContent
//     or DOM node APIs. This guarantees browser-side XSS safety even if
//     an upstream field somehow contained raw HTML.
//   * No inline event handlers. All wiring goes through addEventListener.
//   * No CDN, no imports. This file is served with `script-src 'self'`.

(function () {
  "use strict";

  var $ = function (id) { return document.getElementById(id); };
  var mk = function (tag, opts) {
    var el = document.createElement(tag);
    if (opts) {
      if (opts.className) el.className = opts.className;
      if (opts.text !== undefined) el.textContent = opts.text;
      if (opts.attrs) {
        Object.keys(opts.attrs).forEach(function (k) {
          el.setAttribute(k, opts.attrs[k]);
        });
      }
    }
    return el;
  };

  // ---------------- tabs ----------------
  document.querySelectorAll(".tabs button").forEach(function (b) {
    b.addEventListener("click", function () {
      var tab = b.getAttribute("data-tab");
      document.querySelectorAll(".tabs button").forEach(function (x) {
        x.classList.remove("active");
        x.setAttribute("aria-selected", "false");
      });
      b.classList.add("active");
      b.setAttribute("aria-selected", "true");
      $("tab-prompt").hidden = tab !== "prompt";
      $("tab-skill").hidden = tab !== "skill";
    });
  });

  // ---------------- prompt tab ----------------
  var promptText = $("prompt-text");
  var promptCount = $("prompt-count");
  promptText.addEventListener("input", function () {
    promptCount.textContent = promptText.value.length + " 字符";
  });
  $("prompt-submit").addEventListener("click", function () {
    submitPrompt();
  });

  function submitPrompt() {
    var text = promptText.value;
    var kind = $("prompt-kind").value;
    disable(true);
    fetch("/api/review/prompt", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(Object.assign({ text: text, prompt_kind: kind },
                                          semanticOpts())),
    }).then(handleJson).catch(handleFetchError).finally(function () {
      disable(false);
    });
  }

  function semanticOpts() {
    var box = $("semantic-enabled");
    if (!box || !box.checked) return {};
    return {
      semantic_enabled: true,
      egress_policy: ($("egress-policy") || { value: "metadata_only" }).value,
    };
  }

  // ---------------- skill tab ----------------
  var skillFiles = $("skill-files");
  var skillCount = $("skill-count");
  var minimalNote = $("skill-minimal-note");
  skillFiles.addEventListener("change", function () {
    var n = skillFiles.files ? skillFiles.files.length : 0;
    skillCount.textContent = n + " 个文件";
  });
  $("skill-profile").addEventListener("change", function () {
    minimalNote.hidden = $("skill-profile").value !== "minimal";
  });
  $("skill-submit").addEventListener("click", function () {
    submitSkill();
  });

  function submitSkill() {
    var files = skillFiles.files || [];
    if (!files.length) {
      showError({ code: "no_files", message: "请先选择一个包含 SKILL.md 的文件夹。" });
      return;
    }
    var fd = new FormData();
    fd.append("profile", $("skill-profile").value);
    var opts = semanticOpts();
    if (opts.semantic_enabled) {
      fd.append("semantic_enabled", "true");
      fd.append("egress_policy", opts.egress_policy);
    }
    for (var i = 0; i < files.length; i++) {
      var f = files[i];
      // webkitRelativePath is the browser-normalised relative path
      // rooted at the picked folder. That's the same identity the
      // server-side normaliser will re-check.
      var rel = f.webkitRelativePath || f.name;
      fd.append("files", f, rel);
    }
    disable(true);
    fetch("/api/review/skill", { method: "POST", body: fd })
      .then(handleJson)
      .catch(handleFetchError)
      .finally(function () { disable(false); });
  }

  function disable(state) {
    $("prompt-submit").disabled = state;
    $("skill-submit").disabled = state;
    $("loading").hidden = !state;
    if (state) {
      $("result").hidden = true;
      $("error").hidden = true;
    }
  }

  // ---------------- response handling ----------------
  function handleJson(resp) {
    return resp.json().then(function (body) {
      if (!resp.ok) throw body;
      renderResult(body);
    });
  }
  function handleFetchError(err) {
    showError(err && err.error
      ? err.error
      : { code: "network_error", message: "网络或服务器错误。" });
  }
  function showError(errObj) {
    var el = $("error");
    el.textContent = ""; // clear
    var title = mk("strong", { text: "无法完成检查：" });
    var friendly = friendlyErrorMessage(errObj);
    var msg = mk("span", { text: " " + friendly });
    el.appendChild(title);
    el.appendChild(msg);
    el.hidden = false;
    $("result").hidden = true;
    $("loading").hidden = true;
  }

  function friendlyErrorMessage(err) {
    // Stable machine codes stay in English; UI translates the common ones.
    var m = {
      "prompt_too_large": "Prompt 内容过大，请拆分后重试。",
      "file_too_large": "某个上传文件超过 512 KiB 预算，请拆分。",
      "total_too_large": "上传总体超过 8 MiB，请分批处理。",
      "too_many_files": "上传文件数量超过上限，请精简。",
      "bad_path": "文件路径不安全（包含 .. / 绝对路径 / 反斜杠），已拒绝。",
      "bad_prompt_kind": "prompt 类型必须是 user_prompt 或 system_prompt。",
      "bad_profile": "profile 必须是 standard 或 minimal。",
      "no_files": "请先选中一个包含 SKILL.md 的文件夹。",
      "intake_error": "安全摄入拒绝了这份输入，具体原因附在 code 中。",
      "host_not_allowed": "本服务只接受 loopback 地址。",
      "origin_not_allowed": "本服务只接受 loopback 来源。",
    };
    var code = err.code || "unknown";
    return (m[code] || err.message || code) + "（code=" + code + "）";
  }

  // ---------------- render ----------------
  function renderResult(view) {
    $("error").hidden = true;
    $("loading").hidden = true;
    $("result").hidden = false;

    // Headline
    var hl = $("headline");
    hl.textContent = "";
    hl.className = "headline tone-" + view.headline.tone;
    hl.appendChild(mk("div", { className: "title", text: view.headline.title }));
    hl.appendChild(mk("div", { className: "detail", text: view.headline.detail }));

    // Next steps
    var ns = $("next-steps");
    ns.textContent = "";
    var nsData = view.nextSteps || { steps: [] };
    if (nsData.steps && nsData.steps.length) {
      ns.appendChild(mk("h3", { text: "建议处理顺序" }));
      var ol = mk("ol");
      nsData.steps.forEach(function (s) {
        ol.appendChild(mk("li", { text: s.label }));
      });
      ns.appendChild(ol);
    }

    // Coverage card
    var covText = view.coverage.status === "sufficient"
      ? "已完成" : (view.coverage.status === "insufficient" ? "不充分" : view.coverage.status);
    $("coverage").textContent = covText + (
      view.coverage.reasonCodes && view.coverage.reasonCodes.length
        ? "（原因见下方“未完成的检查”）" : ""
    );

    // Counts card
    var c = view.counts || {};
    $("counts").textContent =
      "高危 " + (c.high || 0) + "，"
      + "严重 " + (c.critical || 0) + "，"
      + "中 " + (c.medium || 0) + "，"
      + "低 " + (c.low || 0);

    // Secret scan card
    var secret = view.secretScan || {};
    var st = secret.status;
    var stText = "未运行";
    if (st === "completed") stText = "已完成";
    else if (st === "not_requested_by_profile") stText = "已明确关闭（minimal profile）";
    else if (st === "not_applicable_engine") stText = "不适用（Prompt 引擎）";
    else if (st) stText = "未完成（" + st + "）";
    $("secret-status").textContent = stText;

    // Findings
    var findingsEl = $("findings");
    findingsEl.textContent = "";
    findingsEl.appendChild(mk("h3", { text: "发现的问题（" + view.findings.length + "）" }));
    if (!view.findings.length) {
      findingsEl.appendChild(mk("p", { className: "muted",
        text: "本次未发现问题；这不能替代运行时验证，也不代表安全。" }));
    }
    // Sort findings: P0 first, then P1, P2, then severity as tiebreaker.
    var findingsSorted = (view.findings || []).slice().sort(function (a, b) {
      var pri = { P0: 0, P1: 1, P2: 2 };
      var pa = pri[((a.guidance || {}).priority) || "P1"] || 1;
      var pb = pri[((b.guidance || {}).priority) || "P1"] || 1;
      if (pa !== pb) return pa - pb;
      var sv = { critical: 0, high: 1, medium: 2, low: 3 };
      return (sv[a.severity] || 4) - (sv[b.severity] || 4);
    });

    findingsSorted.forEach(function (f) {
      var card = mk("div", { className: "finding" });
      var g = f.guidance || {};
      var top = mk("div", { className: "top" });
      top.appendChild(mk("span", { className: "badge sev-" + f.severity,
        text: sevLabel(f.severity) }));
      if (g.priority) {
        top.appendChild(mk("span", { className: "badge prio-" + g.priority,
          text: "优先级 " + g.priority }));
      }
      top.appendChild(mk("strong", { text: g.plainTitle || f.type }));
      card.appendChild(top);

      // Why it matters (short paragraph aimed at a non-technical user)
      if (g.whyItMatters) {
        var why = mk("p", { className: "why", text: g.whyItMatters });
        card.appendChild(why);
      }

      // Actionable steps
      if (g.whatToDo && g.whatToDo.length) {
        var actionsWrap = mk("div", { className: "actions" });
        actionsWrap.appendChild(mk("strong", { text: "建议怎么处理：" }));
        var ol = mk("ol");
        g.whatToDo.forEach(function (a) {
          ol.appendChild(mk("li", { text: a }));
        });
        actionsWrap.appendChild(ol);
        card.appendChild(actionsWrap);
      }

      // Technical detail folded away by default
      var d = mk("details");
      d.appendChild(mk("summary", { text: "技术详情 (Rule ID / OWASP / 证据)" }));
      d.appendChild(mk("div", { className: "muted",
        text: "Rule: " + f.type + "  origin: " + f.originKind }));
      // evidence list
      (f.evidences || []).forEach(function (ev) {
        var line = mk("div", { className: "evidence" });
        line.appendChild(mk("code", { text: ev.artifactPath || "(no path)" }));
        var range = "";
        if (ev.startByte !== null && ev.endByte !== null && ev.startByte !== undefined) {
          range = " bytes " + ev.startByte + "–" + ev.endByte;
        }
        line.appendChild(document.createTextNode(range));
        if (ev.redactedPreview) {
          line.appendChild(mk("span", { className: "muted", text: "  " + ev.redactedPreview }));
        }
        d.appendChild(line);
      });
      Object.keys(f.subject || {}).forEach(function (k) {
        d.appendChild(mk("div", { text: k + ": " + String(f.subject[k]) }));
      });
      if (f.controls && f.controls.length) {
        d.appendChild(mk("div", { text: "映射 controls：" + f.controls.join(", ") }));
      }
      if (g.referenceUrl) {
        var link = mk("div", { text: "参考：" });
        link.appendChild(mk("code", { text: g.referenceUrl }));
        d.appendChild(link);
      }
      card.appendChild(d);
      findingsEl.appendChild(card);
    });

    // Blocked / failed
    var blockedEl = $("blocked");
    blockedEl.textContent = "";
    if (view.blocked && view.blocked.length) {
      blockedEl.appendChild(mk("h3", { text: "未完成的检查（" + view.blocked.length + "）" }));
      view.blocked.forEach(function (b) {
        var row = mk("div");
        row.appendChild(mk("code", { text: b.planItemId }));
        row.appendChild(document.createTextNode(" — " + b.status
          + (b.reasonCode ? "（" + b.reasonCode + "）" : "")));
        blockedEl.appendChild(row);
      });
    }

    // Analyzers
    var anEl = $("analyzers");
    anEl.textContent = "";
    if (view.analyzers && view.analyzers.length) {
      anEl.appendChild(mk("h3", { text: "分析器状态" }));
      view.analyzers.forEach(function (a) {
        var row = mk("div");
        row.appendChild(mk("strong", { text: a.name }));
        row.appendChild(document.createTextNode(
          " " + (a.version || "") + " — " + a.status
          + (a.reasonCode ? "（" + a.reasonCode + "）" : "")));
        anEl.appendChild(row);
      });
    }

    // OWASP
    var owasp = view.owaspCoverage || {};
    var owaspEl = $("owasp");
    owaspEl.textContent = "";
    if (Object.keys(owasp).length) {
      var tbl = mk("table", { className: "owasp-table" });
      var hd = mk("tr");
      ["类别", "描述", "状态", "已映射规则"].forEach(function (h) {
        hd.appendChild(mk("th", { text: h }));
      });
      tbl.appendChild(hd);
      Object.keys(owasp).forEach(function (code) {
        var info = owasp[code];
        var row = mk("tr");
        row.appendChild(mk("td", { text: code }));
        row.appendChild(mk("td", { text: info.title }));
        row.appendChild(mk("td", { text: info.status }));
        row.appendChild(mk("td", { text: (info.rules || []).join(", ") || "(none)" }));
        tbl.appendChild(row);
      });
      owaspEl.appendChild(tbl);
    }

    // Capability matrix
    var capEl = $("capabilities");
    capEl.textContent = "";
    var caps = view.capabilities || {};
    if (Object.keys(caps).length) {
      capEl.appendChild(mk("h3", { text: "能力矩阵" }));
      var t = mk("table", { className: "owasp-table" });
      var hd = mk("tr");
      ["能力", "状态", "说明"].forEach(function (h) { hd.appendChild(mk("th", { text: h })); });
      t.appendChild(hd);
      var order = ["static", "semantic", "promptBlackbox", "skillSandbox"];
      var label = { static: "静态检查", semantic: "语义审查",
                    promptBlackbox: "Prompt 黑盒 (V1.5)",
                    skillSandbox: "Skill 隔离沙箱 (V2)" };
      order.forEach(function (k) {
        var c = caps[k]; if (!c) return;
        var row = mk("tr");
        row.appendChild(mk("td", { text: label[k] || k }));
        row.appendChild(mk("td", { text: c.status }));
        row.appendChild(mk("td", { text: c.note || "" }));
        t.appendChild(row);
      });
      capEl.appendChild(t);
    }

    // Semantic sub-block
    var semEl = $("semantic-view");
    semEl.textContent = "";
    if (view.semantic) {
      semEl.appendChild(mk("h3", { text: "语义审查（实验性）" }));
      var s = view.semantic;
      semEl.appendChild(mk("div", { text: "状态：" + s.status
        + (s.reasonCode ? "（" + s.reasonCode + "）" : "") }));
      semEl.appendChild(mk("div", { text: "出境策略：" + s.egressPolicy
        + "；候选数：" + s.candidateCount }));
      var confirmed = ((s.assessmentCounts || {}).confirmed) || 0;
      var failed = ((s.assessmentCounts || {}).validation_failed) || 0;
      semEl.appendChild(mk("div", { text:
        "确认 " + confirmed + "，拒绝 " + ((s.assessmentCounts || {}).rejected || 0)
        + "，证据不足 " + ((s.assessmentCounts || {}).insufficient_evidence || 0)
        + "，验证失败 " + failed }));
    }

    // Downloads
    var dEl = $("downloads");
    dEl.textContent = "";
    dEl.appendChild(mk("h3", { text: "下载报告" }));
    var links = [
      { href: view.downloads.json, text: "report.json" },
      { href: view.downloads.html, text: "report.html" },
      { href: view.downloads.sarif, text: "report.sarif" },
    ];
    links.forEach(function (l) {
      var a = mk("a", { text: l.text, attrs: { href: l.href, class: "download" } });
      a.className = "download";
      dEl.appendChild(a);
    });
    dEl.appendChild(mk("p", { className: "muted",
      text: "报告仅在当前进程内保存，重启后失效。" }));

    window.scrollTo({ top: $("result").offsetTop - 20, behavior: "smooth" });
  }

  function sevLabel(sev) {
    return ({ low: "低", medium: "中", high: "高", critical: "严重" })[sev] || sev;
  }
})();
