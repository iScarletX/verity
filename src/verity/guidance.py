"""Controlled remediation catalog.

Human-readable Chinese guidance for every built-in Rule / FindingType /
Bandit test_id / gitleaks ruleID. Keys are strictly controlled — the
guidance TEXT never contributes to Finding identity, subjectKey or
fingerprint (spec §5, §8). Lookup is opaque; unknown ids fall back to a
neutral "please review manually" entry.

Priority levels:
    P0  — deal with first (structural high/critical, credential leak,
          direct RCE-shaped pattern)
    P1  — deal with soon (unpinned deps, wildcard perms, unfilled
          placeholders, weak crypto)
    P2  — nice to fix (metadata gaps, low-signal risk markers)

None of these entries auto-apply anything. PatchSet remains proposal-only.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class Guidance:
    id: str
    plainTitle: str
    whyItMatters: str
    whatToDo: List[str]
    priority: str            # P0 / P1 / P2
    referenceUrl: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------- #
# Rule / FindingType level guidance                                     #
# --------------------------------------------------------------------- #

_RULE_GUIDANCE: Dict[str, Guidance] = {

    # Prompt engine ----------------------------------------------------
    "prompt.instruction_override_marker": Guidance(
        id="prompt.instruction_override_marker",
        plainTitle="Prompt 中出现指令覆盖标记",
        whyItMatters=(
            "像“ignore all previous instructions”这样的短语是常见的提示词注入模板。"
            "即使这里只是引用示例，模型仍可能被诱导按字面执行。"
        ),
        whatToDo=[
            "如果这段话是攻击示例，用代码围栏 ``` ``` 或引号明确包裹为“不可执行的引用”。",
            "如果不是引用，删除或改写这段话。",
            "系统提示词中不要出现“忽略先前指令”类模板。",
        ],
        priority="P2",
    ),
    "prompt.unfilled_placeholder": Guidance(
        id="prompt.unfilled_placeholder",
        plainTitle="Prompt 里疑似有未填充的占位符",
        whyItMatters=(
            "像 {{ topic }}、${VAR}、[INSERT ...]、<TODO> 这类占位符如果没被替换，"
            "模型会把它们当作字面字符输出，回答就会跑偏。"
        ),
        whatToDo=[
            "确认每个占位符都由外部数据替换，而不是留在最终 prompt 里。",
            "如果只是示例说明，放到代码围栏或明确加“示例：”前缀避免误判。",
        ],
        priority="P1",
    ),
    "prompt.system_hardcoded_secret": Guidance(
        id="prompt.system_hardcoded_secret",
        plainTitle="系统提示词里疑似写死了凭据",
        whyItMatters=(
            "写在 system prompt 里的密钥可以被任何跟模型对话的人直接问出来，"
            "属于直接可提取的凭据泄露。"
        ),
        whatToDo=[
            "立即撤销/轮换该凭据。",
            "改从环境变量或 Secret Manager 注入，不要把值写进 prompt。",
            "检查历史日志/提交记录，确认没有更多副本。",
        ],
        priority="P0",
    ),
    "prompt.duplicate_numeric_assignment": Guidance(
        id="prompt.duplicate_numeric_assignment",
        plainTitle="同一个参数键出现了不同的数值",
        whyItMatters=(
            "同一个键（例如 temperature）出现两次却给了不同数字，会让读者/调用方"
            "无法判断哪个值生效，是明显的一致性问题。"
        ),
        whatToDo=[
            "只保留一处赋值，删除或合并另一处。",
            "如果确实需要两处，改成不同的键名（例如 temperature_generation / temperature_review）。",
        ],
        priority="P1",
    ),
    "prompt.control_character": Guidance(
        id="prompt.control_character",
        plainTitle="Prompt 内出现控制字符 / Unicode 双向覆盖字符",
        whyItMatters=(
            "ASCII 控制字符通常是复制粘贴的意外；Unicode 双向覆盖（U+202A-2E 等）"
            "是有据可查的提示词注入手段。"
        ),
        whatToDo=[
            "打开原文用支持 Unicode 可视化的编辑器查看这段字节。",
            "如无必要就删除；如果确需 bidi 显示，改为纯 HTML/CSS 排版而不是在文本里插入控制符。",
        ],
        priority="P1",
    ),
    "prompt.empty_or_whitespace": Guidance(
        id="prompt.empty_or_whitespace",
        plainTitle="Prompt 为空或全是空白",
        whyItMatters="空 prompt 通常是上游流程遗漏字段导致的。",
        whatToDo=[
            "补齐必要的指令文本再提交审查。",
        ],
        priority="P2",
    ),
    "prompt.open_ended_tool_wildcard": Guidance(
        id="prompt.open_ended_tool_wildcard",
        plainTitle="系统提示词以通配符授予所有工具",
        whyItMatters=(
            "`allowed_tools: *` / `permissions: [\"*\"]` 一次性给模型所有工具的调用权，"
            "违反最小权限原则；出问题时无法定位到具体工具。"
        ),
        whatToDo=[
            "把星号替换为你确实需要用到的工具名清单，例如 [\"search\", \"read_file\"]。",
            "对文件系统类工具，进一步限制到具体路径前缀。",
        ],
        priority="P0",
    ),

    # Skill engine — manifest / metadata ------------------------------
    "skill.manifest_issue": Guidance(
        id="skill.manifest_issue",
        plainTitle="缺少 SKILL.md",
        whyItMatters=(
            "SKILL.md 是 Verity 唯一可读的机器元数据来源；缺少它意味着依赖 manifest "
            "的所有检查都无法完成，也无法向用户呈现 name/description。"
        ),
        whatToDo=[
            "在 skill 根目录添加一个 SKILL.md，至少写清 name、description、version。",
            "把 scripts / dependencies / permissions 写成结构化 YAML frontmatter。",
        ],
        priority="P0",
    ),
    "skill.manifest_parse_failure": Guidance(
        id="skill.manifest_parse_failure",
        plainTitle="SKILL.md frontmatter 解析失败",
        whyItMatters=(
            "frontmatter 有语法错误或超出安全预算，Verity 拒绝按它做决定；"
            "依赖 manifest 的检查已被判为未完成。"
        ),
        whatToDo=[
            "用 YAML 校验器（例如 yamllint）确认 --- 之间是合法 YAML。",
            "根节点必须是 mapping，而不是 list 或纯字符串。",
            "如果确实需要大 frontmatter，请拆分成多个字段而不是巨型 YAML。",
        ],
        priority="P0",
    ),
    "skill.manifest_field_issue": Guidance(
        id="skill.manifest_field_issue",
        plainTitle="Manifest 中的 name / description 缺失或不合规",
        whyItMatters=(
            "缺 name/description 会让下游平台无法正确展示这个 skill；"
            "非法字符的 name 也可能在 URL / 目录名场景下引发问题。"
        ),
        whatToDo=[
            "补齐 name（[A-Za-z0-9][A-Za-z0-9._- ]{0,62}[A-Za-z0-9]）与 description。",
            "description 用一句话说清 skill 的能力和边界。",
        ],
        priority="P1",
    ),
    "skill.manifest_reference_issue": Guidance(
        id="skill.manifest_reference_issue",
        plainTitle="Manifest 引用的文件不存在 / 不安全",
        whyItMatters=(
            "manifest 声明的脚本或文件如果不存在，或用了绝对路径 / `..` 越界，"
            "运行时会失败或指向 skill 目录之外的内容。"
        ),
        whatToDo=[
            "把引用改为相对于 SKILL.md 所在目录的路径，例如 `scripts/run.py`。",
            "补齐缺失文件，或从 manifest 里移除不再存在的引用。",
            "禁止使用 `/`、`..` 或反斜杠。",
        ],
        priority="P1",
    ),
    "skill.manifest_dependency_issue": Guidance(
        id="skill.manifest_dependency_issue",
        plainTitle="Manifest 依赖版本未固定",
        whyItMatters=(
            "`latest` / `>=X` / 空版本会导致不同机器上装到不同版本，"
            "破坏可复现性，也让供应链攻击更难排查。"
        ),
        whatToDo=[
            "把版本改成精确值，例如 `1.2.3` 或 `==1.2.3`。",
            "如果确需允许小版本，在项目侧用 lock 文件锁定实际安装版本。",
        ],
        priority="P1",
    ),
    "skill.manifest_permission_wildcard": Guidance(
        id="skill.manifest_permission_wildcard",
        plainTitle="Manifest 权限使用了通配符 / 根路径",
        whyItMatters=(
            "`*` / `/` / `**` 会让 skill 拿到几乎无限制的访问权，属于严重的最小权限违规。"
        ),
        whatToDo=[
            "写出确实需要的具体权限，例如 `read:./data`、`net:api.example.com`。",
            "对文件系统权限，写到目录/前缀，而不是根 `/`。",
        ],
        priority="P0",
    ),
    "skill.manifest_external_instructions": Guidance(
        id="skill.manifest_external_instructions",
        plainTitle="Manifest 声明从外部 URL 拉取运行时指令",
        whyItMatters=(
            "`fetch_and_follow` / `runtime_fetch` 模式会在运行时下载并遵循远端指令，"
            "远端一旦被劫持，skill 的行为随之被劫持（OWASP AST05）。"
        ),
        whatToDo=[
            "尽量把指令内联到 skill 内部，不做运行时拉取。",
            "若必须远程拉取，改为受签名/hash 校验的静态资源，并把校验策略写清。",
        ],
        priority="P0",
    ),
    "skill.python_subprocess_shell_true": Guidance(
        id="skill.python_subprocess_shell_true",
        plainTitle="Python 代码里用 subprocess.*(shell=True)",
        whyItMatters=(
            "`shell=True` 会把参数交给 shell 拼接，一旦拼进用户/网络数据就是命令注入。"
        ),
        whatToDo=[
            "改成参数列表形式：subprocess.run([\"cmd\", \"arg1\", \"arg2\"])。",
            "禁止用字符串拼接生成命令。",
        ],
        priority="P0",
    ),
    "skill.fake_secret_fixture": Guidance(
        id="skill.fake_secret_fixture",
        plainTitle="Skill 中出现 Verity 合成 Secret 占位符",
        whyItMatters=(
            "这是 Verity 自己测试用的占位符，不是真实密钥；但代码里出现该字面量说明"
            "开发者可能把测试凭据推到生产文件。"
        ),
        whatToDo=[
            "从生产代码 / manifest 里删除该占位符，改从测试环境注入。",
        ],
        priority="P1",
    ),
    "skill.dangerous_shell_pattern": Guidance(
        id="skill.dangerous_shell_pattern",
        plainTitle="Skill 内出现危险 Shell 模式（如 `curl | sh`、`rm -rf /`）",
        whyItMatters=(
            "远端脚本管道执行、rm -rf /、fork bomb 都是历史上导致真实事故的模式。"
            "Verity 只是文本级检测，并没有执行它们。"
        ),
        whatToDo=[
            "改为下载 -> 校验 SHA-256 -> 再执行的三段流程。",
            "对本地删除命令加白名单目录约束。",
        ],
        priority="P0",
    ),

    # Aggregation entries (dispatched below) --------------------------
    # skill.bandit_finding is per-testId; handled dynamically.
    # skill.gitleaks_finding is per-ruleID; handled dynamically.
}


# --------------------------------------------------------------------- #
# Bandit per-testId guidance                                            #
# --------------------------------------------------------------------- #

_BANDIT_GUIDANCE: Dict[str, Guidance] = {
    "B102": Guidance(
        id="skill.bandit.B102",
        plainTitle="使用了 exec() 执行动态代码",
        whyItMatters="exec() 直接执行字符串，被拼入外部输入即可远程代码执行。",
        whatToDo=[
            "把动态执行换成显式的函数调度表 / dispatch dict。",
            "若确需 DSL，用受限解释器或已知安全的 sandbox 库。",
        ],
        priority="P0",
    ),
    "B105": Guidance(
        id="skill.bandit.B105",
        plainTitle="可能的硬编码密码字符串",
        whyItMatters="源码里带出去的常量密码，属于典型凭据泄露。",
        whatToDo=[
            "从代码中删除，改用环境变量 / Secret Manager 注入。",
            "凭据已经曝光，请撤销并轮换。",
        ],
        priority="P0",
    ),
    "B106": Guidance(
        id="skill.bandit.B106",
        plainTitle="函数默认参数里带了硬编码密码",
        whyItMatters="调用方以为“不给就是空”，实际会拿到源码里的默认值。",
        whatToDo=[
            "把默认值改成 None，函数内部再从配置/环境读取实际值。",
        ],
        priority="P0",
    ),
    "B107": Guidance(
        id="skill.bandit.B107",
        plainTitle="函数参数默认值包含硬编码密码",
        whyItMatters="与 B106 类似，通过默认参数把凭据固化进代码。",
        whatToDo=[
            "把默认值改成 None 或抛异常，强制调用方注入值。",
        ],
        priority="P0",
    ),
    "B301": Guidance(
        id="skill.bandit.B301",
        plainTitle="使用 pickle 加载可能不可信数据",
        whyItMatters="pickle.load 允许构造任意对象；反序列化外部数据即可远程代码执行。",
        whatToDo=[
            "改用 JSON / msgpack 等无可执行语义的格式。",
            "若必须用 pickle，仅接受来自可信、签名校验的字节。",
        ],
        priority="P0",
    ),
    "B303": Guidance(
        id="skill.bandit.B303",
        plainTitle="使用了 MD5/SHA-1 等已过时的哈希算法",
        whyItMatters="MD5 / SHA-1 已被公认为可碰撞，不适合安全场景。",
        whatToDo=[
            "改用 SHA-256 / SHA-3。",
            "若只是做文件去重非安全用途，明确注释“非密码学场景”。",
        ],
        priority="P1",
    ),
    "B310": Guidance(
        id="skill.bandit.B310",
        plainTitle="urllib.urlopen 打开可能包含 file:// 等不可信协议的 URL",
        whyItMatters="用户可控的 URL 若不校验协议，可能读到本地文件甚至内网服务。",
        whatToDo=[
            "先解析 URL，白名单只允许 http/https。",
            "对内网 IP、127.0.0.1、file://、ftp:// 显式拒绝。",
        ],
        priority="P1",
    ),
    "B506": Guidance(
        id="skill.bandit.B506",
        plainTitle="使用 yaml.load 而未指定 SafeLoader",
        whyItMatters="默认的 yaml.load 会实例化任意对象，是历史知名的 RCE 面。",
        whatToDo=[
            "改成 yaml.safe_load(...) 或 yaml.load(..., Loader=yaml.SafeLoader)。",
        ],
        priority="P0",
    ),
    "B602": Guidance(
        id="skill.bandit.B602",
        plainTitle="subprocess 调用启用了 shell=True",
        whyItMatters="shell=True 会走 shell 解析器；一旦命令字符串包含用户输入就是命令注入。",
        whatToDo=[
            "改成参数列表：subprocess.run([\"cmd\", \"arg\"]).",
            "如需管道，拆成多个 subprocess 手动串接。",
        ],
        priority="P0",
    ),
    "B605": Guidance(
        id="skill.bandit.B605",
        plainTitle="使用 os.system() 执行 shell 命令",
        whyItMatters="os.system 等于 shell=True，且没有返回码之外的输出捕获，风险类似 B602。",
        whatToDo=[
            "换成 subprocess.run(..., shell=False) 并捕获输出。",
        ],
        priority="P0",
    ),
    "B607": Guidance(
        id="skill.bandit.B607",
        plainTitle="子进程使用了不完整的可执行文件路径",
        whyItMatters=(
            "只给 “echo”/“ls” 这类名字依赖 PATH 查找；恶意 PATH 前缀可能让 skill "
            "拿到不同的二进制。"
        ),
        whatToDo=[
            "给出绝对路径，例如 /usr/bin/echo。",
            "或先用 shutil.which 校验路径来源。",
        ],
        priority="P1",
    ),
    "B701": Guidance(
        id="skill.bandit.B701",
        plainTitle="Jinja2 模板关闭了自动转义",
        whyItMatters="autoescape=False 时模板会把用户输入原样输出到 HTML，是典型 XSS 面。",
        whatToDo=[
            "启用 autoescape=True，或使用 select_autoescape。",
            "确实需要原样输出的字段，用 Markup(...) 明确圈定。",
        ],
        priority="P1",
    ),
}


_BANDIT_FALLBACK = Guidance(
    id="skill.bandit_finding",
    plainTitle="Bandit 静态分析报告了一个可疑代码模式",
    whyItMatters="Bandit 的具体规则未被 Verity 单独收录建议；请人工查看该规则含义。",
    whatToDo=[
        "在 Bandit 官方文档中查找对应 test_id 的说明。",
        "确认这段代码是否符合业务上下文的最小权限 / 输入过滤要求。",
    ],
    priority="P1",
    referenceUrl="https://bandit.readthedocs.io/",
)


# --------------------------------------------------------------------- #
# Gitleaks per-ruleID guidance                                          #
# --------------------------------------------------------------------- #

_GITLEAKS_DEFAULT = Guidance(
    id="skill.gitleaks_finding",
    plainTitle="gitleaks 在 skill 里发现了疑似真实凭据",
    whyItMatters=(
        "凭据一旦进入版本控制、构建产物或 skill 分发包，就必须视为已泄露："
        "任何拿到这份 skill 的人都能取到该值。"
    ),
    whatToDo=[
        "立即撤销/轮换这条凭据。",
        "改从环境变量或 Secret Manager 注入，不要把值写进 skill 内。",
        "回溯该文件的历史记录（git log、构建缓存、备份），确认没有更多副本。",
    ],
    priority="P0",
    referenceUrl="https://cwe.mitre.org/data/definitions/798.html",
)


# --------------------------------------------------------------------- #
# Fallback                                                              #
# --------------------------------------------------------------------- #

_FALLBACK = Guidance(
    id="unknown",
    plainTitle="Verity 未内置针对该规则的具体建议",
    whyItMatters="该规则的具体含义需要人工复核。",
    whatToDo=[
        "查阅规则名称对应的官方文档。",
        "对照 Finding 提供的证据（相对路径 + byte range）逐条评估。",
        "如需进一步支持，暂缓上线并升级到人工审计。",
    ],
    priority="P1",
)


# --------------------------------------------------------------------- #
# Public API                                                             #
# --------------------------------------------------------------------- #

def lookup(finding: Dict[str, Any]) -> Dict[str, Any]:
    """Given a Finding dict (as produced by ``report.review_to_dict``),
    return the guidance record for it. Never raises."""
    ftype = finding.get("findingType")
    subject = finding.get("subject") or {}
    if ftype == "skill.bandit_finding":
        tid = subject.get("testId")
        g = _BANDIT_GUIDANCE.get(tid, _BANDIT_FALLBACK)
        return g.as_dict()
    if ftype == "skill.gitleaks_finding":
        # For now we use a single guidance for all gitleaks rules; the
        # ruleID variability is captured in the plainTitle / subject.
        return _GITLEAKS_DEFAULT.as_dict()
    g = _RULE_GUIDANCE.get(ftype, _FALLBACK)
    return g.as_dict()


def catalog_keys() -> Dict[str, List[str]]:
    """Diagnostic: enumerate every guidance key currently registered."""
    return {
        "findingTypes": sorted(_RULE_GUIDANCE.keys()),
        "banditTestIds": sorted(_BANDIT_GUIDANCE.keys()),
        "gitleaks": ["default"],
        "fallbacks": ["_FALLBACK", "_BANDIT_FALLBACK", "_GITLEAKS_DEFAULT"],
    }


def next_steps_summary(view_findings: List[Dict[str, Any]],
                       coverage_status: str,
                       secret_scan_status: str) -> Dict[str, Any]:
    """Compute a next-step summary from structured counts (no LLM).

    Order (highest priority first):
      1. P0 findings
      2. Coverage-insufficient blockers (P0 for the reviewer)
      3. Secret scan not run (informational only when other work is queued)
      4. P1 findings
      5. P2 findings
    """
    by_priority = {"P0": [], "P1": [], "P2": []}
    for f in view_findings:
        g = f.get("guidance") or {}
        prio = g.get("priority") or "P1"
        if prio not in by_priority:
            prio = "P1"
        by_priority[prio].append(g.get("plainTitle") or f.get("type") or "")

    steps: List[Dict[str, Any]] = []
    if by_priority["P0"]:
        steps.append({
            "code": "fix_p0",
            "label": (
                f"先处理 {len(by_priority['P0'])} 个 P0 高优先问题（"
                + "; ".join(by_priority["P0"][:2])
                + ("…" if len(by_priority["P0"]) > 2 else "")
                + "）"
            ),
        })
    if coverage_status != "sufficient":
        steps.append({
            "code": "close_coverage_gap",
            "label": "补齐未完成的检查（工具缺失 / 解析失败 / 权限不足）后重新审查",
        })
    if secret_scan_status not in ("completed", "not_applicable_engine"):
        steps.append({
            "code": "enable_secret_scan",
            "label": "启用完整的 Secret 扫描（切到 standard profile 或安装 gitleaks）",
        })
    if by_priority["P1"]:
        steps.append({
            "code": "fix_p1",
            "label": f"再处理 {len(by_priority['P1'])} 个 P1 待办",
        })
    if by_priority["P2"]:
        steps.append({
            "code": "fix_p2",
            "label": f"最后处理 {len(by_priority['P2'])} 个 P2 建议项",
        })
    if not steps:
        steps.append({
            "code": "monitor",
            "label": "本次已完成检查未发现阻断项；仍建议在生产前做一次真实运行验证",
        })
    return {
        "priorityCounts": {k: len(v) for k, v in by_priority.items()},
        "steps": steps,
    }
