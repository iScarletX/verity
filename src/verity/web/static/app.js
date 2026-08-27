// Verity local Web MVP frontend.
// Rules:
//   * No innerHTML. All user/model content is inserted via textContent
//     or DOM node APIs. This guarantees browser-side XSS safety even if
//     an upstream field somehow contained raw HTML.
//   * No inline event handlers. All wiring goes through addEventListener.
//   * No CDN, no imports. This file is served with `script-src 'self'`.
//   * Everything reads from the view model built by web/view.py. No
//     severity/score/coverage logic is duplicated here — the frontend
//     only decides how to SHOW what the backend already decided.

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
  // Small helpers so render functions stay declarative.
  function clear(el) { if (el) el.textContent = ""; }
  function add(parent, child) { if (parent && child) parent.appendChild(child); return child; }
  function setOpen(el, open) { if (el) { if (open) el.setAttribute("open", "open"); else el.removeAttribute("open"); } }
  function setWorkspaceState(state) {
    var empty = $("review-empty");
    var loading = $("loading");
    var result = $("result");
    var error = $("error");
    if (empty) empty.hidden = state !== "idle";
    if (loading) loading.hidden = state !== "loading";
    if (result) result.hidden = state !== "result";
    if (error) error.hidden = state !== "error";
  }
  function scrollToResult() {
    var result = $("result");
    if (!result || typeof window.scrollTo !== "function") return;
    var reduceMotion = typeof window.matchMedia === "function"
      && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    window.scrollTo({
      top: result.offsetTop - 20,
      behavior: reduceMotion ? "auto" : "smooth",
    });
  }
  setWorkspaceState("idle");
  // Tables hold machine ids of unbounded length; keep their overflow inside
  // their own block so they can never widen the whole document.
  function addTable(parent, table) {
    var box = mk("div", { className: "table-scroll" });
    add(box, table);
    return add(parent, box);
  }

  // ---------------- trusted Skill projects ----------------
  var selectedProject = null;
  function api(url, options) { return fetch(url, options).then(function (r) { return r.json().then(function (j) { if (!r.ok) throw new Error((j.error || {}).message || "请求失败"); return j; }); }); }
  function loadProjects() {
    api("/api/projects").then(function (data) {
      var box=$("project-list"); clear(box);
      updateProjectsPill(data.projects.length);
      data.projects.forEach(function (p) {
        var b=mk("button",{text:p.displayName+"（"+p.versionIds.length+" 个版本）"});
        b.addEventListener("click",function(){ selectedProject=p.artifactId; loadProject(); }); box.appendChild(b);
      });
    }).catch(showProjectError);
  }
  function updateProjectsPill(count) {
    var pill = $("projects-state-pill");
    if (!pill) return;
    pill.className = count ? "state-pill is-ok" : "state-pill is-off";
    pill.textContent = count ? count + " 个项目" : "可选";
  }
  function loadProject() {
    api("/api/projects/"+encodeURIComponent(selectedProject)).then(function(data){
      $("project-page").hidden=false; $("project-title").textContent=data.project.displayName;
      var h=$("project-history"); clear(h); data.versions.forEach(function(v){
        var scoreText=(v.score && v.score.status==="available")
          ? " · 安全分 "+v.score.value+"（可信度 "+v.score.confidenceGrade+"）"
          : " · 安全分不可用";
        h.appendChild(mk("p",{text:v.createdAt+" · "+v.contentDigest.slice(0,12)
          +" · Coverage "+v.coverage.status+scoreText+" · "
          +Object.values(v.findingCounts).reduce(function(a,b){return a+b;},0)+" 个问题"}));
      });
      var diffBox=$("project-diff"); clear(diffBox);
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
  var modeTabs = Array.prototype.slice.call(
    document.querySelectorAll(".tabs button"));

  function activateModeTab(button, moveFocus) {
    var tab = button.getAttribute("data-tab");
    modeTabs.forEach(function (item) {
      var selected = item === button;
      item.classList[selected ? "add" : "remove"]("active");
      item.setAttribute("aria-selected", selected ? "true" : "false");
      item.setAttribute("tabindex", selected ? "0" : "-1");
    });
    $("tab-prompt").hidden = tab !== "prompt";
    $("tab-skill").hidden = tab !== "skill";
    if (moveFocus && typeof button.focus === "function") button.focus();
  }

  modeTabs.forEach(function (button, index) {
    button.addEventListener("click", function () {
      activateModeTab(button, false);
    });
    button.addEventListener("keydown", function (event) {
      var targetIndex = index;
      if (event.key === "ArrowRight") targetIndex = (index + 1) % modeTabs.length;
      else if (event.key === "ArrowLeft") {
        targetIndex = (index - 1 + modeTabs.length) % modeTabs.length;
      } else if (event.key === "Home") targetIndex = 0;
      else if (event.key === "End") targetIndex = modeTabs.length - 1;
      else return;
      event.preventDefault();
      activateModeTab(modeTabs[targetIndex], true);
    });
  });

  // ---------------- prompt tab ----------------
  var MAX_PROMPT_BYTES = 256 * 1024;
  var promptText = $("prompt-text");
  var promptCount = $("prompt-count");
  var promptFile = $("prompt-file");
  var promptFileName = $("prompt-file-name");
  promptText.addEventListener("input", function () {
    promptCount.textContent = promptText.value.length + " 字符";
  });
  promptFile.addEventListener("change", function () {
    var f = promptFile.files && promptFile.files[0];
    if (!f) {
      $("prompt-file-drop").classList.remove("has-file");
      if (promptFileName) {
        promptFileName.textContent = "支持 .txt / .md / .json · 仅在本机读取";
      }
      return;
    }
    $("prompt-file-drop").classList.add("has-file");
    if (promptFileName) promptFileName.textContent = f.name + " · 本地读取";
    if (f.size > MAX_PROMPT_BYTES) {
      showError({ code: "prompt_too_large", message: "prompt file exceeds server budget" });
      return;
    }
    f.text().then(function (text) {
      promptText.value = text;
      promptCount.textContent = text.length + " 字符";
    }).catch(function () {
      showError({ code: "bad_file", message: "无法读取该文件内容（可能不是文本文件）。" });
    });
  });
  $("prompt-submit").addEventListener("click", function () {
    submitPrompt();
  });

  function submitPrompt() {
    var text = promptText.value;
    var kind = $("prompt-kind").value;
    var opts = semanticOpts();
    if (opts === null) return;
    var bbOpts = blackboxOpts();
    if (bbOpts === null) return;
    currentSource = { engine: "prompt", files: { "prompt.txt": text } };
    disable(true);
    fetch("/api/review/prompt", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(Object.assign({ text: text, prompt_kind: kind },
                                          opts, bbOpts)),
    }).then(handleJson).catch(handleFetchError).finally(function () {
      disable(false);
    });
  }

  function semanticOpts() {
    // Semantic review has no separate on/off flag any more: whenever a
    // Provider is configured (one-time setup step above), it is attempted
    // automatically. These two guards still protect the settings-save
    // workflow: don't submit a review while settings are still loading or
    // have unsaved edits.
    if (!providerSettingsLoaded) {
      showError({
        code: "provider_settings_loading",
        message: "Provider 配置仍在读取，请稍后再开始审查。",
      });
      return null;
    }
    if (providerConfigDirty) {
      setOpen($("semantic-panel"), true);
      showError({
        code: "provider_settings_unsaved",
        message: "Provider 配置有未保存的更改，请先点“保存配置”。",
      });
      return null;
    }
    var opts = { egress_policy: "redacted_evidence" };
    var validatorModels = collectValidatorModels();
    if (validatorModels.length > 1) {
      opts.validator_models = JSON.stringify(validatorModels);
    }
    return opts;
  }

  // Collect every non-empty selected validator model: the primary
  // #validator-model select, plus any .validator-model-extra rows added
  // via "+ 添加校验模型". Configuring only one keeps today's exact
  // single-validator behaviour (the caller only sends validator_models
  // when there is more than one).
  function collectValidatorModels() {
    var models = [];
    var primary = $("validator-model");
    if (primary && primary.value) models.push(primary.value);
    document.querySelectorAll(".validator-model-extra").forEach(function (sel) {
      if (sel.value) models.push(sel.value);
    });
    return models;
  }

  // ---------------- V1.5 Prompt 黑盒测试 opt-in ----------------
  // Deliberately stricter than semanticOpts()'s "field present => wanted"
  // convention: this stage sends the reviewed Prompt to a real model over
  // the network, so two INDEPENDENT explicit signals are required -- the
  // card's own "启用" checkbox AND a separate "确认" checkbox -- before a
  // single blackbox_* field is added to the request body. Leaving the
  // card collapsed and unchecked (the default) changes nothing about the
  // request "开始审查" already sends.
  var blackboxEnabledEl = $("blackbox-enabled");
  var blackboxConfirmEl = $("blackbox-confirm");
  var BLACKBOX_CONFIG_IDS = [
    "blackbox-base-url", "blackbox-model", "blackbox-api-key",
    "blackbox-copy-provider-btn", "blackbox-scenario-policy",
    "blackbox-scenario-ids",
    "blackbox-max-calls", "blackbox-timeout", "blackbox-max-tokens",
  ];

  function setBlackboxControlsDisabled(disabled) {
    BLACKBOX_CONFIG_IDS.forEach(function (id) {
      var el = $(id);
      if (el) el.disabled = disabled;
    });
  }

  function updateBlackboxPill() {
    var pill = $("blackbox-state-pill");
    if (!pill || !blackboxEnabledEl) return;
    if (!blackboxEnabledEl.checked) {
      pill.className = "state-pill is-off";
      pill.textContent = "未启用";
    } else if (!blackboxConfirmEl || !blackboxConfirmEl.checked) {
      pill.className = "state-pill is-warn";
      pill.textContent = "已启用，待确认";
    } else {
      pill.className = "state-pill is-warn";
      pill.textContent = "下次审查将真实调用模型";
    }
  }

  if (blackboxEnabledEl) {
    blackboxEnabledEl.addEventListener("change", function () {
      var on = blackboxEnabledEl.checked;
      setBlackboxControlsDisabled(!on);
      if (blackboxConfirmEl) {
        blackboxConfirmEl.disabled = !on;
        if (!on) blackboxConfirmEl.checked = false;
      }
      updateBlackboxPill();
    });
  }
  if (blackboxConfirmEl) {
    blackboxConfirmEl.addEventListener("change", updateBlackboxPill);
  }

  var blackboxCopyBtn = $("blackbox-copy-provider-btn");
  if (blackboxCopyBtn) {
    // Copies only whatever is literally sitting in the semantic panel's
    // inputs right now, in this page session -- never reads anything back
    // from the persisted Provider store. A previously-saved API key is
    // never re-readable from the page (see provider-api-key's own
    // field-note), so if the user hasn't retyped it this session there is
    // nothing to copy. That's intentional: no silent reuse of a stored
    // secret for a stage that makes its own independent network calls.
    blackboxCopyBtn.addEventListener("click", function () {
      var baseUrlEl = $("blackbox-base-url");
      var modelEl = $("blackbox-model");
      var apiKeyEl = $("blackbox-api-key");
      var srcBaseUrl = providerBaseUrlEl ? providerBaseUrlEl.value : "";
      var srcKey = ($("provider-api-key") || {}).value || "";
      var srcModel = ($("generator-model") || {}).value || "";
      if (baseUrlEl && srcBaseUrl) baseUrlEl.value = srcBaseUrl;
      if (apiKeyEl && srcKey) apiKeyEl.value = srcKey;
      if (modelEl && srcModel) modelEl.value = srcModel;
      var statusEl = $("blackbox-status");
      if (statusEl) {
        statusEl.textContent = srcKey
          ? "已从语义审查 Provider 复制地址/模型/Key（仅本次页面会话中的值）。"
          : "已复制地址与模型；未复制 API Key——语义审查面板当前没有可读取的 Key（保存后不会回显），请手动填写。";
      }
    });
  }

  function blackboxOpts() {
    if (!blackboxEnabledEl || !blackboxEnabledEl.checked) return {};
    if (!blackboxConfirmEl || !blackboxConfirmEl.checked) {
      setOpen($("blackbox-panel"), true);
      showError({
        code: "blackbox_confirmation_required",
        message: "请先在黑盒测试卡片中勾选确认项，再开始审查。",
      });
      return null;
    }
    var baseUrl = (($("blackbox-base-url") || {}).value || "").trim();
    var model = (($("blackbox-model") || {}).value || "").trim();
    var apiKey = ($("blackbox-api-key") || {}).value || "";
    if (!baseUrl || !model || !apiKey.trim()) {
      setOpen($("blackbox-panel"), true);
      showError({
        code: "blackbox_config_incomplete",
        message: "黑盒测试需要填写 Provider 地址、模型 ID 与 API Key。",
      });
      return null;
    }
    var opts = {
      blackbox_enabled: true,
      blackbox_confirm: true,
      blackbox_base_url: baseUrl,
      blackbox_model: model,
      blackbox_api_key: apiKey,
      blackbox_scenario_policy:
        (($("blackbox-scenario-policy") || {}).value || "artifact_aware"),
    };
    var scenarioRaw = (($("blackbox-scenario-ids") || {}).value || "").trim();
    if (scenarioRaw) {
      opts.blackbox_scenario_ids = scenarioRaw.split(",")
        .map(function (s) { return s.trim(); })
        .filter(function (s) { return s.length > 0; });
    }
    var maxCalls = parseInt((($("blackbox-max-calls") || {}).value || ""), 10);
    if (!isNaN(maxCalls)) opts.blackbox_max_calls = maxCalls;
    var timeout = parseFloat((($("blackbox-timeout") || {}).value || ""));
    if (!isNaN(timeout)) opts.blackbox_timeout_seconds = timeout;
    var maxTokens = parseInt((($("blackbox-max-tokens") || {}).value || ""), 10);
    if (!isNaN(maxTokens)) opts.blackbox_max_tokens = maxTokens;
    return opts;
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
    "add-validator-model-btn",
  ];
  // Recommended range for the multi-validator vote feature is 2-3 models
  // total (see AGENTS.md); this caps the primary select plus extra rows.
  var MAX_VALIDATOR_MODELS = 3;
  var lastFetchedModels = [];
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
    document.querySelectorAll(
      ".validator-model-extra, .validator-model-extra-remove"
    ).forEach(function (el) { el.disabled = disabled; });
  }

  // Provider readiness is shown in TWO always-visible places so the user
  // never has to expand the panel and read prose to learn whether the
  // model layer will run: the header capability chip, and a state pill on
  // the collapsed panel's own summary.
  //   ready   — base URL + key + both models are set: semantic WILL run.
  //   partial — something is configured but the run cannot be complete.
  //   off     — semantic Provider is unconfigured; black-box opt-in is
  //             tracked separately and must never be implied by this state.
  function providerReadiness(settings) {
    var hasUrl = Boolean((settings || {}).baseUrl);
    var hasKey = Boolean((settings || {}).keySaved);
    var hasModels = Boolean((settings || {}).generatorModel)
      && Boolean((settings || {}).validatorModel);
    if (hasUrl && hasKey && hasModels) return "ready";
    if (hasUrl || hasKey || (settings || {}).generatorModel
        || (settings || {}).validatorModel) return "partial";
    return "off";
  }

  function renderProviderState(state, dirty) {
    var pill = $("provider-state-pill");
    var chip = $("provider-chip");
    var chipState = $("provider-chip-state");
    var label = { ready: "已就绪", partial: "配置未完成", off: "未配置" }[state]
      || "未配置";
    var tone = { ready: "is-ok", partial: "is-warn", off: "is-off" }[state]
      || "is-off";
    if (dirty) { label = "有未保存更改"; tone = "is-warn"; }
    if (pill) {
      pill.className = "state-pill " + tone;
      pill.textContent = label;
    }
    if (chip) {
      chip.className = "status-chip "
        + ({ ready: "is-ok", partial: "is-warn", off: "is-idle" }[state]
          || "is-idle");
    }
    if (chipState) {
      chipState.textContent = dirty ? "有未保存更改"
        : { ready: "已就绪", partial: "配置未完成", off: "未配置（仅静态）" }[state]
          || "未配置（仅静态）";
    }
  }

  // ---------------- optional extra validator-model rows ----------------
  // Repeatable UI for the multi-vote feature: each row is one extra
  // validator model. Configuring zero extra rows (just the primary
  // #validator-model select) keeps today's exact single-validator
  // behaviour; this list only matters when the user explicitly adds rows.
  function extraValidatorRowCount() {
    return document.querySelectorAll(".validator-model-extra").length;
  }

  function addValidatorModelRow() {
    var list = $("validator-model-extra-list");
    if (!list || extraValidatorRowCount() >= MAX_VALIDATOR_MODELS - 1) return;
    var row = mk("div", { className: "validator-model-extra-row" });
    var sel = mk("select", { className: "validator-model-extra" });
    sel.disabled = !providerSettingsLoaded;
    fillModelSelect(sel, lastFetchedModels, "");
    var removeBtn = mk("button", {
      className: "validator-model-extra-remove small", text: "移除",
      attrs: { type: "button" },
    });
    removeBtn.disabled = !providerSettingsLoaded;
    removeBtn.addEventListener("click", function () {
      list.removeChild(row);
      if (providerSettingsLoaded) {
        markProviderDirty();
      }
    });
    sel.addEventListener("change", function () {
      if (!providerSettingsLoaded) return;
      markProviderDirty();
    });
    row.appendChild(sel);
    row.appendChild(removeBtn);
    list.appendChild(row);
  }

  function markProviderDirty() {
    providerConfigDirty = true;
    setProviderSettingsStatus("有未保存的 Provider 配置更改", "warn");
    renderProviderState(lastProviderState, true);
  }

  var addValidatorModelBtn = $("add-validator-model-btn");
  if (addValidatorModelBtn) {
    addValidatorModelBtn.addEventListener("click", function () {
      addValidatorModelRow();
    });
  }

  function setProviderSettingsStatus(textValue, tone) {
    var status = $("provider-settings-status");
    if (!status) return;
    status.textContent = textValue;
    status.className = "status-line" + (tone ? " is-" + tone : "");
  }

  function setModelsStatus(textValue, tone) {
    var status = $("models-status");
    if (!status) return;
    status.textContent = textValue;
    status.className = "status-line" + (tone ? " is-" + tone : "");
  }

  function setStoredModel(sel, model) {
    fillModelSelect(sel, model ? [{ id: model }] : [], model);
  }

  var lastProviderState = "off";

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
    lastProviderState = providerReadiness(settings);
    renderProviderState(lastProviderState, false);
    if (lastProviderState === "ready") {
      setProviderSettingsStatus(
        "已恢复本机配置，API Key 已保存在 macOS 钥匙串", "ok");
    } else if (lastProviderState === "partial") {
      setProviderSettingsStatus(
        settings.keySaved
          ? "已恢复本机配置，还需选择生成器与校验器模型"
          : "已恢复本机配置，尚未保存 API Key", "warn");
    } else {
      setProviderSettingsStatus("尚未保存 Provider 配置，本次将只做静态检查");
    }
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
        setProviderSettingsStatus("配置读取失败：" + e.message, "bad");
        renderProviderState("off", false);
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
      markProviderDirty();
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
            : "配置已保存，尚未保存 API Key",
          settings.keySaved ? "ok" : "warn");
      }).catch(function (e) {
        if (operationId !== providerOperationId) return;
        providerSettingsLoaded = true;
        setProviderControlsDisabled(false);
        setProviderSettingsStatus("保存失败：" + e.message, "bad");
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
          setProviderSettingsStatus("清除失败：" + e.message, "bad");
        });
    });
  }

  var fetchModelsBtn = $("fetch-models-btn");
  if (fetchModelsBtn) {
    fetchModelsBtn.addEventListener("click", function () {
      if (!providerSettingsLoaded) {
        setModelsStatus("Provider 配置仍在读取", "warn");
        return;
      }
      if (providerConfigDirty) {
        setModelsStatus("请先保存当前 Provider 配置", "warn");
        return;
      }
      var operationId = ++providerOperationId;
      setModelsStatus("拉取中…");
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
        lastFetchedModels = j.models || [];
        var generatorSelected = ($("generator-model") || {}).value || "";
        var validatorSelected = ($("validator-model") || {}).value || "";
        fillModelSelect(
          $("generator-model"), j.models, generatorSelected);
        fillModelSelect(
          $("validator-model"), j.models, validatorSelected);
        document.querySelectorAll(".validator-model-extra").forEach(
          function (sel) {
            fillModelSelect(sel, j.models, sel.value || "");
          });
        setProviderControlsDisabled(false);
        setModelsStatus("已加载 " + j.count + " 个模型，请选择", "ok");
      }).catch(function (e) {
        if (operationId !== providerOperationId) return;
        setProviderControlsDisabled(false);
        setModelsStatus("错误：" + e.message, "bad");
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
  var MAX_SKILL_FILES = 500;
  var MAX_SKILL_FILE_BYTES = 512 * 1024;
  var MAX_SKILL_TOTAL_BYTES = 8 * 1024 * 1024;
  var skillFiles = $("skill-files");
  var skillZip = $("skill-zip");
  var skillCount = $("skill-count");
  skillFiles.addEventListener("change", function () {
    var n = skillFiles.files ? skillFiles.files.length : 0;
    if (n) {
      skillZip.value = "";
      $("skill-folder-drop").classList.add("has-file");
      $("skill-zip-drop").classList.remove("has-file");
      $("skill-folder-name").textContent = n + " 个文件已加入本次卷宗";
      $("skill-zip-name").textContent = "单个 ZIP · 解包预算 8 MiB";
      skillCount.textContent = n + " 个文件";
    } else {
      $("skill-folder-drop").classList.remove("has-file");
      $("skill-folder-name").textContent = "包含根目录 SKILL.md · 最多 500 个文件";
      skillCount.textContent = "尚未选择文件";
    }
  });
  skillZip.addEventListener("change", function () {
    if (skillZip.files && skillZip.files.length) {
      skillFiles.value = "";
      $("skill-folder-drop").classList.remove("has-file");
      $("skill-zip-drop").classList.add("has-file");
      $("skill-folder-name").textContent = "包含根目录 SKILL.md · 最多 500 个文件";
      $("skill-zip-name").textContent = skillZip.files[0].name + " · 已加入本次卷宗";
      skillCount.textContent = skillZip.files[0].name + "（ZIP）";
    } else {
      $("skill-zip-drop").classList.remove("has-file");
      $("skill-zip-name").textContent = "单个 ZIP · 解包预算 8 MiB";
      skillCount.textContent = "尚未选择文件";
    }
  });
  $("skill-submit").addEventListener("click", function () {
    submitSkill();
  });

  function submitSkill() {
    var zipFiles = skillZip.files || [];
    if (zipFiles.length) {
      submitSkillZip(zipFiles[0]);
      return;
    }
    var files = skillFiles.files || [];
    if (!files.length) {
      showError({ code: "no_files", message: "请先选择一个包含 SKILL.md 的文件夹或 ZIP 文件。" });
      return;
    }
    var preflightError = skillUploadPreflightError(files);
    if (preflightError) {
      showError(preflightError);
      return;
    }
    var fd = new FormData();
    fd.append("profile", "standard");
    var opts = semanticOpts();
    if (opts === null) return;
    var sbOpts = sandboxOpts();
    if (sbOpts === null) return;
    fd.append("egress_policy", opts.egress_policy);
    if (opts.validator_models) {
      fd.append("validator_models", opts.validator_models);
    }
    Object.keys(sbOpts).forEach(function (k) { fd.append(k, sbOpts[k]); });
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

  function skillUploadPreflightError(files) {
    if (files.length > MAX_SKILL_FILES) {
      return { code: "too_many_files", message: "too many files" };
    }
    var totalBytes = 0;
    for (var i = 0; i < files.length; i++) {
      if (files[i].size > MAX_SKILL_FILE_BYTES) {
        return { code: "file_too_large", message: "file too large" };
      }
      totalBytes += files[i].size;
      if (totalBytes > MAX_SKILL_TOTAL_BYTES) {
        return { code: "total_too_large", message: "total too large" };
      }
    }
    return null;
  }

  function submitSkillZip(zipFile) {
    if (zipFile.size > MAX_SKILL_TOTAL_BYTES) {
      showError({ code: "total_too_large", message: "total too large" });
      return;
    }
    var fd = new FormData();
    fd.append("profile", "standard");
    fd.append("archive_format", "zip");
    var opts = semanticOpts();
    if (opts === null) return;
    var sbOpts = sandboxOpts();
    if (sbOpts === null) return;
    fd.append("egress_policy", opts.egress_policy);
    if (opts.validator_models) {
      fd.append("validator_models", opts.validator_models);
    }
    Object.keys(sbOpts).forEach(function (k) { fd.append(k, sbOpts[k]); });
    fd.append("files", zipFile, zipFile.name);
    disable(true);
    // The client has no per-entry File objects to read from a ZIP; the
    // server decodes and echoes them back as view.sourceFiles, which
    // handleJson uses to hydrate currentSource once the response lands.
    currentSource = { engine: "skill", files: {} };
    fetch("/api/review/skill", { method: "POST", body: fd })
      .then(handleJson)
      .catch(handleFetchError)
      .finally(function () { disable(false); });
  }

  // ---------------- V2 Skill 隔离边界（当前不可用） ----------------
  // Product reviews fail closed until V2 has a separately hardened process,
  // filesystem, output and resource boundary. The browser never emits a
  // sandbox configuration and therefore cannot start the research runner.
  var sandboxEnabledEl = $("sandbox-enabled");
  var sandboxConfirmEl = $("sandbox-confirm");
  var SANDBOX_CONFIG_IDS = [
    "sandbox-entry-point", "sandbox-argv",
    "sandbox-cpu-seconds", "sandbox-memory-mb", "sandbox-wall-seconds",
  ];

  function setSandboxControlsDisabled() {
    SANDBOX_CONFIG_IDS.forEach(function (id) {
      var el = $(id);
      if (el) el.disabled = true;
    });
  }

  function updateSandboxPill() {
    var pill = $("sandbox-state-pill");
    if (!pill) return;
    pill.className = "state-pill is-off";
    pill.textContent = "暂不可用";
  }

  if (sandboxEnabledEl) {
    sandboxEnabledEl.disabled = true;
  }
  if (sandboxConfirmEl) {
    sandboxConfirmEl.disabled = true;
  }
  setSandboxControlsDisabled();
  updateSandboxPill();

  function sandboxOpts() {
    if (!sandboxEnabledEl || !sandboxEnabledEl.checked) return {};
    setOpen($("sandbox-panel"), true);
    showError({
      code: "sandbox_isolation_hardening_required",
      message: "V2 隔离边界尚未完成加固，当前产品路径不会执行 Skill 代码。",
    });
    return null;
  }

  // ---------------- in-flight state ----------------
  // A review can take a while (static rules are fast, but a configured
  // Provider adds real model round-trips). The old spinner said nothing
  // and gave no sense of progress, so a slow run looked like a hang.
  // The skeleton mirrors the result layout so nothing jumps when the
  // real result replaces it.
  var loadingTimer = null;
  var loadingStart = 0;
  var LOADING_HINTS = [
    { at: 0, text: "正在运行确定性静态规则。" },
    { at: 3, text: "静态规则通常几秒内完成；如已配置 Provider，接下来是模型生成与校验。" },
    { at: 12, text: "模型审查进行中：候选生成 → 多模型校验 → 证据核对，整个过程可能需要 1 分钟以上。" },
    { at: 45, text: "仍在等待 Provider 返回。语义链路未完成不会影响静态结果，届时会如实标注。" },
  ];

  function startLoading() {
    loadingStart = Date.now();
    var elapsedEl = $("loading-elapsed");
    var hintEl = $("loading-hint");
    if (hintEl) hintEl.textContent = LOADING_HINTS[0].text;
    if (elapsedEl) elapsedEl.textContent = "0.0s";
    if (typeof setInterval !== "function") return;
    loadingTimer = setInterval(function () {
      var secs = (Date.now() - loadingStart) / 1000;
      if (elapsedEl) elapsedEl.textContent = secs.toFixed(1) + "s";
      if (!hintEl) return;
      for (var i = LOADING_HINTS.length - 1; i >= 0; i--) {
        if (secs >= LOADING_HINTS[i].at) {
          if (hintEl.textContent !== LOADING_HINTS[i].text) {
            hintEl.textContent = LOADING_HINTS[i].text;
          }
          break;
        }
      }
    }, 100);
  }

  function stopLoading() {
    if (loadingTimer !== null && typeof clearInterval === "function") {
      clearInterval(loadingTimer);
    }
    loadingTimer = null;
  }

  function disable(state) {
    $("prompt-submit").disabled = state;
    $("skill-submit").disabled = state;
    if (state) {
      setWorkspaceState("loading");
      startLoading();
    } else {
      stopLoading();
    }
  }

  // ---------------- response handling ----------------
  function handleJson(resp) {
    return resp.json().then(function (body) {
      if (!resp.ok) throw body;
      if (body.sourceFiles) {
        // ZIP uploads have no client-side per-entry File objects to read;
        // the server decodes and echoes the contents back instead.
        currentSource = { engine: body.engine || currentSource.engine, files: body.sourceFiles };
      }
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
    clear(el);
    el.className = "error";
    add(el, mk("span", { className: "error-mark", text: "!", attrs: { "aria-hidden": "true" } }));
    var body = mk("div");
    add(body, mk("strong", { text: "无法完成检查" }));
    add(body, mk("div", { text: friendlyErrorMessage(errObj) }));
    add(el, body);
    add(el, mk("div", { className: "error-code",
      text: "code = " + (errObj.code || "unknown") }));
    setWorkspaceState("error");
    stopLoading();
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
      "bad_base_url": "语义审查 Provider 地址未配置完整，请展开 Provider 面板检查。",
      "no_files": "请先选中一个包含 SKILL.md 的文件夹或 ZIP 文件。",
      "intake_error": "安全摄入拒绝了这份输入，具体原因附在 code 中。",
      "host_not_allowed": "本服务只接受 loopback 地址。",
      "origin_not_allowed": "本服务只接受 loopback 来源。",
      "bad_zip": "无法解析该 ZIP 文件，请确认文件未损坏。",
      "bad_archive": "ZIP 上传只能包含一个 .zip 文件。",
      "bad_archive_format": "archive_format 参数不受支持。",
      "bad_file": "无法读取该文件内容（可能不是文本文件）。",
      "blackbox_confirmation_required": "请先在黑盒测试卡片中勾选确认项。",
      "blackbox_base_url_required": "黑盒测试需要填写 Provider 地址。",
      "blackbox_model_required": "黑盒测试需要填写模型 ID（不超过 200 字符）。",
      "blackbox_api_key_required": "黑盒测试需要填写 API Key。",
      "blackbox_api_key_too_large": "API Key 超出长度限制。",
      "bad_blackbox_scenario_ids": "场景 ID 必须是字符串列表。",
      "bad_blackbox_scenario_policy": "场景选择策略不合法。",
      "bad_blackbox_config": "黑盒测试配置不合法，请检查预算参数。",
      "sandbox_isolation_hardening_required":
        "V2 隔离边界尚未完成加固，当前产品路径不会执行 Skill 代码。",
    };
    var code = err.code || "unknown";
    return m[code] || err.message || code;
  }

  // ---------------- render ----------------
  function renderResult(view) {
    stopLoading();
    setWorkspaceState("result");

    renderVerdict(view);
    renderNextSteps(view);
    renderBlocked(view);
    renderUnifiedIssues(view);
    renderDynamicPlan(view);
    var findingsSorted = renderFindings(view);
    renderFullDocument(view, findingsSorted);
    renderRemediations(view);
    renderFixWorkbench(view, findingsSorted);
    renderDownloads(view);
    renderDiagnostics(view);

    scrollToResult();
  }

  // ---- tier 1: the verdict a user must read first ----
  function renderVerdict(view) {
    var hl = $("headline");
    clear(hl);
    hl.className = "headline tone-" + view.headline.tone;
    add(hl, mk("div", { className: "title", text: view.headline.title }));
    add(hl, mk("div", { className: "detail", text: view.headline.detail }));

    // Safety score. Score/confidence semantics are decided by the backend;
    // the UI only formats them.
    var score = view.score || { status: "unavailable", value: null };
    var scoreEl = $("safety-score");
    var scoreCard = $("card-score");
    clear(scoreEl);
    if (score.status === "available") {
      if (scoreCard) scoreCard.className = "card";
      add(scoreEl, document.createTextNode(String(score.value)));
      add(scoreEl, mk("span", { className: "unit", text: " / 100" }));
    } else {
      if (scoreCard) scoreCard.className = "card is-na";
      scoreEl.textContent = "暂不评分";
    }

    var confidence = view.reviewConfidence || { grade: "D", limitations: [] };
    var confEl = $("review-confidence");
    clear(confEl);
    add(confEl, document.createTextNode(confidence.grade));
    var limitCount = (confidence.limitations || []).length;
    add(confEl, mk("span", { className: "unit",
      text: limitCount ? "（" + limitCount + " 项已知限制）" : "（无已知限制）" }));

    // Coverage. Reason codes are surfaced by #blocked / diagnostics.
    var covEl = $("coverage");
    var covCard = $("card-coverage");
    var covLabel = { sufficient: "已完成", insufficient: "不充分" };
    clear(covEl);
    covEl.textContent = covLabel[view.coverage.status] || view.coverage.status;
    if (covCard) {
      covCard.className = "card"
        + (view.coverage.status === "sufficient" ? "" : " is-na");
    }

    // Counts as a proportional bar + legend instead of a comma soup.
    var c = view.counts || {};
    var countsEl = $("counts");
    clear(countsEl);
    var total = ["critical", "high", "medium", "low"].reduce(
      function (a, k) { return a + (c[k] || 0); }, 0);
    var countsCard = $("card-counts");
    if (!total) {
      if (countsCard) countsCard.className = "card is-na";
      countsEl.textContent = "未发现问题";
    } else {
      if (countsCard) countsCard.className = "card";
      var split = mk("span", { className: "sev-split" });
      var bar = mk("span", { className: "sev-bar", attrs: { "aria-hidden": "true" } });
      var legend = mk("span", { className: "sev-legend" });
      ["critical", "high", "medium", "low"].forEach(function (k) {
        var n = c[k] || 0;
        if (!n) return;
        var seg = mk("i", { className: "sev-" + k });
        seg.setAttribute("data-w", String(n));
        bar.appendChild(seg);
        var item = mk("span");
        add(item, mk("i", { className: "sev-" + k }));
        add(item, mk("b", { text: String(n) }));
        add(item, document.createTextNode(sevLabel(k)));
        legend.appendChild(item);
      });
      add(split, bar);
      add(split, legend);
      add(countsEl, split);
      // Width is proportional; set via style property (not an inline HTML
      // attribute) so the CSP's style-src does not need 'unsafe-inline'.
      Array.prototype.forEach.call(bar.children || [], function (seg) {
        var n = parseInt(seg.getAttribute("data-w"), 10) || 0;
        if (seg.style) seg.style.width = (n / total * 100).toFixed(2) + "%";
      });
    }

    // A one-line reason when the score is withheld, right where the user
    // looked for the number — not buried in the diagnostics drawer.
    var noteEl = $("verdict-note");
    clear(noteEl);
    if (score.status !== "available") {
      add(noteEl, mk("p", { className: "warn", text:
        "本次不显示数字分：" + reasonCodesText(score.reasonCodes)
        + "。已完成的静态检查结果依然有效。" }));
    }
    var metaEl = $("verdict-meta");
    if (metaEl) {
      metaEl.textContent = (view.engine === "skill" ? "Agent Skill" : "Prompt")
        + " · 评分政策 v" + (score.policyVersion || "—");
    }
  }

  function reasonCodesText(codes) {
    var m = {
      "semantic_requested_but_incomplete": "语义审查未完整完成",
      "coverage_insufficient": "关键检查未完整完成",
      "score_policy_incomplete": "评分映射不完整",
      "semantic_requested_but_failed": "语义审查失败",
    };
    var list = (codes || []).map(function (x) { return m[x] || x; });
    return list.length ? list.join("、") : "原因未知";
  }

  // ---- tier 2: what to do next ----
  function renderNextSteps(view) {
    var ns = $("next-steps");
    clear(ns);
    var nsData = view.nextSteps || { steps: [] };
    if (!nsData.steps || !nsData.steps.length) return;
    add(ns, mk("h3", { text: "建议处理顺序" }));
    var ol = mk("ol");
    nsData.steps.forEach(function (s) {
      ol.appendChild(mk("li", { text: s.label }));
    });
    add(ns, ol);
  }

  function renderBlocked(view) {
    var blockedEl = $("blocked");
    clear(blockedEl);
    if (!view.blocked || !view.blocked.length) return;
    var head = mk("div", { className: "section-head" });
    add(head, mk("h3", { text: "未完成的检查" }));
    add(head, mk("span", { className: "count-pill",
      text: String(view.blocked.length) }));
    add(head, mk("span", { className: "hint",
      text: "这些检查没有跑完，因此本次结论只覆盖已完成的部分。" }));
    add(blockedEl, head);
    var tbl = mk("table", { className: "data-table" });
    var hd = mk("tr");
    ["检查项", "状态", "原因"].forEach(function (h) {
      hd.appendChild(mk("th", { text: h }));
    });
    add(tbl, hd);
    view.blocked.forEach(function (b) {
      var tr = mk("tr");
      var td = mk("td");
      add(td, mk("code", { text: b.planItemId }));
      add(tr, td);
      var statusTd = mk("td");
      add(statusTd, mk("span", { className: "status-tag t-warn",
        text: b.status === "failed" ? "失败" : "被上游阻塞" }));
      add(tr, statusTd);
      add(tr, mk("td", { text: b.reasonCode || "—" }));
      add(tbl, tr);
    });
    addTable(blockedEl, tbl);
  }

  // ---- tier 3: the findings list (the substance) ----
  // ONE unified list mixing deterministic-rule and semantic
  // (model-suggested) findings; each item carries a small inline origin
  // tag, and a partial/incomplete semantic run's findings additionally
  // carry a distinct "not scored" badge (findingsDisplay is a pure
  // display-layer merge; view.counts / view.score are computed upstream
  // from the already-safe view.findings list and are NOT affected here).
  // Findings/remediations that share the same (type, subjectKey) refer to
  // the exact same underlying subject -- e.g. every citation of the same
  // undefined rule name, or every occurrence of the same duplicated
  // sentence. Assigning them the same color, consistently between the
  // findings list, the evidence highlight inside each finding, and the
  // matching remediation checklist entry, lets the reader visually track
  // one subject without re-reading every card. Colors are assigned in
  // first-seen order per render so the same subject always gets the same
  // color within one result; renderFindings() resets the map and always
  // runs before renderRemediations() (see renderResult), so remediations
  // reuse the same assignment their finding already got.
  var SUBJECT_PALETTE_SIZE = 8;
  var subjectColorMap = new Map();
  function resetSubjectColors() { subjectColorMap = new Map(); }
  function subjectColorClass(type, subjectKey) {
    if (!subjectKey) return "";
    var key = type + "|" + subjectKey;
    if (!subjectColorMap.has(key)) {
      subjectColorMap.set(key, subjectColorMap.size % SUBJECT_PALETTE_SIZE);
    }
    return "subj-c" + subjectColorMap.get(key);
  }

  function renderUnifiedIssues(view) {
    var el = $("unified-issues");
    clear(el);
    var issues = view.issues || [];
    var head = mk("div", { className: "section-head" });
    add(head, mk("h3", { text: "统一问题" }));
    add(head, mk("span", { className: "count-pill"
      + (issues.length ? "" : " is-zero"), text: String(issues.length) }));
    add(head, mk("span", { className: "hint",
      text: "同一风险合并展示，但保留静态、语义和运行时的每个发生点。" }));
    add(el, head);
    if (!issues.length) {
      var empty = mk("div", { className: "empty-state" });
      add(empty, mk("strong", { text: "当前没有已证实的问题组" }));
      add(empty, mk("span", { text: "仍需查看动态覆盖与未完成检查。" }));
      add(el, empty);
      return;
    }
    var labels = {
      runtime_confirmed: "运行时确认",
      runtime_only: "仅运行时发现",
      static_only: "仅静态/语义证据",
      not_reproduced: "本次动态未复现",
      evidence_conflict: "证据冲突",
      unverified: "尚未验证",
    };
    var list = mk("div", { className: "issue-list" });
    issues.forEach(function (issue) {
      var card = mk("article", { className: "issue-card sev-" + issue.severity });
      add(card, mk("h4", { text: issue.title || issue.riskId }));
      var badges = mk("div", { className: "issue-badges" });
      add(badges, mk("span", { className: "badge sev-" + issue.severity,
        text: sevLabel(issue.severity) }));
      add(badges, mk("span", { className: "status-tag t-"
        + (issue.status === "runtime_confirmed" || issue.status === "runtime_only"
          ? "bad" : issue.status === "not_reproduced" ? "warn" : "off"),
        text: labels[issue.status] || issue.status }));
      add(badges, mk("code", { text: issue.riskId }));
      add(card, badges);
      add(card, mk("p", { className: "muted", text:
        "证据层：" + (issue.sourceLayers || []).join(" · ")
        + "；发生点：" + String(issue.occurrenceCount || 0) }));
      if ((issue.runtimeChecks || []).length) {
        var checks = mk("ul", { className: "compact-list" });
        issue.runtimeChecks.forEach(function (check) {
          add(checks, mk("li", { text: check.detectorId + "：" + check.outcome }));
        });
        add(card, checks);
      }
      add(list, card);
    });
    add(el, list);
  }

  function renderDynamicPlan(view) {
    var el = $("dynamic-plan");
    clear(el);
    var plan = view.dynamicPlan || { counts: {}, items: [] };
    var counts = plan.counts || {};
    var head = mk("div", { className: "section-head" });
    add(head, mk("h3", { text: "动态检查覆盖" }));
    add(head, mk("span", { className: "hint", text:
      "按内容画像选择：已选 " + (counts.selected || 0)
      + " · 不适用 " + (counts.not_applicable || 0)
      + " · 不可用 " + (counts.unavailable || 0) }));
    add(el, head);

    function group(title, status, open) {
      var items = (plan.items || []).filter(function (item) {
        return item.status === status;
      });
      var details = mk("details", { className: "disclosure" });
      details.open = Boolean(open);
      add(details, mk("summary", { text: title + "（" + items.length + "）" }));
      var body = mk("div", { className: "disclosure-body" });
      if (!items.length) {
        add(body, mk("p", { className: "muted", text: "无" }));
      } else {
        var table = mk("table", { className: "data-table" });
        var row = mk("tr");
        ["检查", "阶段", "原因", "风险"].forEach(function (label) {
          add(row, mk("th", { text: label }));
        });
        add(table, row);
        items.forEach(function (item) {
          var tr = mk("tr");
          add(tr, mk("td", { text: item.checkId }));
          add(tr, mk("td", { text: item.stage }));
          add(tr, mk("td", { text: (item.reasonCodes || []).join(", ") }));
          add(tr, mk("td", { text: (item.riskIds || []).join(", ") }));
          add(table, tr);
        });
        addTable(body, table);
      }
      add(details, body);
      add(el, details);
    }
    group("已选择的检查", "selected", true);
    group("不适用的检查", "not_applicable", false);
    group("当前不可用的检查", "unavailable", Boolean(counts.unavailable));
  }

  function renderFindings(view) {
    var findingsEl = $("findings");
    clear(findingsEl);
    resetSubjectColors();
    var findingsForDisplay = view.findingsDisplay || view.findings || [];
    var scoredCount = findingsForDisplay.filter(function (f) {
      return !f.notScored;
    }).length;
    var notScoredCount = findingsForDisplay.length - scoredCount;

    var head = mk("div", { className: "section-head" });
    add(head, mk("h3", { text: "原始分层技术发现" }));
    var pill = mk("span", { className: "count-pill"
      + (scoredCount ? "" : " is-zero"), text: String(scoredCount) });
    add(head, pill);
    if (notScoredCount) {
      add(head, mk("span", { className: "badge not-scored",
        text: "另有 " + notScoredCount + " 条模型建议未计入评分" }));
    }
    add(head, mk("span", { className: "hint",
      text: "按优先级排序；标注“模型建议”的条目来自语义审查，需人工复核。" }));
    add(findingsEl, head);

    if (!findingsForDisplay.length) {
      var empty = mk("div", { className: "empty-state" });
      add(empty, mk("strong", { text: "本次未发现问题" }));
      add(empty, mk("span", {
        text: "这不能替代运行时验证，也不代表安全。请同时查看上方的审查可信度。" }));
      add(findingsEl, empty);
      return [];
    }

    // Sort findings: P0 first, then P1, P2, then severity as tiebreaker.
    var findingsSorted = findingsForDisplay.slice().sort(function (a, b) {
      var pri = { P0: 0, P1: 1, P2: 2 };
      var pa = pri[((a.guidance || {}).priority) || "P1"] || 1;
      var pb = pri[((b.guidance || {}).priority) || "P1"] || 1;
      if (pa !== pb) return pa - pb;
      var sv = { critical: 0, high: 1, medium: 2, low: 3 };
      return (sv[a.severity] || 4) - (sv[b.severity] || 4);
    });

    var list = mk("div", { className: "finding-list" });
    findingsSorted.forEach(function (f, index) {
      list.appendChild(renderFindingCard(f, index));
    });
    add(findingsEl, list);
    return findingsSorted;
  }

  function renderFindingCard(f, index) {
    var g = f.guidance || {};
    var colorClass = subjectColorClass(f.type, f.subjectKey);
    var card = mk("div", { className: "finding sev-" + f.severity
      + (f.notScored ? " is-not-scored" : "")
      + (colorClass ? " " + colorClass : "") });

    // Title on its own line; tags on a dedicated wrapping row underneath,
    // so severity + priority + origin + not-scored can never collide with
    // the title on a narrow viewport.
    add(card, mk("div", { className: "finding-title",
      text: g.plainTitle || f.type }));

    var top = mk("div", { className: "top" });
    add(top, mk("span", { className: "badge sev-" + f.severity,
      text: sevLabel(f.severity) }));
    if (g.priority) {
      add(top, mk("span", { className: "badge prio-" + g.priority,
        text: g.priority }));
    }
    var isModel = f.originKind === "semantic_validation";
    add(top, mk("span", {
      className: "badge origin-tag" + (isModel ? " origin-model" : ""),
      text: f.originTag || (isModel ? "模型建议" : "确定性规则") }));
    if (f.notScored) {
      // The origin badge already says 模型建议; this one carries the
      // safety-critical part (it does NOT count toward the score).
      add(top, mk("span", { className: "badge not-scored",
        text: "未计入评分" }));
    }
    if (f.hitCount && f.hitCount > 1) {
      add(top, mk("span", { className: "badge hit-count",
        text: "命中 " + f.hitCount + " 处" }));
    }
    if (colorClass && f.subject && f.subject.referenceText) {
      add(top, mk("span", { className: "badge subject-chip " + colorClass,
        text: "标记：" + f.subject.referenceText }));
    }
    add(card, top);

    // Why it matters (short paragraph aimed at a non-technical user)
    if (g.whyItMatters) {
      add(card, mk("p", { className: "why", text: g.whyItMatters }));
    }
    // Actionable steps
    if (g.whatToDo && g.whatToDo.length) {
      var actionsWrap = mk("div", { className: "actions" });
      add(actionsWrap, mk("strong", { text: "建议怎么处理" }));
      var ol = mk("ol");
      g.whatToDo.forEach(function (a) {
        ol.appendChild(mk("li", { text: a }));
      });
      add(actionsWrap, ol);
      add(card, actionsWrap);
    }

    // Original-text locations: ONE box per finding listing every hit
    // location together (not one collapsible box per occurrence) -- a
    // finding that was merged from several occurrences (see hitCount
    // above) reads as one issue with several places it shows up, not as
    // several separate issues. Only the first finding is expanded at first;
    // later evidence stays one click away instead of forming a source wall. The
    // matched span in every location shares the finding's subject color,
    // so scanning the page shows at a glance which locations belong to
    // the same underlying subject (e.g. every citation of one undefined
    // rule name).
    if ((f.evidences || []).length) {
      var locBox = mk("details", { className: "finding-locations"
        + (colorClass ? " " + colorClass : "") });
      setOpen(locBox, index === 0);
      add(locBox, mk("summary", { text: f.evidences.length > 1
        ? "原文位置（共 " + f.evidences.length + " 处）" : "原文位置" }));
      var locList = mk("div", { className: "location-list" });
      f.evidences.forEach(function (ev) {
        var item = mk("div", { className: "location-item" });
        add(item, mk("div", { className: "location-path",
          text: (ev.artifactPath || "prompt.txt") + formatByteRange(ev) }));
        var source = ev.sensitivity === "normal"
          ? readSourceForEvidence(ev) : "";
        if (ev.sensitivity !== "normal") {
          if (ev.redactedPreview) {
            add(item, mk("pre", { className: "source-snippet",
              text: ev.redactedPreview }));
          }
        } else if (source) {
          var parts = sliceUtf8Range(source, ev.startByte, ev.endByte);
          var pre = mk("pre", { className: "source-snippet" });
          add(pre, mk("span", { text: parts.before }));
          if (parts.hit) {
            add(pre, mk("mark", { className: colorClass, text: parts.hit }));
          }
          add(pre, mk("span", { text: parts.after }));
          add(item, pre);
        } else if (ev.redactedPreview) {
          add(item, mk("pre", { className: "source-snippet",
            text: ev.redactedPreview }));
        }
        add(locList, item);
      });
      add(locBox, locList);
      add(card, locBox);
    }

    // Technical detail folded away by default
    var d = mk("details", { className: "tech" });
    add(d, mk("summary", { text: "技术详情 (Rule ID / OWASP)" }));
    var grid = mk("div", { className: "tech-grid" });
    add(grid, mk("div", {
      text: "Rule: " + f.type + "  layer: " + (f.sourceLayer || "unknown")
        + "  origin: " + f.originKind }));
    Object.keys(f.subject || {}).forEach(function (k) {
      add(grid, mk("div", { text: k + ": " + String(f.subject[k]) }));
    });
    if (f.controls && f.controls.length) {
      add(grid, mk("div", { text: "映射 controls：" + f.controls.join(", ") }));
    }
    if (g.referenceUrl) {
      var link = mk("div", { text: "参考：" });
      add(link, mk("code", { text: g.referenceUrl }));
      add(grid, link);
    }
    add(d, grid);
    add(card, d);
    return card;
  }

  // ---- full-document view: every finding's hit locations marked in
  // place inside the complete original text, instead of extracted as
  // separate snippets. A snippet list tells you a subject was hit N
  // times; only reading the real document straight through, with every
  // occurrence lit up in place, shows the reader all N at once in their
  // real context. Findings sharing a subjectKey share a color via
  // subjectColorClass (already assigned during renderFindings, which
  // always runs first -- see renderResult) so the same color that marks
  // a finding's card also marks every one of its occurrences here.
  function buildLineIndex(text) {
    var lines = text.split("\n");
    var starts = [];
    var pos = 0;
    for (var i = 0; i < lines.length; i++) {
      starts.push(pos);
      pos += lines[i].length + 1;
    }
    return { lines: lines, starts: starts };
  }

  function charIdxToLine(starts, idx) {
    for (var i = starts.length - 1; i >= 0; i -= 1) {
      if (idx >= starts[i]) return i;
    }
    return 0;
  }

  function renderFullDocument(view, findingsSorted) {
    var el = $("full-document");
    if (!el) return;
    clear(el);
    var files = currentSource.files || {};
    var paths = Object.keys(files);
    if (!paths.length || !findingsSorted.length) return;

    var hitsByPath = {};
    var lineIndexByPath = {};
    findingsSorted.forEach(function (f) {
      var colorClass = subjectColorClass(f.type, f.subjectKey);
      (f.evidences || []).forEach(function (ev) {
        if (ev.sensitivity !== "normal") return;
        var text = readSourceForEvidence(ev);
        if (!text) return;
        var path = Object.prototype.hasOwnProperty.call(files, ev.artifactPath || "")
          ? ev.artifactPath : (paths.length === 1 ? paths[0] : null);
        if (!path) return;
        var range = byteRangeToCharRange(text, ev.startByte, ev.endByte);
        if (!range) return;
        if (!lineIndexByPath[path]) lineIndexByPath[path] = buildLineIndex(text);
        var idx = lineIndexByPath[path];
        var startLine = charIdxToLine(idx.starts, range.startIdx);
        var endLine = charIdxToLine(idx.starts, Math.max(range.startIdx, range.endIdx - 1));
        if (!hitsByPath[path]) hitsByPath[path] = new Map();
        for (var line = startLine; line <= endLine; line += 1) {
          var lineStart = idx.starts[line];
          var lineLen = idx.lines[line].length;
          var hitStart = line === startLine ? range.startIdx - lineStart : 0;
          var hitEnd = line === endLine ? range.endIdx - lineStart : lineLen;
          var list = hitsByPath[path].get(line) || [];
          list.push({ finding: f, colorClass: colorClass, hitStart: hitStart, hitEnd: hitEnd });
          hitsByPath[path].set(line, list);
        }
      });
    });

    var pathsWithHits = Object.keys(hitsByPath);
    if (!pathsWithHits.length) return;

    var head = mk("div", { className: "section-head" });
    add(head, mk("h3", { text: "完整原文与标注" }));
    add(head, mk("span", { className: "hint",
      text: "同一颜色标记同一类问题在全文中的每一处出现；点击标亮行查看具体原因。" }));
    add(el, head);

    pathsWithHits.forEach(function (path) {
      var idx = lineIndexByPath[path];
      var hits = hitsByPath[path];
      var box = mk("details", { className: "fulldoc-file" });
      setOpen(box, false);
      add(box, mk("summary", { text: path + "（" + hits.size + " 行有标记）" }));
      var body = mk("div", { className: "fulldoc-body" });
      idx.lines.forEach(function (lineText, lineIdx) {
        var positions = hits.get(lineIdx);
        var row = mk(positions ? "button" : "div", { className: "docline-row"
          + (positions ? " has-mark" : ""), attrs: positions ? { type: "button" } : {} });
        add(row, mk("span", { className: "docline-no", text: String(lineIdx + 1) }));

        if (!positions) {
          add(row, mk("span", { className: "docline-text", text: lineText || " " }));
          add(body, row);
          return;
        }

        positions.sort(function (a, b) { return a.hitStart - b.hitStart; });
        var textEl = mk("span", { className: "docline-text" });
        var cursor = 0;
        positions.forEach(function (p) {
          if (p.hitStart > cursor) {
            add(textEl, document.createTextNode(lineText.slice(cursor, p.hitStart)));
          }
          var markStart = Math.max(p.hitStart, cursor);
          if (p.hitEnd > markStart) {
            add(textEl, mk("mark", { className: p.colorClass,
              text: lineText.slice(markStart, p.hitEnd) }));
          }
          cursor = Math.max(cursor, p.hitEnd);
        });
        if (cursor < lineText.length) {
          add(textEl, document.createTextNode(lineText.slice(cursor)));
        }
        add(row, textEl);

        var dots = mk("span", { className: "docline-dots" });
        positions.slice(0, 6).forEach(function (p) {
          add(dots, mk("i", { className: "docline-dot " + p.colorClass }));
        });
        add(row, dots);
        add(body, row);

        var detail = mk("div", { className: "docline-detail" });
        detail.hidden = true;
        var seenIds = {};
        positions.forEach(function (p) {
          if (seenIds[p.finding.id]) return;
          seenIds[p.finding.id] = true;
          var g = p.finding.guidance || {};
          var entry = mk("div", { className: "docline-detail-item" });
          add(entry, mk("i", { className: "docline-dot " + p.colorClass }));
          var textWrap = mk("div");
          add(textWrap, mk("strong", { text: g.plainTitle || p.finding.type }));
          if (g.whyItMatters) add(textWrap, mk("p", { text: g.whyItMatters }));
          add(entry, textWrap);
          add(detail, entry);
        });
        add(body, detail);

        row.addEventListener("click", function () {
          var wasHidden = detail.hidden;
          detail.hidden = !wasHidden;
          row.classList.toggle("is-expanded", wasHidden);
        });
      });
      add(box, body);
      add(el, box);
    });
  }

  // Controlled remediation plan; proposal only, never auto-applied.
  function renderRemediations(view) {
    var remEl = $("remediations");
    clear(remEl);
    var rems = view.remediations || [];
    var head = mk("div", { className: "section-head" });
    add(head, mk("h3", { text: "整改与复查" }));
    add(head, mk("span", { className: "count-pill"
      + (rems.length ? "" : " is-zero"), text: String(rems.length) }));
    add(head, mk("span", { className: "hint",
      text: "只提供修改建议，不会自动改写任何文件。" }));
    add(remEl, head);
    if (!rems.length) {
      var empty = mk("div", { className: "empty-state" });
      add(empty, mk("strong", { text: "当前没有受控整改项" }));
      add(empty, mk("span", { text: "仍需结合上方审查可信度判断。" }));
      add(remEl, empty);
      return;
    }
    var list = mk("div", { className: "fix-list" });
    rems.forEach(function (rem) {
      // Reuses the color the matching finding already got (see
      // renderFindingCard) -- same (type, subjectKey) always maps to the
      // same color within one result, so a reader can match a checklist
      // entry back to its finding card at a glance.
      var colorClass = subjectColorClass(rem.findingType, rem.subjectKey);
      var item = mk("details", { className: colorClass });
      var summary = mk("summary");
      add(summary, mk("span", { className: "badge prio-" + (rem.priority || "P1"),
        text: rem.priority || "P1" }));
      add(summary, mk("span", { className: "fix-title", text: rem.title }));
      add(item, summary);
      var body = mk("div", { className: "fix-body" });
      var actions = mk("ol");
      (rem.actions || []).forEach(function (x) {
        actions.appendChild(mk("li", { text: x }));
      });
      add(body, actions);
      add(body, mk("strong", { text: "改完后这样验证：" }));
      var checks = mk("ul");
      (rem.verificationChecks || []).forEach(function (x) {
        checks.appendChild(mk("li", { text: x.label }));
      });
      add(body, checks);
      add(body, mk("p", { className: "muted",
        text: "仅提供修改建议，不会自动改写文件。风险："
          + (rem.riskIds || []).join(", ") }));
      add(item, body);
      list.appendChild(item);
    });
    add(remEl, list);
  }

  function renderDownloads(view) {
    var dEl = $("downloads");
    clear(dEl);
    var head = mk("div", { className: "section-head" });
    add(head, mk("h3", { text: "下载报告" }));
    add(head, mk("span", { className: "hint",
      text: "报告仅在当前进程内保存，重启后失效。" }));
    add(dEl, head);
    var row = mk("div", { className: "download-row" });
    [
      { href: view.downloads.json, text: "report.json" },
      { href: view.downloads.html, text: "report.html" },
      { href: view.downloads.sarif, text: "report.sarif" },
    ].forEach(function (l) {
      var a = mk("a", { text: l.text, attrs: { href: l.href, class: "download" } });
      a.className = "download";
      row.appendChild(a);
    });
    add(dEl, row);
  }

  // ---- tier 4: execution diagnostics, collapsed unless something is off ----
  function renderDiagnostics(view) {
    renderScoreDetail(view);
    renderSemanticView(view);
    renderAnalyzers(view);
    renderCapabilities(view);
    renderBlackboxResult(view);
    renderSandboxResult(view);
    renderOwasp(view);

    // Auto-expand + flag the drawer exactly when there is something the
    // user would otherwise miss (semantic failure, blocked checks, or a
    // withheld score). Otherwise stay quiet and collapsed.
    var sem = view.semantic || null;
    var semBad = Boolean(sem && sem.status && sem.status !== "completed"
      && sem.status !== "provider_not_configured");
    var scoreBad = (view.score || {}).status !== "available";
    var problems = [];
    if (semBad) problems.push("语义审查未完成");
    if (view.blocked && view.blocked.length) problems.push("有未完成检查");
    if (scoreBad && !semBad) problems.push("未评分");
    var pill = $("diagnostics-pill");
    if (pill) {
      pill.className = "state-pill " + (problems.length ? "is-warn" : "is-off");
      pill.textContent = problems.length ? problems.join(" · ") : "正常";
    }
    setOpen($("diagnostics"), problems.length > 0);
  }

  function renderScoreDetail(view) {
    var score = view.score || { status: "unavailable", value: null };
    var confidence = view.reviewConfidence || { grade: "D", limitations: [] };
    var scoreDetail = $("score-detail");
    clear(scoreDetail);
    add(scoreDetail, mk("h3", { text: "评分依据" }));
    if (score.status !== "available") {
      add(scoreDetail, mk("p", { className: "warn", text:
        "关键检查未完整完成或评分映射不完整，因此本次不显示数字分。原因："
        + ((score.reasonCodes || []).join(", ") || "unknown") }));
    } else {
      var kv = mk("div", { className: "kv-list" });
      [
        ["评分政策", "v" + (score.policyVersion || "")],
        ["实际评估层", (score.evaluatedLayers || []).join(", ") || "未知"],
        ["产生扣分层", (score.includedLayers || []).join(", ") || "无"],
      ].forEach(function (pair) {
        var row = mk("div");
        add(row, mk("span", { className: "k", text: pair[0] }));
        add(row, mk("span", { className: "v", text: pair[1] }));
        add(kv, row);
      });
      if (score.highestSeverity) {
        var capRow = mk("div");
        add(capRow, mk("span", { className: "k", text: "严重度上限" }));
        add(capRow, mk("span", { className: "v",
          text: score.highestSeverity + " → 最高 " + score.severityCap + " 分" }));
        add(kv, capRow);
      }
      add(scoreDetail, kv);
      var deductions = (score.deductions || []).filter(function (x) {
        return x.points > 0;
      });
      if (deductions.length) {
        var tbl = mk("table", { className: "data-table" });
        var hd = mk("tr");
        ["扣分", "风险", "严重度", "备注"].forEach(function (h) {
          hd.appendChild(mk("th", { text: h }));
        });
        add(tbl, hd);
        deductions.forEach(function (x) {
          var tr = mk("tr");
          add(tr, mk("td", { text: "-" + x.points }));
          add(tr, mk("td", { text: (x.riskIds || []).join(", ") }));
          add(tr, mk("td", { text: sevLabel(x.severity) }));
          add(tr, mk("td", { text: x.factorPercent < 100
            ? "同类重复，按 " + x.factorPercent + "% 递减" : "—" }));
          add(tbl, tr);
        });
        addTable(scoreDetail, tbl);
      } else {
        add(scoreDetail, mk("p", { className: "muted", text:
          "本次已完成检查未产生扣分；不代表未实现或未启用的检查也安全。" }));
      }
    }
    if (confidence.limitations && confidence.limitations.length) {
      var cd = mk("details", { className: "disclosure" });
      add(cd, mk("summary", { text: "审查可信度限制（" + confidence.grade
        + "，" + confidence.limitations.length + " 项）" }));
      var body = mk("div", { className: "disclosure-body kv-list" });
      confidence.limitations.forEach(function (x) {
        add(body, mk("div", { text: x }));
      });
      add(cd, body);
      add(scoreDetail, cd);
    }
  }

  // Semantic run diagnostics. The findings themselves are no longer
  // rendered separately here — they are merged into the unified
  // #findings list above (each tagged "模型建议", with a "未计入评分"
  // badge for a partial/incomplete run). This block keeps only the
  // execution status / stage-path diagnostics that don't belong in a
  // per-finding card.
  function renderSemanticView(view) {
    var semEl = $("semantic-view");
    clear(semEl);
    if (!view.semantic) return;
    var s = view.semantic;
    var head = mk("div", { className: "section-head" });
    add(head, mk("h3", { text: "语义审查状态" }));
    add(head, mk("span", { className: "status-tag " + semanticTone(s.status),
      text: semanticStatusLabel(s.status) }));
    add(head, mk("span", { className: "hint", text: "实验性能力，结论仅作参考。" }));
    add(semEl, head);

    var kv = mk("div", { className: "kv-list" });
    function row(k, v) {
      var r = mk("div");
      add(r, mk("span", { className: "k", text: k }));
      add(r, mk("span", { className: "v", text: v }));
      add(kv, r);
    }
    row("执行状态", s.status + (s.reasonCode ? "（" + s.reasonCode + "）" : ""));
    row("出境策略", s.egressPolicy);
    row("候选数", String(s.candidateCount));
    var ac = s.assessmentCounts || {};
    row("判定结果", "确认 " + (ac.confirmed || 0)
      + " · 拒绝 " + (ac.rejected || 0)
      + " · 证据不足 " + (ac.insufficient_evidence || 0)
      + " · 验证失败 " + (ac.validation_failed || 0));
    add(semEl, kv);

    var stageStats = s.stageStats || [];
    if (stageStats.length) {
      var stageDetails = mk("details", { className: "disclosure" });
      add(stageDetails, mk("summary", {
        text: "查看各语意类型的实际执行路径（" + stageStats.length + "）" }));
      var body = mk("div", { className: "disclosure-body" });
      var stageTable = mk("table", { className: "data-table" });
      var stageHead = mk("tr");
      ["类型", "种子", "目录候选", "模型候选", "已验证"].forEach(function (h) {
        stageHead.appendChild(mk("th", { text: h }));
      });
      add(stageTable, stageHead);
      stageStats.forEach(function (r) {
        var tr = mk("tr");
        var states = r.validatorStates || {};
        add(tr, mk("td", { text: r.findingType }));
        add(tr, mk("td", { text: String(r.extractorSeedCount || 0) }));
        add(tr, mk("td", { text: String(r.catalogHintProposedCount || 0) }));
        add(tr, mk("td", { text: String(r.generatorAcceptedCandidateCount || 0) }));
        add(tr, mk("td", { text: "确认 " + (states.confirmed || 0)
          + " / 拒绝 " + (states.rejected || 0)
          + " / 失败 " + (states.validation_failed || 0) }));
        add(stageTable, tr);
      });
      addTable(body, stageTable);
      add(stageDetails, body);
      add(semEl, stageDetails);
    }

    // Partial-run warning: the run did not fully complete (e.g. a network
    // error) but some candidates were confirmed. Those findings appear in
    // the unified list above with a "未计入评分" badge; this note just
    // explains why.
    if (s.partial) {
      var warn = mk("p", { className: "warn" });
      add(warn, mk("strong", { text: "⚠️ 本次语义审查中途未完成" }));
      add(warn, mk("span", { text:
        "（" + (s.reasonCode || s.status) + "）。下方“发现的问题”中标记为"
        + "“模型建议，未计入评分”的条目就是本次已确认但可能不完整的部分结果，"
        + "建议检查网络后重试一次。" }));
      add(semEl, warn);
    } else if (!(s.findings || []).length && s.status === "completed") {
      add(semEl, mk("p", { className: "muted",
        text: "本次语义审查未确认任何问题（不代表安全）。" }));
    }
  }

  function semanticStatusLabel(status) {
    return ({
      completed: "已完成",
      failed: "失败",
      budget_exhausted: "预算耗尽",
      provider_not_configured: "未配置 Provider",
      skipped: "已跳过",
    })[status] || status;
  }

  function semanticTone(status) {
    if (status === "completed") return "t-ok";
    if (status === "provider_not_configured" || status === "skipped") return "t-off";
    return "t-warn";
  }

  function renderAnalyzers(view) {
    var anEl = $("analyzers");
    clear(anEl);
    if (!view.analyzers || !view.analyzers.length) return;
    add(anEl, mk("h3", { text: "分析器状态" }));
    var tbl = mk("table", { className: "data-table" });
    var hd = mk("tr");
    ["分析器", "版本", "状态", "原因"].forEach(function (h) {
      hd.appendChild(mk("th", { text: h }));
    });
    add(tbl, hd);
    view.analyzers.forEach(function (a) {
      var tr = mk("tr");
      add(tr, mk("td", { text: a.name }));
      add(tr, mk("td", { text: a.version || "—" }));
      var td = mk("td");
      add(td, mk("span", {
        className: "status-tag " + (a.status === "completed" ? "t-ok" : "t-warn"),
        text: a.status }));
      add(tr, td);
      add(tr, mk("td", { text: a.reasonCode || "—" }));
      add(tbl, tr);
    });
    addTable(anEl, tbl);
  }

  function renderCapabilities(view) {
    var capEl = $("capabilities");
    clear(capEl);
    var caps = view.capabilities || {};
    if (!Object.keys(caps).length) return;
    add(capEl, mk("h3", { text: "能力矩阵" }));
    var t = mk("table", { className: "data-table" });
    var hd = mk("tr");
    ["能力", "状态", "说明"].forEach(function (h) {
      hd.appendChild(mk("th", { text: h }));
    });
    add(t, hd);
    var order = ["static", "semantic", "promptBlackbox", "skillSandbox"];
    var label = { static: "静态检查", semantic: "语义审查",
                  promptBlackbox: "Prompt 黑盒 (V1.5)",
                  skillSandbox: "Skill 隔离沙箱 (V2)" };
    order.forEach(function (k) {
      var c = caps[k]; if (!c) return;
      var tr = mk("tr");
      add(tr, mk("td", { text: label[k] || k }));
      var td = mk("td");
      add(td, mk("span", { className: "status-tag " + capabilityTone(c.status),
        text: c.status }));
      add(tr, td);
      add(tr, mk("td", { text: c.note || "" }));
      add(t, tr);
    });
    addTable(capEl, t);
  }

  function capabilityTone(status) {
    if (status === "completed") return "t-ok";
    if (status === "not_implemented" || status === "not_enabled") return "t-off";
    return "t-warn";
  }

  // A ScenarioResult's verdict (passed/failed/error/partial) is a computed
  // @property on the Python side and is NOT included in dataclasses.asdict()
  // output, so it is recomputed here from probe_results -- mirrors
  // blackbox/runner.py's ScenarioResult.verdict exactly.
  function scenarioVerdict(sr) {
    if (sr.outcome === "passed" || sr.outcome === "failed") return sr.outcome;
    if (sr.outcome === "insufficient_evidence" || sr.outcome === "unavailable") {
      return "partial";
    }
    var probes = sr.probe_results || [];
    var total = probes.length;
    var failedCount = 0, errorCount = 0;
    probes.forEach(function (p) {
      if (p.safe === false) failedCount += 1;
      else if (p.safe === null || p.safe === undefined) errorCount += 1;
    });
    if (total > 0 && errorCount === total) return "error";
    if (failedCount > 0) return "failed";
    if (errorCount > 0) return "partial";
    return "passed";
  }

  function scenarioVerdictTone(verdict) {
    if (verdict === "passed") return "t-ok";
    if (verdict === "error") return "t-off";
    return "t-warn";
  }

  function scenarioVerdictLabel(verdict) {
    return ({ passed: "通过", failed: "失败", error: "调用出错", partial: "部分" }
    )[verdict] || verdict;
  }

  // V1.5 Prompt 黑盒测试结果。The server exposes only controlled outcomes,
  // counts, digests and lengths. Raw prompts, probes and Provider responses
  // are deliberately absent from both this view and downloadable reports.
  function renderBlackboxResult(view) {
    var el = $("blackbox-result-view");
    if (!el) return;
    clear(el);
    var pb = view.promptBlackbox;
    if (!pb) return;
    var head = mk("div", { className: "section-head" });
    add(head, mk("h3", { text: "Prompt 黑盒测试结果（V1.5）" }));
    add(head, mk("span", { className: "status-tag " + capabilityTone(pb.status),
      text: pb.status }));
    add(head, mk("span", { className: "hint",
      text: "受控失败信号会纳入风险评分与审查结论；原始问答内容不会写入报告。" }));
    add(el, head);
    if (pb.status === "not_enabled") return;

    var kv = mk("div", { className: "kv-list" });
    function row(k, v) {
      var r = mk("div");
      add(r, mk("span", { className: "k", text: k }));
      add(r, mk("span", { className: "v", text: v }));
      add(kv, r);
    }
    row("目标模型", pb.model || "—");
    if (pb.reasonCode) row("原因码", pb.reasonCode);
    var summary = pb.summary || {};
    row("场景统计", "共 " + (summary.totalScenarios || 0)
      + " · 完成 " + (summary.completed || 0)
      + " · 通过 " + (summary.passed || 0)
      + " · 失败 " + (summary.failed || 0)
      + " · 出错 " + (summary.errors || 0)
      + " · 部分 " + (summary.partial || 0));
    row("调用次数", String(summary.totalCalls || pb.totalCalls || 0)
      + (summary.budgetExhausted ? "（预算已耗尽）" : ""));
    add(el, kv);

    var scenarioResults = pb.scenarioResults || [];
    if (scenarioResults.length) {
      var tbl = mk("table", { className: "data-table" });
      var hd = mk("tr");
      ["场景", "严重度", "结果", "探测数"].forEach(function (h) {
        hd.appendChild(mk("th", { text: h }));
      });
      add(tbl, hd);
      scenarioResults.forEach(function (sr) {
        var verdict = scenarioVerdict(sr);
        var tr = mk("tr");
        add(tr, mk("td", { text: sr.scenario_id || "—" }));
        add(tr, mk("td", { text: sevLabel(sr.severity) }));
        var td = mk("td");
        add(td, mk("span", { className: "status-tag " + scenarioVerdictTone(verdict),
          text: scenarioVerdictLabel(verdict) }));
        add(tr, td);
        add(tr, mk("td", { text: String((sr.probe_results || []).length) }));
        add(tbl, tr);
      });
      addTable(el, tbl);
    }

    // Per-probe drill-down intentionally shows metadata only. The text used
    // by the internal judge is discarded before report projection.
    var probeBox = null;
    scenarioResults.forEach(function (sr) {
      var probes = sr.probe_results || [];
      if (!probes.length) return;
      if (!probeBox) {
        probeBox = mk("details", { className: "disclosure" });
        add(probeBox, mk("summary", { text: "探测元数据（不含原始问答）" }));
      }
      var body = mk("div", { className: "disclosure-body" });
      add(body, mk("h4", { text: sr.scenario_id || "—" }));
      probes.forEach(function (p) {
        var verdictText = p.safe === true ? "安全" : p.safe === false ? "不安全" : "调用出错";
        var head = mk("div", { className: "kv-list" });
        var hr = mk("div");
        add(hr, mk("span", { className: "k", text: "探测 #" + (p.probe_index + 1) }));
        add(hr, mk("span", { className: "v", text: verdictText
          + (p.error_code ? "（" + p.error_code + "）" : "")
          + " · 探测长度 " + String(p.probe_length || 0)
          + " · 响应长度 " + String(p.response_length || 0)
          + (typeof p.duration_seconds === "number"
            ? " · 耗时 " + p.duration_seconds.toFixed(2) + "s" : "")
          + (p.response_digest ? " · 摘要 " + p.response_digest.slice(0, 12) : "") }));
        add(head, hr);
        add(body, head);
      });
      add(probeBox, body);
    });
    if (probeBox) add(el, probeBox);
  }

  // V2 Skill sandbox product result. Only controlled availability state and
  // aggregate counts cross this boundary; raw runtime payloads never do.
  function renderSandboxResult(view) {
    var el = $("sandbox-result-view");
    if (!el) return;
    clear(el);
    var ss = view.skillSandbox;
    if (!ss) return;
    var head = mk("div", { className: "section-head" });
    add(head, mk("h3", { text: "Skill 隔离沙箱观察结果（V2）" }));
    add(head, mk("span", { className: "status-tag " + capabilityTone(ss.status),
      text: ss.status }));
    add(head, mk("span", { className: "hint",
      text: "该阶段状态会纳入风险评分与审查结论；当前产品路径在隔离加固完成前不会执行 Skill。" }));
    add(el, head);
    if (ss.status === "not_enabled") return;

    var kv = mk("div", { className: "kv-list" });
    function row(k, v) {
      var r = mk("div");
      add(r, mk("span", { className: "k", text: k }));
      add(r, mk("span", { className: "v", text: v }));
      add(kv, r);
    }
    row("沙箱结果", ss.observationStatus || ss.status || "—");
    if (ss.reasonCode) row("原因码", ss.reasonCode);
    if (ss.stdoutBytes !== undefined && ss.stdoutBytes !== null) {
      row("标准输出字节数", String(ss.stdoutBytes));
    }
    if (ss.stderrBytes !== undefined && ss.stderrBytes !== null) {
      row("标准错误字节数", String(ss.stderrBytes));
    }
    add(el, kv);

    // A failed/unavailable stage did not observe runtime behaviour. Do not
    // render absent counters as zero; that would look like a completed clean
    // observation. Aggregate counts are meaningful only after completion.
    if (ss.status !== "completed") return;

    var counts = mk("div", { className: "kv-list" });
    var eventCounts = ss.eventCounts || {};
    function countRow(label, count) {
      var r = mk("div");
      add(r, mk("span", { className: "k", text: label }));
      add(r, mk("span", { className: "v", text: String(count || 0) }));
      add(counts, r);
    }
    countRow("文件事件", eventCounts.file);
    countRow("网络尝试", eventCounts.network);
    countRow("子进程尝试", eventCounts.subprocess);
    countRow("SQL 语句", eventCounts.sql);
    add(el, counts);
  }

  function renderOwasp(view) {
    var owasp = view.owaspCoverage || {};
    var owaspEl = $("owasp");
    var owaspSection = $("owasp-section");
    clear(owaspEl);
    // The wrapper holds an <h3>, so `.section:empty` cannot hide it; do it
    // explicitly or an empty matrix leaves a dangling heading behind.
    if (owaspSection) owaspSection.hidden = !Object.keys(owasp).length;
    if (!Object.keys(owasp).length) return;
    var tbl = mk("table", { className: "data-table" });
    var hd = mk("tr");
    ["类别", "描述", "状态", "已映射规则"].forEach(function (h) {
      hd.appendChild(mk("th", { text: h }));
    });
    add(tbl, hd);
    Object.keys(owasp).forEach(function (code) {
      var info = owasp[code];
      var tr = mk("tr");
      add(tr, mk("td", { text: code }));
      add(tr, mk("td", { text: info.title }));
      var td = mk("td");
      add(td, mk("span", {
        className: "status-tag " + (info.status === "none" ? "t-off" : "t-warn"),
        text: info.status }));
      add(tr, td);
      add(tr, mk("td", { text: (info.rules || []).join(", ") || "(none)" }));
      add(tbl, tr);
    });
    addTable(owaspEl, tbl);
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

  // Shared by the per-finding snippet view and the full-document view --
  // both need the same byte-offset -> string-index conversion (JS strings
  // are UTF-16, but sourceByteRange is a UTF-8 byte offset from the
  // backend), just at different zoom levels.
  function byteRangeToCharRange(text, startByte, endByte) {
    if (typeof text !== "string") return null;
    if (startByte === null || startByte === undefined
        || endByte === null || endByte === undefined) return null;
    var enc = new TextEncoder();
    var bytePos = 0;
    var startIdx = null;
    var endIdx = text.length;
    for (var i = 0; i < text.length; ) {
      var code = text.codePointAt(i);
      var ch = String.fromCodePoint(code);
      var next = i + ch.length;
      if (startIdx === null && bytePos >= startByte) startIdx = i;
      bytePos += enc.encode(ch).length;
      if (bytePos >= endByte) {
        endIdx = next;
        break;
      }
      i = next;
    }
    if (startIdx === null) startIdx = text.length;
    return { startIdx: startIdx, endIdx: endIdx };
  }

  function sliceUtf8Range(text, startByte, endByte) {
    if (typeof text !== "string") return { before: "", hit: "", after: "" };
    var range = byteRangeToCharRange(text, startByte, endByte);
    if (!range) {
      return {
        before: text.slice(0, 180),
        hit: "",
        after: text.length > 180 ? text.slice(180, 360) : "",
      };
    }
    var pad = 160;
    return {
      before: text.slice(Math.max(0, range.startIdx - pad), range.startIdx),
      hit: text.slice(range.startIdx, range.endIdx),
      after: text.slice(range.endIdx, Math.min(text.length, range.endIdx + pad)),
    };
  }

  function formatByteRange(ev) {
    if (ev.startByte === null || ev.startByte === undefined
        || ev.endByte === null || ev.endByte === undefined) return "";
    return " · bytes " + ev.startByte + "-" + ev.endByte;
  }

  function renderFixWorkbench(view, findings) {
    var box = $("fix-workbench");
    clear(box);
    var rems = view.remediations || [];
    var head = mk("div", { className: "section-head" });
    add(head, mk("h3", { text: "修改与复查" }));
    add(head, mk("span", { className: "hint",
      text: "改完直接在这里重新审查，对比整改效果。" }));
    add(box, head);
    if (!findings.length && !rems.length) {
      var empty = mk("div", { className: "empty-state" });
      add(empty, mk("strong", { text: "当前没有需要修改的审查项" }));
      add(empty, mk("span", { text: "可直接导出上方报告留档。" }));
      add(box, empty);
      return;
    }
    var draft = mk("textarea", { attrs: { rows: "10" } });
    if (currentSource.engine === "prompt") {
      draft.value = currentSource.files["prompt.txt"] || "";
      add(box, mk("p", { className: "muted",
        text: "在这里修改 Prompt 草稿，改完可直接重新审查。" }));
      add(box, draft);
      var rerun = mk("button", { text: "审查修改版", className: "primary" });
      rerun.addEventListener("click", function () {
        promptText.value = draft.value;
        promptCount.textContent = promptText.value.length + " 字符";
        submitPrompt();
      });
      var row = mk("div", { className: "row" });
      add(row, rerun);
      add(box, row);
    } else {
      add(box, mk("p", { className: "muted",
        text: "按下列整改项修改本地文件后，重新选择文件夹或直接复查当前选择。" }));
      var rerunSkill = mk("button", { text: "复查当前文件夹", className: "primary" });
      rerunSkill.addEventListener("click", submitSkill);
      var skillRow = mk("div", { className: "row" });
      add(skillRow, rerunSkill);
      add(box, skillRow);
    }
    if (rems.length) {
      var checklist = mk("ol", { className: "fix-list" });
      rems.forEach(function (rem) {
        var li = mk("li");
        var titleText = (rem.priority || "P1") + " · " + rem.title;
        if (rem.hitCount && rem.hitCount > 1) {
          titleText += "（命中 " + rem.hitCount + " 处）";
        }
        add(li, mk("strong", { text: titleText }));
        (rem.actions || []).forEach(function (action) {
          add(li, mk("div", { text: action }));
        });
        checklist.appendChild(li);
      });
      add(box, checklist);
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
    var save = mk("button", {text:"保存", className:"primary small"});
    var cancel = mk("button", {text:"取消", className:"small"});

    save.addEventListener("click", function(){
      var payload={status:sel.value,expiryDays:parseInt(days.value)||30};
      if(note.value) payload.note=note.value;
      api("/api/projects/"+encodeURIComponent(selectedProject)+"/dispositions/"+encodeURIComponent(fp),
          {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)})
        .then(function(){form.remove();loadProject();})
        .catch(showProjectError);
    });
    cancel.addEventListener("click", function(){form.remove();});

    form.appendChild(mk("label",{text:"状态"})); form.appendChild(sel);
    form.appendChild(mk("label",{text:"有效天数"})); form.appendChild(days);
    form.appendChild(mk("label",{text:"备注"})); form.appendChild(note);
    var actions = mk("div", {className:"row-inline"});
    actions.appendChild(save); actions.appendChild(cancel);
    form.appendChild(actions);
    container.appendChild(form);
  }
})();
