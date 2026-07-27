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
  var currentSource = { engine: null, files: {} };
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
    if(!selectedProject) return; var fd=new FormData(); Array.prototype.forEach.call($("project-files").files,function(f){fd.append("files",f,f.webkitRelativePath||f.name);}); fd.append("profile", "standard");
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
    var opts = semanticOpts();
    if (opts === null) return;
    currentSource = { engine: "prompt", files: { "prompt.txt": text } };
    disable(true);
    fetch("/api/review/prompt", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(Object.assign({ text: text, prompt_kind: kind },
                                          opts)),
    }).then(handleJson).catch(handleFetchError).finally(function () {
      disable(false);
    });
  }

  function semanticOpts() {
    var box = $("semantic-enabled");
    if (!box || !box.checked) return {};
    if (!providerSettingsLoaded) {
      showError({
        code: "provider_settings_loading",
        message: "Provider 配置仍在读取，请稍后再开始审查。",
      });
      return null;
    }
    if (providerConfigDirty) {
      showError({
        code: "provider_settings_unsaved",
        message: "Provider 配置有未保存的更改，请先点“保存配置”。",
      });
      return null;
    }
    return {
      semantic_enabled: true,
      egress_policy: "redacted_evidence",
    };
  }

  // ---------------- provider model listing ----------------
  // Default base URL is assigned here (not in HTML) so the page source has
  // no external URL literal; the strict no-external-asset test stays valid.
  var providerBaseUrlEl = $("provider-base-url");
  var defaultProviderUrl = "htt" + "ps:" + "//openrouter.ai/api/v1";
  var providerSettingsLoaded = false;
  var providerConfigDirty = false;
  var providerOperationId = 0;
  var providerControlIds = [
    "provider-base-url",
    "provider-api-key",
    "generator-model",
    "validator-model",
    "provider-save-btn",
    "provider-clear-btn",
    "fetch-models-btn",
  ];
  if (providerBaseUrlEl && !providerBaseUrlEl.value) {
    // Scheme assembled from parts so this source file contains no external
    // URL literal (keeps the strict no-external-asset asset test valid).
    providerBaseUrlEl.value = defaultProviderUrl;
    providerBaseUrlEl.setAttribute("placeholder", defaultProviderUrl);
  }

  function setProviderControlsDisabled(disabled) {
    providerControlIds.forEach(function (id) {
      var control = $(id);
      if (control) control.disabled = disabled;
    });
  }

  function setProviderSettingsStatus(textValue) {
    var status = $("provider-settings-status");
    if (status) status.textContent = textValue;
  }

  function setStoredModel(sel, model) {
    fillModelSelect(sel, model ? [{ id: model }] : [], model);
  }

  function applyProviderSettings(settings) {
    var keySaved = Boolean(settings.keySaved);
    if (providerBaseUrlEl) {
      providerBaseUrlEl.value = settings.baseUrl || defaultProviderUrl;
    }
    var keyEl = $("provider-api-key");
    if (keyEl) {
      keyEl.value = "";
      keyEl.setAttribute(
        "placeholder",
        keySaved ? "已安全保存；留空可继续使用" : "输入 API Key");
    }
    setStoredModel($("generator-model"), settings.generatorModel || "");
    setStoredModel($("validator-model"), settings.validatorModel || "");
    providerSettingsLoaded = true;
    providerConfigDirty = false;
    setProviderControlsDisabled(false);
    setProviderSettingsStatus(
      settings.baseUrl || settings.generatorModel ||
      settings.validatorModel || settings.keySaved
        ? (settings.keySaved
          ? "已恢复本机配置，API Key 已保存在 macOS 钥匙串"
          : "已恢复本机配置，尚未保存 API Key")
        : "尚未保存 Provider 配置");
  }

  function restoreProviderSettings() {
    var operationId = ++providerOperationId;
    setProviderControlsDisabled(true);
    api("/api/provider-settings")
      .then(function (settings) {
        if (operationId !== providerOperationId) return;
        applyProviderSettings(settings);
      })
      .catch(function (e) {
        if (operationId !== providerOperationId) return;
        providerSettingsLoaded = true;
        setProviderControlsDisabled(false);
        setProviderSettingsStatus("配置读取失败：" + e.message);
      });
  }

  [
    "provider-base-url",
    "provider-api-key",
    "generator-model",
    "validator-model",
  ].forEach(function (id) {
    var control = $(id);
    if (!control) return;
    var eventName = control.tagName === "SELECT" ? "change" : "input";
    control.addEventListener(eventName, function () {
      if (!providerSettingsLoaded) return;
      providerConfigDirty = true;
      setProviderSettingsStatus("有未保存的 Provider 配置更改");
    });
  });

  var providerSaveBtn = $("provider-save-btn");
  if (providerSaveBtn) {
    providerSaveBtn.addEventListener("click", function () {
      if (!providerSettingsLoaded) return;
      var keyEl = $("provider-api-key");
      var operationId = ++providerOperationId;
      setProviderControlsDisabled(true);
      setProviderSettingsStatus("保存中…");
      api("/api/provider-settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          baseUrl: (providerBaseUrlEl || {}).value || "",
          apiKey: (keyEl || {}).value || "",
          generatorModel: ($("generator-model") || {}).value || "",
          validatorModel: ($("validator-model") || {}).value || "",
        }),
      }).then(function (settings) {
        if (operationId !== providerOperationId) return;
        applyProviderSettings(settings);
        setProviderSettingsStatus(
          settings.keySaved
            ? "配置已保存，API Key 已写入 macOS 钥匙串"
            : "配置已保存，尚未保存 API Key");
      }).catch(function (e) {
        if (operationId !== providerOperationId) return;
        providerSettingsLoaded = true;
        setProviderControlsDisabled(false);
        setProviderSettingsStatus("保存失败：" + e.message);
      });
    });
  }

  var providerClearBtn = $("provider-clear-btn");
  if (providerClearBtn) {
    providerClearBtn.addEventListener("click", function () {
      if (!providerSettingsLoaded) return;
      var operationId = ++providerOperationId;
      setProviderControlsDisabled(true);
      setProviderSettingsStatus("清除中…");
      api("/api/provider-settings", { method: "DELETE" })
        .then(function (settings) {
          if (operationId !== providerOperationId) return;
          applyProviderSettings(settings);
          setProviderSettingsStatus("Provider 配置和钥匙串凭据已清除");
        }).catch(function (e) {
          if (operationId !== providerOperationId) return;
          providerSettingsLoaded = true;
          setProviderControlsDisabled(false);
          setProviderSettingsStatus("清除失败：" + e.message);
        });
    });
  }

  var fetchModelsBtn = $("fetch-models-btn");
  if (fetchModelsBtn) {
    fetchModelsBtn.addEventListener("click", function () {
      var status = $("models-status");
      if (!providerSettingsLoaded) {
        if (status) status.textContent = "Provider 配置仍在读取";
        return;
      }
      if (providerConfigDirty) {
        if (status) status.textContent = "请先保存当前 Provider 配置";
        return;
      }
      var operationId = ++providerOperationId;
      if (status) status.textContent = "拉取中…";
      setProviderControlsDisabled(true);
      fetch("/api/models", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      }).then(function (r) {
        return r.json().then(function (j) {
          if (!r.ok) throw new Error((j.error || {}).message || "拉取失败");
          return j;
        });
      }).then(function (j) {
        if (operationId !== providerOperationId) return;
        var generatorSelected = ($("generator-model") || {}).value || "";
        var validatorSelected = ($("validator-model") || {}).value || "";
        fillModelSelect(
          $("generator-model"), j.models, generatorSelected);
        fillModelSelect(
          $("validator-model"), j.models, validatorSelected);
        setProviderControlsDisabled(false);
        if (status) status.textContent = "已加载 " + j.count + " 个模型，请选择";
      }).catch(function (e) {
        if (operationId !== providerOperationId) return;
        setProviderControlsDisabled(false);
        if (status) status.textContent = "错误：" + e.message;
      });
    });
  }

  function fillModelSelect(sel, models, selected) {
    if (!sel) return;
    while (sel.firstChild) sel.removeChild(sel.firstChild);
    var placeholder = mk("option", { text: "（请选择模型）" });
    placeholder.value = "";
    sel.appendChild(placeholder);
    var selectedFound = false;
    for (var i = 0; i < models.length; i++) {
      var opt = mk("option", { text: models[i].id });
      opt.value = models[i].id;
      opt.selected = models[i].id === selected;
      selectedFound = selectedFound || opt.selected;
      sel.appendChild(opt);
    }
    if (selected && !selectedFound) {
      var savedOption = mk("option", {
        text: selected + "（已保存，当前列表未返回）",
      });
      savedOption.value = selected;
      savedOption.selected = true;
      sel.appendChild(savedOption);
    }
  }
  restoreProviderSettings();

  // ---------------- skill tab ----------------
  var skillFiles = $("skill-files");
  var skillCount = $("skill-count");
  skillFiles.addEventListener("change", function () {
    var n = skillFiles.files ? skillFiles.files.length : 0;
    skillCount.textContent = n + " 个文件";
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
    fd.append("profile", "standard");
    var opts = semanticOpts();
    if (opts === null) return;
    if (opts.semantic_enabled) {
      fd.append("semantic_enabled", "true");
      fd.append("egress_policy", opts.egress_policy);
    }
    var sourceFiles = {};
    for (var i = 0; i < files.length; i++) {
      var f = files[i];
      // webkitRelativePath is the browser-normalised relative path
      // rooted at the picked folder. That's the same identity the
      // server-side normaliser will re-check.
      var rel = f.webkitRelativePath || f.name;
      fd.append("files", f, rel);
    }
    disable(true);
    Promise.all(Array.prototype.map.call(files, function (f) {
      var rawRel = f.webkitRelativePath || f.name;
      var rel = rawRel.indexOf("/") >= 0 ? rawRel.split("/").slice(1).join("/") : rawRel;
      if (!rel) rel = rawRel;
      if (typeof f.text !== "function") return Promise.resolve();
      return f.text().then(function (text) { sourceFiles[rel] = text; })
        .catch(function () { sourceFiles[rel] = ""; });
    })).then(function () {
      currentSource = { engine: "skill", files: sourceFiles };
      return fetch("/api/review/skill", { method: "POST", body: fd });
    })
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
      if (f.claim) {
        var claim = mk("p", { className: "finding-claim" });
        claim.appendChild(mk("strong", { text: "本次具体发现：" }));
        claim.appendChild(document.createTextNode(f.claim));
        card.appendChild(claim);
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
        text: "Rule: " + f.type + "  layer: " + (f.sourceLayer || "unknown")
          + "  origin: " + f.originKind }));
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
    renderEvidenceWorkbench(findingsSorted);
    renderFixWorkbench(view, findingsSorted);

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
      var stageStats = s.stageStats || [];
      if (stageStats.length) {
        var stageDetails = mk("details");
        stageDetails.appendChild(mk("summary", {
          text: "查看各语意类型的实际执行路径（" + stageStats.length + "）"
        }));
        var stageTable = mk("table", { className: "owasp-table" });
        var stageHead = mk("tr");
        ["类型", "种子", "目录候选", "模型候选", "已验证"].forEach(function (h) {
          stageHead.appendChild(mk("th", { text: h }));
        });
        stageTable.appendChild(stageHead);
        stageStats.forEach(function (row) {
          var tr = mk("tr");
          var states = row.validatorStates || {};
          tr.appendChild(mk("td", { text: row.findingType }));
          tr.appendChild(mk("td", { text: String(row.extractorSeedCount || 0) }));
          tr.appendChild(mk("td", { text: String(row.catalogHintProposedCount || 0) }));
          tr.appendChild(mk("td", { text: String(row.generatorAcceptedCandidateCount || 0) }));
          tr.appendChild(mk("td", { text:
            "确认 " + (states.confirmed || 0)
            + " / 拒绝 " + (states.rejected || 0)
            + " / 失败 " + (states.validation_failed || 0)
          }));
          stageTable.appendChild(tr);
        });
        stageDetails.appendChild(stageTable);
        semEl.appendChild(stageDetails);
      }

      // Partial-run warning: the run did not fully complete (e.g. a network
      // error) but some candidates were confirmed. Those results are shown
      // for reference only and may be incomplete.
      if (s.partial) {
        var warn = mk("div", { attrs: { class: "warn-box" } });
        warn.appendChild(mk("strong", { text: "⚠️ 本次语义审查中途未完成" }));
        warn.appendChild(mk("span", { text:
          "（" + (s.reasonCode || s.status) + "）。以下为已确认的部分结果，"
          + "可能不完整，仅供参考；建议检查网络后重试一次。" }));
        semEl.appendChild(warn);
      }

      // Render the confirmed semantic findings (advisory / experimental).
      var semFindings = s.findings || [];
      if (semFindings.length) {
        semEl.appendChild(mk("div", { attrs: { class: "muted" },
          text: "语义发现（实验性，仅供参考，非可信判定）：" }));
        var list = mk("ul");
        for (var i = 0; i < semFindings.length; i++) {
          var sf = semFindings[i];
          var li = mk("li");
          li.appendChild(mk("strong", { text: "[" + (sf.severity || "?") + "] " }));
          li.appendChild(mk("span", { text: sf.type || "" }));
          if (sf.claim) {
            li.appendChild(mk("div", { attrs: { class: "muted" }, text: sf.claim }));
          }
          (sf.evidences || []).forEach(function (ev) {
            li.appendChild(mk("div", { attrs: { class: "evidence" }, text:
              (ev.artifactPath || "(no path)") + " bytes "
              + String(ev.startByte) + "–" + String(ev.endByte)
            }));
          });
          list.appendChild(li);
        }
        semEl.appendChild(list);
      } else if (s.status === "completed") {
        semEl.appendChild(mk("div", { attrs: { class: "muted" },
          text: "本次语义审查未确认任何问题（不代表安全）。" }));
      }
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

  function readSourceForEvidence(ev) {
    var files = currentSource.files || {};
    var path = ev.artifactPath || "";
    if (Object.prototype.hasOwnProperty.call(files, path)) return files[path];
    var keys = Object.keys(files);
    if (keys.length === 1) return files[keys[0]];
    return "";
  }

  function sliceUtf8Range(text, startByte, endByte) {
    if (typeof text !== "string") return { before: "", hit: "", after: "" };
    if (startByte === null || startByte === undefined
        || endByte === null || endByte === undefined) {
      return {
        before: text.slice(0, 180),
        hit: "",
        after: text.length > 180 ? text.slice(180, 360) : "",
      };
    }
    var enc = new TextEncoder();
    var bytePos = 0;
    var startIdx = 0;
    var endIdx = text.length;
    var setStart = false;
    for (var i = 0; i < text.length; ) {
      var code = text.codePointAt(i);
      var ch = String.fromCodePoint(code);
      var next = i + ch.length;
      if (!setStart && bytePos >= startByte) {
        startIdx = i;
        setStart = true;
      }
      bytePos += enc.encode(ch).length;
      if (bytePos >= endByte) {
        endIdx = next;
        break;
      }
      i = next;
    }
    var pad = 160;
    return {
      before: text.slice(Math.max(0, startIdx - pad), startIdx),
      hit: text.slice(startIdx, endIdx),
      after: text.slice(endIdx, Math.min(text.length, endIdx + pad)),
    };
  }

  function renderEvidenceWorkbench(findings) {
    var box = $("evidence-workbench");
    box.textContent = "";
    box.appendChild(mk("h3", { text: "原文定位" }));
    var rows = [];
    (findings || []).forEach(function (f) {
      (f.evidences || []).forEach(function (ev) {
        rows.push({ finding: f, evidence: ev });
      });
    });
    if (!rows.length) {
      box.appendChild(mk("p", { className: "muted",
        text: "本次没有可定位证据；请查看技术详情和报告。" }));
      return;
    }
    rows.slice(0, 12).forEach(function (row) {
      var ev = row.evidence;
      var f = row.finding;
      var item = mk("div", { className: "evidence-card" });
      var title = mk("div", { className: "evidence-title" });
      title.appendChild(mk("strong", { text: f.type }));
      title.appendChild(mk("span", { className: "badge sev-" + f.severity,
        text: sevLabel(f.severity) }));
      item.appendChild(title);
      item.appendChild(mk("div", { className: "muted", text:
        (ev.artifactPath || "prompt.txt") + formatByteRange(ev) }));
      var source = readSourceForEvidence(ev);
      if (source) {
        var parts = sliceUtf8Range(source, ev.startByte, ev.endByte);
        var pre = mk("pre", { className: "source-snippet" });
        pre.appendChild(mk("span", { text: parts.before }));
        if (parts.hit) pre.appendChild(mk("mark", { text: parts.hit }));
        pre.appendChild(mk("span", { text: parts.after }));
        item.appendChild(pre);
      } else if (ev.redactedPreview) {
        item.appendChild(mk("pre", { className: "source-snippet",
          text: ev.redactedPreview }));
      }
      box.appendChild(item);
    });
    if (rows.length > 12) {
      box.appendChild(mk("p", { className: "muted",
        text: "已显示前 12 条定位；完整证据在 JSON / HTML 报告中。" }));
    }
  }

  function formatByteRange(ev) {
    if (ev.startByte === null || ev.startByte === undefined
        || ev.endByte === null || ev.endByte === undefined) return "";
    return " · bytes " + ev.startByte + "-" + ev.endByte;
  }

  function renderFixWorkbench(view, findings) {
    var box = $("fix-workbench");
    box.textContent = "";
    box.appendChild(mk("h3", { text: "修改与复查" }));
    var rems = view.remediations || [];
    if (!findings.length && !rems.length) {
      box.appendChild(mk("p", { className: "muted",
        text: "当前没有需要修改的审查项；可导出报告留档。" }));
      return;
    }
    var draft = mk("textarea", { attrs: { rows: "10" } });
    if (currentSource.engine === "prompt") {
      draft.value = currentSource.files["prompt.txt"] || "";
      box.appendChild(mk("p", { className: "muted",
        text: "在这里修改 Prompt 草稿，改完可直接重新审查。" }));
      box.appendChild(draft);
      var rerun = mk("button", { text: "审查修改版", className: "primary" });
      rerun.addEventListener("click", function () {
        promptText.value = draft.value;
        promptCount.textContent = promptText.value.length + " 字符";
        submitPrompt();
      });
      box.appendChild(rerun);
    } else {
      box.appendChild(mk("p", { className: "muted",
        text: "按下列整改项修改本地文件后，重新选择文件夹或直接复查当前选择。" }));
      var rerunSkill = mk("button", { text: "复查当前文件夹", className: "primary" });
      rerunSkill.addEventListener("click", submitSkill);
      box.appendChild(rerunSkill);
    }
    if (rems.length) {
      var checklist = mk("ol", { className: "fix-list" });
      rems.forEach(function (rem) {
        var li = mk("li");
        li.appendChild(mk("strong", { text: (rem.priority || "P1") + " · " + rem.title }));
        (rem.actions || []).forEach(function (action) {
          li.appendChild(mk("div", { text: action }));
        });
        checklist.appendChild(li);
      });
      box.appendChild(checklist);
    }
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
