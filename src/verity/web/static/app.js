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

  // ---------------- trusted Skill projects ----------------
  var selectedProject = null;
  function api(url, options) { return fetch(url, options).then(function (r) { return r.json().then(function (j) { if (!r.ok) throw new Error((j.error || {}).message || "请求失败"); return j; }); }); }
  function loadProjects() {
    api("/api/projects").then(function (data) {
      var box=$("project-list"); box.textContent="";
      data.projects.forEach(function (p) {
        var b=mk("button",{text:p.displayName+"（"+p.versionIds.length+" 个版本）"});
        b.addEventListener("click",function(){ selectedProject=p.artifactId; loadProject(); }); box.appendChild(b);
      });
    }).catch(showProjectError);
  }
  function loadProject() {
    api("/api/projects/"+encodeURIComponent(selectedProject)).then(function(data){
      $("project-page").hidden=false; $("project-title").textContent=data.project.displayName;
      var h=$("project-history"); h.textContent=""; data.versions.forEach(function(v){
        var scoreText=(v.score && v.score.status==="available")
          ? " · 安全分 "+v.score.value+"（可信度 "+v.score.confidenceGrade+"）"
          : " · 安全分不可用";
        h.appendChild(mk("p",{text:v.createdAt+" · "+v.contentDigest.slice(0,12)
          +" · Coverage "+v.coverage.status+scoreText+" · "
          +Object.values(v.findingCounts).reduce(function(a,b){return a+b;},0)+" 个问题"}));
      });
      var diffBox=$("project-diff"); diffBox.textContent="";
      if(data.versions.length>1) api("/api/projects/"+encodeURIComponent(selectedProject)+"/diff").then(function(x){
        var d=x.diff; diffBox.appendChild(mk("h4",{text:"与上一版本相比"}));
        diffBox.appendChild(mk("p",{text:"新增 "+d.counts.new+"，持续 "+d.counts.existing+"，变化 "+d.counts.changed+"，已解决 "+d.counts.resolved+"，因覆盖不足无法确认 "+d.counts.unknown_due_to_coverage}));
        var sc=d.scoreComparison||{status:"not_comparable",reasonCodes:["missing"]};
        if(sc.status==="comparable"){
          var direction={improved:"提高",declined:"下降",unchanged:"不变"}[sc.direction]||sc.direction;
          diffBox.appendChild(mk("p",{text:"安全分："+sc.previous+" → "+sc.current
            +"（"+direction+" "+(sc.delta>0?"+":"")+sc.delta+"）。分数变化不能替代上方问题状态。"}));
        } else {
          diffBox.appendChild(mk("p",{className:"muted",text:
            "安全分不可比较："+(sc.reasonCodes||[]).join(", ")}));
        }
        if(d.notedCounts && Object.values(d.notedCounts).some(function(v){return v>0;})){
          var nc=d.notedCounts;
          diffBox.appendChild(mk("p",{className:"muted",text:"已标注：确认 "+nc.acknowledged+"，接受风险 "+nc.accept_risk+"，误报 "+nc.false_positive+"，不修复 "+nc.wont_fix}));
        }
        var labels={new:"新增",existing:"仍然存在",changed:"发生变化",resolved:"已解决",unknown_due_to_coverage:"无法确认"};
        d.changes.forEach(function(change){
          var s=change.summary||{}; var item=mk("details");
          var summary=mk("summary",{text:(labels[change.state]||change.state)+" · "+(s.findingType||"unknown")+" · "+(s.severity||"")}); 
          if(change.disposition){
            var disp=change.disposition;
            var badge=mk("span",{className:"badge disp-"+disp.status,text:dispositionLabel(disp.status)});
            summary.appendChild(document.createTextNode(" "));
            summary.appendChild(badge);
          }
          item.appendChild(summary);
          item.appendChild(mk("p",{text:s.claim||""}));
          if(change.state==="unknown_due_to_coverage") item.appendChild(mk("p",{className:"warn",text:"本轮相关检查未完整完成，因此不能宣称已经修复。"}));
          if(change.disposition && change.disposition.note){
            item.appendChild(mk("p",{className:"muted",text:"备注："+change.disposition.note}));
          }
          if((change.state==="existing" || change.state==="changed") && data.versions.length>0){
            var curVer=data.versions[data.versions.length-1];
            var fp=null;
            if(change.currentFindingIds && change.currentFindingIds.length>0){
              var fid=change.currentFindingIds[0];
              var finding=curVer.findings.find(function(f){return f.findingId===fid;});
              if(finding) fp=finding.fingerprint;
            }
            if(fp){
              var btn=mk("button",{text:"标注此问题",className:"small"});
              btn.addEventListener("click",function(){showDispositionForm(fp,item);});
              item.appendChild(btn);
            }
          }
          diffBox.appendChild(item);
        });
      });
    }).catch(showProjectError);
  }
  function showProjectError(e) { $("project-diff").textContent=e.message; }
  $("project-create").addEventListener("click",function(){ api("/api/projects",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({displayName:$("project-name").value})}).then(function(){ $("project-name").value=""; loadProjects(); }).catch(showProjectError); });
  $("project-submit").addEventListener("click",function(){
    if(!selectedProject) return; var fd=new FormData(); Array.prototype.forEach.call($("project-files").files,function(f){fd.append("files",f,f.webkitRelativePath||f.name);}); fd.append("profile",$("project-profile").value);
    api("/api/projects/"+encodeURIComponent(selectedProject)+"/versions",{method:"POST",body:fd}).then(loadProject).catch(showProjectError);
  });
  loadProjects();

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

    // Explainable score and separate review confidence.
    var score = view.score || {status:"unavailable",value:null};
    $("safety-score").textContent = score.status === "available"
      ? String(score.value) + " / 100"
      : "暂不评分";
    var confidence = view.reviewConfidence || {grade:"D",limitations:[]};
    $("review-confidence").textContent = confidence.grade
      + "（查看已知限制）";
    var scoreDetail = $("score-detail"); scoreDetail.textContent = "";
    scoreDetail.appendChild(mk("h3",{text:"评分依据"}));
    if(score.status !== "available"){
      scoreDetail.appendChild(mk("p",{className:"warn",text:
        "关键检查未完整完成或评分映射不完整，因此本次不显示数字分。原因："
        + ((score.reasonCodes||[]).join(", ")||"unknown")}));
    } else {
      scoreDetail.appendChild(mk("p",{text:
        "评分政策 v"+(score.policyVersion||"")+"；实际评估层："
        + ((score.evaluatedLayers||[]).join(", ")||"未知")
        +"；产生扣分层："+((score.includedLayers||[]).join(", ")||"无")
        + (score.highestSeverity ? "；最高严重度："+score.highestSeverity
          +"；分数上限："+score.severityCap : "")}));
      var deductions=(score.deductions||[]).filter(function(x){return x.points>0;});
      if(deductions.length){
        var ul=mk("ul"); deductions.forEach(function(x){
          ul.appendChild(mk("li",{text:"扣 "+x.points+" 分 · "
            +(x.riskIds||[]).join(", ")+" · "+x.severity
            +(x.factorPercent<100?"（同类重复，按 "+x.factorPercent+"% 递减）":"")}));
        }); scoreDetail.appendChild(ul);
      } else scoreDetail.appendChild(mk("p",{className:"muted",text:
        "本次已完成检查未产生扣分；不代表未实现或未启用的检查也安全。"}));
    }
    if(confidence.limitations && confidence.limitations.length){
      var cd=mk("details"); cd.appendChild(mk("summary",{text:"审查可信度限制"}));
      confidence.limitations.forEach(function(x){cd.appendChild(mk("div",{text:x}));});
      scoreDetail.appendChild(cd);
    }

    // Controlled remediation plan; proposal only, never auto-applied.
    var remEl=$("remediations"); remEl.textContent="";
    var rems=view.remediations||[];
    remEl.appendChild(mk("h3",{text:"整改与复查（"+rems.length+"）"}));
    if(!rems.length) remEl.appendChild(mk("p",{className:"muted",text:
      "当前没有受控整改项；仍需结合审查可信度判断。"}));
    rems.forEach(function(rem){
      var item=mk("details");
      item.appendChild(mk("summary",{text:(rem.priority||"P1")+" · "+rem.title}));
      var actions=mk("ol"); (rem.actions||[]).forEach(function(x){
        actions.appendChild(mk("li",{text:x})); }); item.appendChild(actions);
      item.appendChild(mk("strong",{text:"改完后这样验证："}));
      var checks=mk("ul"); (rem.verificationChecks||[]).forEach(function(x){
        checks.appendChild(mk("li",{text:x.label})); }); item.appendChild(checks);
      item.appendChild(mk("p",{className:"muted",text:
        "仅提供修改建议，不会自动改写文件。风险："+(rem.riskIds||[]).join(", ")}));
      remEl.appendChild(item);
    });

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

  function dispositionLabel(status) {
    return ({acknowledged:"已确认",accept_risk:"接受风险",false_positive:"误报",wont_fix:"不修复"})[status] || status;
  }

  function showDispositionForm(fp, container) {
    if($("disp-form-"+fp)) return;
    var form = mk("div", {className: "disposition-form", attrs: {id: "disp-form-"+fp}});
    var sel = mk("select");
    [{v:"acknowledged",t:"确认"},{v:"accept_risk",t:"接受风险"},{v:"false_positive",t:"误报"},{v:"wont_fix",t:"不修复"}].forEach(function(o){
      var opt=mk("option",{text:o.t}); opt.value=o.v; sel.appendChild(opt);
    });
    var days = mk("input", {attrs:{type:"number",min:"1",max:"180",value:"30"}});
    var note = mk("input", {attrs:{maxlength:"200",placeholder:"可选备注"}});
    var save = mk("button", {text:"保存"});
    var cancel = mk("button", {text:"取消"});
    
    save.addEventListener("click", function(){
      var payload={status:sel.value,expiryDays:parseInt(days.value)||30};
      if(note.value) payload.note=note.value;
      api("/api/projects/"+encodeURIComponent(selectedProject)+"/dispositions/"+encodeURIComponent(fp),
          {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)})
        .then(function(){form.remove();loadProject();})
        .catch(showProjectError);
    });
    cancel.addEventListener("click", function(){form.remove();});
    
    form.appendChild(mk("label",{text:"状态："})); form.appendChild(sel);
    form.appendChild(mk("label",{text:" 有效天数："})); form.appendChild(days);
    form.appendChild(mk("label",{text:" 备注："})); form.appendChild(note);
    form.appendChild(save); form.appendChild(cancel);
    container.appendChild(form);
  }
})();
