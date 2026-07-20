<!--
Snapshot of the "mature project reuse decision table" as of the same
round-8 snapshot. See ENGINEERING_SPEC-v0.3.md for the snapshot rules.
-->

# Verity 成熟项目复用决策表 v0.2

> 日期：2026-07-18
> 变更说明：v0.1 只调研了"专门针对 Skill/Prompt 审查"的项目（SkillSpector、Cisco Skill Scanner 等）。
> 本版补上两类此前完全缺失的调研对象：
> 1. 通用安全领域最权威的基础组件（Secret 扫描、YARA、JSON Schema、SARIF、Python 安全静态分析）——这些比任何 Skill 专用工具都更成熟、被验证时间更长；
> 2. 权威风险分类标准（OWASP Agentic Skills Top 10）——用于校验我们的规则覆盖面是否完整，而不是自己拍脑袋定分类。
> 原则：不追求穷尽，每个能力方向只挑当前最权威/最多人验证的 1-2 个，先能开工，后续替换不难。

---

## 一句话结论

> Verity 要做的事：用户提交 Prompt/System Prompt/Skill → 系统检查出问题 → 给出证据和修改建议。
> 这份清单回答：这件事里的每一块"零件"，市面上已经有哪个最权威的现成方案，我们直接拿来用，不用自己发明。

---

## 1. 最底层的通用安全组件（新增，此前完全没调研）

这些不是"审Skill的工具"，是任何安全扫描工具都会用到的基础零件，成熟度和验证时长远超Skill专用工具。

| 能力 | 最权威项目 | Star数 | 许可证 | 复用方式 | 说明 |
|---|---|---:|---|---|---|
| **Secret/密钥检测** | gitleaks/gitleaks | 28,190 | MIT | 直接复用规则集 | 目前最广泛使用的开源密钥扫描器，规则库覆盖数百种密钥格式（AWS/GCP/Azure/GitHub/Slack等），持续更新。这比我们自己维护一份API Key正则清单可靠得多 |
| **Secret检测（备选/交叉验证）** | trufflesecurity/trufflehog | 27,086 | AGPL-3.0 | 仅思想参考，不直接引入代码 | 星数和gitleaks接近，但AGPL许可证限制较强（衍生作品需开源），如果不想承担这个义务，只借鉴它"验证密钥是否真实有效"的思路（它会尝试用检测到的Key做只读API调用验证真伪），不直接用它的代码 |
| **恶意代码签名匹配** | VirusTotal/yara | 9,760 | BSD-3-Clause | 直接复用规则引擎 | YARA是恶意软件检测行业标准，SkillSpector自己也内置了YARA规则。这是"检测已知恶意模式"最权威的引擎，不需要自己发明一套模式匹配语法 |
| **JSON Schema校验** | ajv-validator/ajv（如走TS/JS）或 python-jsonschema/jsonschema（如走Python） | 14,770 / 4,962 | MIT / MIT | 直接复用 | 校验LLM返回的结构化输出、Rule Registry定义、PatchSet格式都需要这个。这是两个生态里事实标准，没有必要自己写Schema校验器 |
| **通用静态代码安全分析** | PyCQA/bandit（Python专用）+ returntocorp/semgrep（多语言） | 8,171 / 15,934 | Apache-2.0 / LGPL-2.1 | 直接复用规则思路，可考虑直接调用 | 这是我们此前完全没有对比过的对象。这两个是通用SAST（静态应用安全测试）领域最权威的工具，Skill里的脚本本质就是普通代码，可以直接用bandit/semgrep扫，不需要重新发明"怎么检测eval()/subprocess注入"这套东西 |
| **SARIF格式标准** | microsoft/sarif-sdk（官方规范实现） | 223 | 未标注 | 直接遵循规范，不一定引入代码库本身 | SARIF是Static Analysis Results Interchange Format的微软官方参考实现，是行业标准格式本身（不是热门项目，但是权威源头），bandit/semgrep/SkillSpector/Cisco都支持输出这个格式，Verity也应该原生支持，保证能接入用户现有的CI流水线（比如GitHub Code Scanning） |

**关键结论**：这一层的发现直接推翻我们之前"重新参考Skill专用工具的Secret检测正则"这个计划。**正确做法是直接用gitleaks的规则库，而不是从SkillSpector里抄一份它自己维护的正则**——SkillSpector的Secret检测能力本身很可能也是参考或复用了gitleaks这类项目，我们应该找到源头，而不是抄一个二次转述版本。同理，Skill里的Python/Shell/JS脚本本身就是普通代码，第一层安全检查应该直接调用bandit（Python）和semgrep（多语言）这类成熟SAST工具，Skill专用逻辑只需要在这基础上补充"和Skill/Agent特有的部分"（比如Manifest检查、声明-实现一致性），不需要重新造轮子去检测通用的代码漏洞模式。

---

## 2. Skill/Agent专用安全扫描（v0.1已覆盖，本版补充Benchmark对比）

| 能力 | 项目 | Star数 | 许可证 | 复用方式 | 说明 |
|---|---|---:|---|---|---|
| Skill包多维度安全扫描 | NVIDIA/SkillSpector | 13,283 | Apache-2.0 | 直接复用规则 | 68种检测模式，17类威胁，工程质量高，已有Docker/CI集成 |
| Skill包安全扫描（企业级） | Cisco Skill Scanner | 2,358 | Apache-2.0 | 直接复用规则+架构思路 | 多Analyzer架构（静态+行为+LLM+Meta），文档最完整 |
| **Agent代码专用安全审计（本版新增）** | HeadyZhang/agent-audit | 199 | MIT | 借鉴改写，重点参考其评测方法论 | 专门针对`@tool`装饰器污点追踪、MCP配置审计；公开了对比Bandit/Semgrep的Benchmark（F1 0.778 vs Bandit的0.458），方法论（oracle-based评测+file/line匹配容差+专门的噪音数据集验证误报率）值得直接照抄用于Verity自己的Phase 0评测设计 |
| 资产发现与威胁分类 | Snyk Agent Scan | 2,787 | Apache-2.0 | 仅思想参考 | 定位是"发现本机已装的Agent/Skill"，和Verity"用户主动提交审查"场景不同，V1不需要 |

---

## 3. 权威风险分类标准（新增，此前完全缺失）

| 标准 | 来源 | 状态 | 复用方式 | 说明 |
|---|---|---|---|---|
| **OWASP Agentic Skills Top 10 (AST10)** | OWASP官方项目 | Public Review阶段（v1草案） | **直接采用其10大风险分类作为Verity Skill规则的分类框架** | 覆盖：恶意Skill、供应链风险、过度授权、不安全元数据、不可信外部指令、隔离薄弱、更新漂移、扫描能力不足、无治理、跨平台复用风险。这比我们自己发明分类法更有说服力，也方便未来对外宣称"覆盖OWASP AST10" |
| OWASP AST10 "通用Skill格式提案" | 同上 | 提案阶段，非既成标准 | 仅思想参考，不作为输入格式标准 | 字段设计思路值得借鉴（`risk_tier`分级、`network.allow`域名白名单而非布尔开关、`deny_write`保护身份文件），但目前是OWASP自己的一个建议稿，不是被广泛采用的实际格式，不能当作Verity必须兼容的输入标准 |
| OWASP Top 10 for LLM Applications | OWASP官方项目 | 已发布，广泛引用 | 用于校验Prompt/System Prompt规则覆盖面 | Prompt Injection、Excessive Agency、Insecure Plugin Design等条目，用于交叉核对Verity的Prompt Auditor规则是否有遗漏 |

---

## 4. 已被验证"会被绕过"的真实教训（新增，极其重要）

| 来源 | 核心结论 | 对Verity的意义 |
|---|---|---|
| Trail of Bits《The Sorry State of Skill Distribution》（2026年6月） | 测试的所有公开Skill扫描器（ClawHub的VirusTotal+LLM guard、**Cisco的skill-scanner**、skills.sh的扫描器）均在一小时内被绕过，手段包括：填充内容迫使截断、恶意逻辑藏进二进制/压缩包、直接对扫描器自己的LLM判断模型做Prompt注入 | 这不是理论风险，是对我们已经准备直接复用的Cisco Skill Scanner的真实攻破记录。证实了Verity工程规格v0.3里已经写的§7.2（验证器输出隔离）、§13.1（不递归解包嵌套archive）、§18.1（供应链完整性）不是过度设计，而是复用Cisco规则时必须叠加的额外防护层，不能原样照抄它的架构 |

---

## 5. Prompt测试/运行时评测（v0.1已覆盖，维持不变）

| 能力 | 项目 | 复用方式 |
|---|---|---|
| 测试案例×断言Schema | promptfoo | 借鉴改写，V1.5阶段使用 |
| Probe与Detector分离 | garak | 仅思想参考，V2阶段使用 |

---

## 6. 明确不采用/需要额外加固后才能用

| 能力/做法 | 来源 | 处理方式 |
|---|---|---|
| LLM Meta Analyzer直接过滤已确认的静态发现 | SkillSpector/Cisco | 不采用，与Verity §7.4铁律冲突 |
| 直接照抄Cisco Skill Scanner架构不做额外加固 | Cisco | **不可以**，见上方Trail of Bits绕过记录，必须叠加Verity自己的防护层 |
| 把AST10提案格式当作既成输入标准 | OWASP AST10 | 不采用，只借鉴思路 |

---

## 7. 修正后的Phase 3技术路线（重要变化）

v0.1原计划：Skill脚本安全检测主要参考SkillSpector/Cisco的规则重写。

**v0.2修正**：

```text
第一层（通用代码安全）：直接调用/集成 bandit（Python脚本）+ semgrep（多语言脚本）
                        + gitleaks（密钥检测）+ YARA（已知恶意模式）
                        这四个是各自领域最权威的现成工具，优先直接调用而不是重写规则

第二层（Skill/Agent专有）：参考 SkillSpector + Cisco 的架构思路，
                          但只实现"通用工具覆盖不到"的部分：
                          - Manifest/Frontmatter规范检查
                          - 声明-实现一致性对比（Declared vs Observed）
                          - Skill触发条件质量
                          - 跨文件引用完整性

第三层（Verity自己的增量创新）：
                          - Coverage可见化（计划vs执行对账）
                          - Evidence-first + 精确去重
                          - Candidate/Validator分离且互相不能夹带新问题
                          - 按OWASP AST10分类组织规则，方便对外核对覆盖率
```

这个分层比v0.1"参考SkillSpector重写一遍类似规则"更省力，也更可靠——直接用bandit/semgrep这种被全球无数项目验证过的工具，比我们自己（或抄SkillSpector）重新写一套Python/JS的代码安全检测规则风险更低。

---

## 8. 下一步

1. Phase 3开工前，先验证bandit/semgrep能否以库或子进程方式集成进我们的技术栈（Python Core），预计可行性很高，因为都是Python生态原生工具。
2. gitleaks是Go写的，需要确认是走"调用其CLI二进制"还是"移植其规则库到Python里自己实现匹配"，两种都可行，前者更省力但增加一个外部依赖。
3. 按OWASP AST10的10个分类，重新过一遍Verity现有的Skill规则草案，标出每个分类目前"有覆盖/暂缺"，缺口留作Phase 3待办。
4. 每次调研有新发现，继续更新本表并记录进CHANGELOG。
