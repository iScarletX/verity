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
      body: JSON.stringify({ text: text, prompt_kind: kind }),
    }).then(handleJson).catch(handleFetchError).finally(function () {
      disable(false);
    });
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
    var msg = mk("span", { text: " " + (errObj.message || errObj.code || "unknown") });
    el.appendChild(title);
    el.appendChild(msg);
    el.hidden = false;
    $("result").hidden = true;
  }

  // ---------------- render ----------------
  function renderResult(view) {
    $("error").hidden = true;
    $("result").hidden = false;

    // Headline
    var hl = $("headline");
    hl.textContent = "";
    hl.className = "headline tone-" + view.headline.tone;
    hl.appendChild(mk("div", { className: "title", text: view.headline.title }));
    hl.appendChild(mk("div", { className: "detail", text: view.headline.detail }));

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
    view.findings.forEach(function (f) {
      var card = mk("div", { className: "finding" });
      var top = mk("div", { className: "top" });
      top.appendChild(mk("span", { className: "badge sev-" + f.severity,
        text: sevLabel(f.severity) }));
      top.appendChild(mk("strong", { text: f.type }));
      top.appendChild(mk("span", { className: "muted", text: "engine origin: " + f.originKind }));
      card.appendChild(top);
      card.appendChild(mk("div", { className: "claim", text: f.claim || "(未提供描述)" }));
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
        card.appendChild(line);
      });
      // subject / controls
      var d = mk("details");
      d.appendChild(mk("summary", { text: "更多元信息" }));
      Object.keys(f.subject || {}).forEach(function (k) {
        var p = mk("div", { text: k + ": " + String(f.subject[k]) });
        d.appendChild(p);
      });
      if (f.controls && f.controls.length) {
        d.appendChild(mk("div", { text: "映射 controls：" + f.controls.join(", ") }));
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
