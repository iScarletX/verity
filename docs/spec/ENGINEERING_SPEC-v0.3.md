<!--
Snapshot committed into this repository as the in-repo Single Source of Truth
for Verity's engineering spec.

- Snapshot date:      2026-07-20
- Snapshot source:    main-agent design docs (private, not part of this repo)
- Snapshot commit:    round-8 (verity semantic scaffold + fake-credential
                      literal split)
- If the upstream spec is revised, update THIS file in a dedicated commit
  and note the revision in docs/PROGRESS.md.

This file is content-authoritative for Verity's implementation. Anything
else in this repository that describes engineering intent must not
contradict it.
-->

# Verity · Prompt & Skill Auditor 工程规格 v0.3

> 日期：2026-07-18
> 状态：Phase 0 定稿依据；取代 v0.1
> 版本变更来源：
> - v0.1 → v0.2：外部架构审查《Verity架构审查与重写建议》，重写核心数据模型、去重、Provider 出境、Safe Intake、Baseline、PatchSet
> - v0.2 → v0.3：本轮追加 7 条审查意见 + 主 Agent 两轮独立复查发现的 19 项问题，全部采纳并落地为 Schema 约束
> 完整讨论记录见 `CHANGELOG.md`

---

## 0. 这一版解决的问题

v0.1 的问题不是方向错，是核心 Schema 存在会直接导致实现分叉的冲突：一个类型同时表示事实/规则命中/候选、去重范围未定义、身份分层缺失、语义层缺少防护。v0.3 把这些全部拆开、钉死，并把开发计划改为"按能力设门禁"，而不是要求一次性把所有设计冻结完才能动手。

**核心不变量（贯穿全文，任何设计不得违反）**：

1. Evidence-first：先有证据，再有问题。
2. Deterministic-first：能由代码确定的，禁止交给 LLM 定案。
3. Candidate 与 Validator 分离：提候选和验证候选必须是不同的执行、不同的职责边界。
4. 精确去重仅限同一 Snapshot 内、同一 Rule 的重复执行；跨版本对比是完全不同的机制。
5. 相关问题只建关系，不做破坏性合并。
6. High/Critical 的 deterministic Finding，任何 LLM 组件都无权过滤、降级或重新判断。
7. 未覆盖、失败、跳过必须可见，不能被解释为"没有问题"。
8. V1 不执行 Skill，不安装依赖，不启动未知服务。

---

## 1. 核心生命周期总览

```text
SourceReceipt
  → ArtifactSnapshot
  → RouteDecision
  → ReviewProfile + InitialReviewPlan
  → ParserRun[] + ArtifactModel
  → ExpandedReviewPlan（有深度上限）
  → ExecutionRecord[]
      ├─ EvidenceRecord[]
      └─ CoverageAssessment（对账 Plan vs Execution）
  → RuleMatchEvent[] ──────────────→ deterministic Finding（物理独立路径）
  → SemanticCandidate[]
      → ValidationRecord[]
      → CandidateAssessment
      → confirmed 才生成 semantic Finding（物理独立路径）
  → FindingRelation[]
  → BaselineComparison + DispositionRecord[]
  → ReviewDecision
  → ReportProjection
```

所有底层审计对象不可变、追加式保存。报告、排序、首页折叠只是投影，不能改写事实层。

---

## 2. 身份分层：Artifact / Snapshot / Review

不用单一模糊的 `contentHash`，区分三层：

```ts
interface SourceReceipt {
  sourceType: 'text' | 'file' | 'directory' | 'zip' | 'git'
  sourceEnvelopeDigest?: string   // 只有存在单一原始 Blob 时才有，如粘贴文本/单文件/ZIP 整体
  sanitizedLocator?: string       // 不含 token、用户名、query 的来源标识
  resolvedRevision?: string       // Git 完整 object ID（来源修订信息，不作为 Verity 安全摘要）
}

interface ArtifactSnapshot {
  artifactId: string              // 逻辑制品/版本谱系，长期存在
  snapshotId: string              // 这个逻辑对象的某一次不可变内容快照
  snapshotManifestDigest: string  // 完整摄取清单摘要，包括 rejected/skipped 条目
  contentRootDigest: string       // 实际进入分析的规范化内容树摘要
  digestAlgorithm: 'sha256'
  canonicalizationVersion: string
  files: ArtifactFile[]
}
```

- `artifactId`：长期逻辑对象（"这个 Skill 项目"）。
- `snapshotId`：这个逻辑对象的某个具体版本。
- `reviewId`：对某个 Snapshot 发起的一次审查（见 §9 ReviewRun）。

**V1 范围声明**：一次 Review 只对应一个 Artifact。用户一次提交包含多个 Skill 的目录，属于批量扫描，明确列为延期能力，不在 V1 实现。

### 2.1 内容摘要的准确含义

SHA-256 类摘要用于回答"在相同算法、相同字节表示和相同规范化版本下，当前内容是否与参考内容一致"，它检测不一致，不是数字签名，不证明来源、作者或时间。若需要抗篡改证明，需另外引入签名或可信审计日志。文档中一律使用"检测不一致"表述，不使用"证明未被篡改"。

### 2.2 文件树摘要必须无歧义序列化

不能用 `path + size + hash` 简单拼接（存在字段边界歧义）。必须使用带 domain separation 的规范序列化：

```text
snapshotManifestDigest = sha256(
  "verity:snapshot-manifest:v1"
  || canonicalSerialize(sortedEntries)
)
```

每个 Entry 至少包含：规范化相对路径、Entry 类型、大小、mode/executable 等安全属性、symlink target 字符串（不跟随）、included/skipped/rejected 结果及原因码。`contentDigest` 只对已通过安全限制、可安全读取的 Entry 必填；encrypted、超限、损坏条目不得为了计算摘要继续解压。

路径规范化必须版本化；出现重复路径、大小写碰撞、绝对路径、NUL、盘符或逃逸路径时直接拒绝，不采用 last-write-wins。

---

## 3. Parser 层

Parser 负责把原始内容转为结构化对象，不负责风险定案。Skill 包通常需要多个 Parser（Manifest、YAML frontmatter、Markdown、Shell、JS/TS、Python AST、依赖清单），因此每次调用单独记录：

```ts
interface ParserRun {
  parserRunId: string
  snapshotId: string
  parserId: string
  parserVersion: string
  grammarOrDialectVersion?: string
  configVersion: string
  configDigest: string
  inputFileIds: string[]
  outputModelType: string
  outputSchemaVersion: string
  status: 'completed' | 'partial' | 'failed' | 'unsupported'
  diagnostics: Diagnostic[]
  usedFallback: boolean
}
```

Parser 回答"Verity 怎样理解这份内容"；Snapshot 摘要回答"Verity 审的是哪个版本"，两者不能互相替代，也不混入 Artifact 内容身份。

**Parser 安全约束（进入 Phase 0 验收清单）**：限制编码/BOM/超长行、YAML alias bomb、极深 JSON/AST、Regex ReDoS、Parser 时间/递归/内存/输出节点数上限。Binary/unsupported 只记录有界 metadata 和摘要，并明确降低 Coverage。

---

## 4. Location：权威坐标系与 canonical 指纹序列化

### 4.1 Location 结构

行号、列号在不同编码/换行格式下会漂移，内部精确身份必须使用原始字节范围：

```ts
interface Location {
  fileId: string
  artifactPath: string
  fileDigest: string
  sourceEncoding: string
  sourceByteRange?: { start: number; end: number }   // 可能不存在，见 4.2
  sourceMapId?: string
  displayLineColumn?: {
    startLine: number
    startColumn: number
    endLine: number
    endColumn: number
  }
  structuralPath?: string
  locationSchemaVersion: string
}
```

line/column 只作展示派生；Parser 若构造规范化 UTF-8 视图，必须保存指回原始字节的 SourceMap；structuralPath 使用有版本的语法和转义规则。

### 4.2 canonical fingerprint serialization（Phase 0 必须冻结，问题#1 的解决方案）

**问题**：`precise_location` 缺失字段（比如只有 `structuralPath` 没有 `sourceByteRange`）如何编码进指纹，未定义时同一事件会因表示差异去重失败。

**规则**：

```text
canonicalLocation(loc) 的序列化必须遵循固定字段顺序和显式空值占位：

{
  "fileId": loc.fileId,
  "sourceByteRange": loc.sourceByteRange ?? "ABSENT",
  "structuralPath": loc.structuralPath ?? "ABSENT",
  "locationSchemaVersion": loc.locationSchemaVersion
}
```

- 字段顺序固定，不允许序列化器按对象 key 遍历顺序自由排列；
- 缺失字段必须显式写入占位符 `"ABSENT"`，不能直接省略该字段（省略 vs 写 null vs 写空字符串，三者在字节层面不同，必须唯一化）；
- 多个 Location（同一 Evidence 跨多处）时，`locations[]` 排序前必须先按固定 key（`fileId` 后 `sourceByteRange.start`）排序，再序列化，防止同一组 Location 因数组顺序不同产生不同指纹；
- 该序列化规则本身要有版本号（`locationSchemaVersion` 已经存在于 Location 里，序列化算法version 单独记为 `canonicalFingerprintSpecVersion`），变更需要走规则迁移，不能静默改变已发布指纹的计算方式。

**验收要求**：必须有反例测试，构造"同一事件但一次产出 byteRange、一次因 Parser 降级只产出 structuralPath"的场景，验证在明确定义下这两种表示是否应该判定为同一事件（默认判定为不同事件，因为证据种类不同；如果产品需要视为同一事件，必须显式声明等价规则，不能隐式相等）。

---

## 5. Evidence / RuleMatch：从 DetectionEvent 拆分（问题：职责重叠）

原设计让一个对象同时表示 Fact、Rule Match 和 Candidate，本版彻底拆开。

```ts
interface EvidenceRecord {
  evidenceId: string
  snapshotId: string
  kind:
    | 'source_span'
    | 'parsed_fact'
    | 'reference_path'
    | 'dataflow_path'
    | 'capability_observation'
  locations: Location[]
  sensitivity: 'normal' | 'sensitive' | 'secret'
  redactedPreview?: string
  occurrenceFingerprint: string
  producer: {
    componentId: string
    componentVersion: string
    executionId: string
  }
  derivedFromEvidenceIds: string[]
  identityPolicyId: string
  metadata: Record<string, unknown>   // 必须走 allowlist Schema，不能成为绕过脱敏的自由存储区
}

interface RuleMatchEvent {
  eventId: string
  snapshotId: string
  ruleId: string
  ruleVersion: string
  evidenceIds: string[]              // 只引用 Evidence，不重复保存证据文本/位置
  eventDedupKey: string
  executionId: string
}
```

约束：

- 没有 Rule 的普通 Fact（比如"这个包含3个脚本"这类中性观察）进入 `EvidenceRecord`，不需要伪造 `ruleId`；
- `RuleMatchEvent.ruleId` 永远必填；
- 多位置证据使用 `locations[]`；派生事实保留来源链（`derivedFromEvidenceIds`）；
- Secret 原文不得持久化，只保留 `redactedPreview`。

### 5.1 `occurrenceFingerprint` 的计算方式（问题#8 的解决方案）

**问题**：`eventDedupKey` 若引用 `evidenceId`（系统生成的随机 ID），两次独立运行即使产生内容完全一样的证据，因为 ID 不同也无法去重，`EvidenceRecord` 专门设计的指纹字段形同虚设。

**规则**：

```text
eventDedupKey 的计算输入必须是 EvidenceRecord.occurrenceFingerprint 的集合，
禁止引用 evidenceId。

occurrenceFingerprint 的计算方式因 sensitivity 分级不同：

普通/非敏感 evidence（kind = source_span 且 sensitivity = normal）：
  occurrenceFingerprint = sha256(
    "verity:evidence-occurrence:v1"
    || canonicalLocation(locations)
    || rawByteRangeDigest   // 对原始字节范围直接摘要，不做语义规范化
  )

敏感/密钥类 evidence（sensitivity = secret）：
  occurrenceFingerprint = sha256(
    "verity:evidence-occurrence:v1"
    || canonicalLocation(locations)
    || evidenceKindTag      // 如 "aws_access_key"
    || producer.componentVersion
    || identityPolicyId
  )
  # 不对原始 Secret 值做哈希或保留，见 §12.4
```

### 5.2 `eventDedupKey` 的作用域（问题：跨版本重复的解决方案之一半）

```text
eventDedupKey = sha256(
  "verity:rule-match:v1"
  || canonicalSerialize({
      ruleId,
      ruleVersion,
      ruleConfigDigest,
      canonicalEvidenceOccurrences: sortedOccurrenceFingerprints,  // 只放 occurrenceFingerprint，不放 evidenceId
      canonicalLocations
    })
)
eventId = opaqueId(snapshotId, eventDedupKey)
```

**铁律**：`eventDedupKey`/`eventId` 只用于消除**同一个 ArtifactSnapshot 内**、同一 Rule 的重复执行/重试副本。绝不用于跨版本 Baseline，也绝不用于跨 Rule 合并。跨版本对比见 §10 `FindingMatchRecord`。

---

## 6. Rule 版本升级不能制造历史重复（问题#3 的解决方案）

**问题**：`eventDedupKey` 含 `ruleVersion`，同一个未修的问题在规则升级后会产生全新的 `eventId`/Finding，让用户误以为"突然多了一个新问题"，其实什么都没变。

**规则**：Rule Registry 强制要求声明版本迁移关系，不能是可选配置：

```ts
interface RuleDefinition {
  ruleId: string
  ruleVersion: string
  supersedes: string[]        // 必填数组（可为空），格式 "RULE_ID@version"，声明本版本是谁的延续
  // supersedes 为空数组表示"全新规则，无历史延续"，必须显式声明为空，不能省略字段
  engine: 'prompt' | 'skill'
  title: string
  findingType: string
  implementationId: string
  applicableKinds: ArtifactKind[]
  requiredEvidenceKinds: string[]
  defaultSeverity: FindingSeverity
  controlIds: string[]
  fixtureIds: string[]
}
```

Registry 加载时校验：如果新版本规则的 `findingType` 与某个旧版本规则相同但 `supersedes` 未声明该旧版本，加载失败并报错，强制规则作者显式做出选择（延续或断代），不能"忘记声明"而默默产生假新问题。`supersedes` 关系只用于 Baseline 匹配（§10），绝不影响 §5.2 的扫描内去重。

---

## 7. Candidate → Validation → Finding：状态分离与语义层安全边界

### 7.1 三种状态必须区分（不能混淆基础设施失败与判断结果）

```ts
interface SemanticCandidate {
  candidateId: string
  snapshotId: string
  findingType: string
  subject: Record<string, unknown>       // 见 §8 subject_key taxonomy，禁止生成器自由填写字段结构
  claim: string
  evidenceIds: string[]
  falsificationQuestion: string
  proposedSeverity: FindingSeverity
  generatorExecutionId: string
  generatorId: string
  generatorVersion: string
}

type CandidateAssessmentState =
  | 'pending'
  | 'confirmed'
  | 'rejected'
  | 'insufficient_evidence'
  | 'validation_failed'          // 超时/拒答/格式错误/取消/Provider故障，绝不等同于 rejected

interface ValidationRecordBase {
  validationId: string
  candidateId: string
  executionId: string
  checkedEvidenceIds: string[]
  validatorId: string
  validatorVersion: string
  promptTemplateVersion?: string
  providerId?: string
  modelId?: string
  timestamp: string
}

type ValidationRecord = ValidationRecordBase & (
  | { status: 'completed'; verdict: 'confirmed' | 'rejected' | 'insufficient_evidence'; rationale: string; evidenceSufficiencyChallenge?: EvidenceSufficiencyChallenge }
  | { status: 'failed'; verdict?: never; errorCode: string }
  | { status: 'cancelled'; verdict?: never; terminationReason: string }
)

interface CandidateAssessment {
  candidateAssessmentId: string
  candidateId: string
  validationPolicyId: string
  validationPolicyVersion: string
  validationIds: string[]
  state: CandidateAssessmentState
  reasonCodes: string[]
}
```

**规则**：不能因为一条 `ValidationRecord.verdict = confirmed` 就直接生成 Finding。必须由版本化的 `ValidationPolicy` 读取该候选**全部**相关 `ValidationRecord`（可能有多条，来自不同验证轮次），综合产生 `CandidateAssessment`。只有 `CandidateAssessment.state = confirmed` 才允许生成 semantic Finding。

### 7.2 验证器输出隔离（问题#4 的解决方案，本轮最重要的新增防护）

**问题**：验证器输出 Schema 的 `verdict` 字段受约束，但 `rationale`（判断理由）和潜在的 `missing_evidence` 是自由文本。如果被审内容（可能是攻击者精心构造）诱导验证器在理由文字中"顺嘴"夹带新的问题描述，若后续任何环节把这段自由文本当输入去解析生成新 Finding 或新 Candidate，就构成一次成功的间接注入。

**规则（程序级约束，不是提示词约束）**：

```text
1. ValidationRecord.rationale 字段的内容，在整套管线代码中，
   只允许被当作"展示文本"原样挂载在该条 ValidationRecord 下面。
2. 禁止任何代码路径把 rationale 字符串再次传给：
   - 候选生成器（防止"自我循环放大"）
   - 任何会产出 Finding/Candidate/RuleMatch 的解析函数
   - 报告渲染时的结构化字段解析器
3. 如果验证器返回的 JSON 结构中出现未声明字段（Schema 之外的字段），
   整条 ValidationRecord 标记 status = 'failed'，errorCode = 'SCHEMA_VIOLATION_EXTRA_FIELD'，
   不得静默忽略多余字段后继续使用其余部分。
4. 每次 Provider 调用的完整原始响应（脱敏后）需要保留在审计日志中，
   便于事后核查"验证器是否曾尝试夹带内容"，用于持续改进检测。
```

**验收要求**：必须有专门的对抗测试用例，构造"被审内容中包含诱导验证器在 rationale 里提出新问题"的 fixture，验证系统在这种输入下最终 Finding 列表不会包含任何未经过正规候选生成流程的问题。

### 7.3 候选生成器与验证器的独立性（问题#5 的解决方案）

**问题**：候选生成器同时控制 claim、evidence 选择和 falsification question 三者，如果生成器本身选择性挑选了对自己claim有利的证据（无论是无意疏漏还是被诱导），验证器拿到的只是被框定好的材料，"独立验证"名不副实。

**规则**：

```ts
interface EvidenceSufficiencyChallenge {
  challengeType: 'insufficient_context' | 'biased_evidence_selection' | 'missing_counter_evidence'
  missingContextDescription: string
  // 关键约束：这个字段只能表达"我认为材料不够/有偏"，
  // 绝不能包含新的 claim 或新的 findingType，
  // 结构上不提供任何字段可以让验证器借此产出新问题
}
```

验证器的输出 Schema（`ValidationRecord.completed.evidenceSufficiencyChallenge`）允许验证器标记"证据选择可能有偏或缺关键反证"，但这个标记：

- 只能导致该候选的 `CandidateAssessment.state` 落在 `insufficient_evidence`，不能自动升级成一个新 Candidate；
- 触发该标记的候选需要计入 Coverage 的"需要人工复核"类别；
- 不允许验证器在此字段中夹带任何形式的新 claim（Schema 层面这个字段没有 `claim` 或 `findingType` 属性，物理上做不到）。

### 7.4 Finding 的两条来源路径必须物理隔离（问题#6 的解决方案）

**问题**：文档一边说"deterministic RuleMatch 可以直接产 Finding"，一边统一给所有 Finding 套用 validation/status 字段，导致读者无法判断确定性结果是否也要走验证流程，也没有明确谁有权把 deterministic Finding 降级。

**规则（铁律，代码架构层面强制，不是文档建议）**：

```ts
type FindingOrigin =
  | { kind: 'deterministic_rule'; ruleMatchEventIds: string[] }
  | { kind: 'semantic_validation'; candidateId: string; candidateAssessmentId: string; validationIds: string[] }

interface Finding {
  findingId: string
  snapshotId: string
  findingOccurrenceFingerprint: string
  findingType: string
  subject: Record<string, unknown>
  subjectKey: string              // 见 §8
  claim: string
  severity: FindingSeverity
  origin: FindingOrigin
  evidenceIds: string[]
  controls: string[]
  tags: string[]
}
```

1. `origin.kind === 'deterministic_rule'` 的 Finding：产出路径上不存在任何 `ValidationPolicy`、`CandidateAssessment` 或 LLM 调用节点，从 `RuleMatchEvent` 到 `Finding` 是纯代码函数，物理上不经过任何可能引入 LLM 判断的代码路径。
2. 唯一能让一条 deterministic Finding 不出现在最终报告的原因，只能是：（a）Rule 本身的适用条件未满足（不适用，发生在 Rule 执行之前）；（b）用户/Policy 层面的 `DispositionRecord`（接受风险、Suppression，发生在 Finding 产出之后，是显式记录，不是隐藏）。
3. 除以上两种情况，任何组件（候选生成器、验证器、Meta 汇总模型、报告渲染层）都没有权限过滤、降级、重写或"综合判断后忽略"一条 deterministic Finding，尤其是 severity 为 High/Critical 的。这一条必须有专门的架构测试：故意构造一个"LLM 汇总步骤尝试忽略某条 Critical deterministic Finding"的场景，验证代码结构上是否存在能让这种情况发生的调用路径，若存在则视为架构违规。

---

## 8. `subject_key` taxonomy：Finding 身份不能由生成器自由决定（问题#2 的解决方案）

**问题**：`Finding.subject` 若允许候选生成器自由填写字段结构，`subject_key`（用于跨位置聚合、Baseline 匹配的身份键）就会变成一个新的隐性模糊身份键——本质上是把之前反复批判的"让 LLM 自己命名问题身份"换了个字段名重新犯一次。

**规则**：每个 `findingType` 必须在 Finding Type Registry 中预先声明它的 `subject` 允许包含哪些字段，以及每个字段必须来自 ArtifactModel/Evidence 的哪个确定性路径，生成器不能自由决定 `subject` 的结构：

```ts
interface FindingTypeDefinition {
  findingType: string
  engine: 'prompt' | 'skill'
  subjectSchema: {
    // 每个字段必须声明它从哪个确定性来源提取，不允许 "freeform" 类型
    fields: Array<{
      fieldName: string
      sourceKind: 'artifact_model_path' | 'evidence_field' | 'literal_enum'
      sourcePath?: string           // 例如 "toolPolicies.maximumCalls" 的路径本身，不是值
      allowedValues?: string[]      // sourceKind = literal_enum 时必填
    }>
  }
  subjectKeyFields: string[]        // subjectSchema.fields 的子集，决定 subjectKey 由哪些字段组成
  defaultSeverity: FindingSeverity
  requiredEvidenceKinds: string[]
}
```

`subjectKey` 的计算方式：

```text
subjectKey = sha256(
  "verity:subject-key:v1"
  || findingType
  || canonicalSerialize(
       pick(subject, subjectKeyFields).sortByFieldName()
     )
)
```

**校验时机**：候选生成器/Rule 产出的 `subject` 对象在写入前必须通过对应 `FindingTypeDefinition.subjectSchema` 校验；出现未声明字段或字段值不满足 `sourceKind` 约束，则该 Candidate/RuleMatch 直接判定为格式错误（`validation_failed` 类似语义），不进入下游管线。

---

## 9. ReviewPlan / Coverage：先有计划，再对账执行

原设计的 Coverage 只记录"实际跑了什么"，缺少"本应该跑什么"的基准，导致漏注册的 Analyzer 完全不可见。

```ts
interface ReviewProfile {
  mode: 'static_only' | 'full_semantic'
  semanticExecution: 'disabled' | 'local_model' | 'external_provider'
  profileVersion: string
  maxCandidateGenerationCalls: number    // 见 §12.5，防成本攻击
  maxValidationCalls: number
}

interface ReviewPlan {
  reviewPlanId: string
  reviewId: string
  revision: number
  phase: 'initial' | 'expanded'
  expansionDepth: number                 // 见 9.1，递归展开的深度上限
  items: AnalysisPlanItem[]
  createdAt: string
}

interface AnalysisPlanItem {
  planItemId: string
  componentKind: 'parser' | 'analyzer' | 'rule' | 'candidate_generator' | 'validator'
  componentId: string
  componentVersion: string
  scope: string[]
  requirement: 'required' | 'optional'
  gatingClass: 'critical' | 'normal'
}

interface ExecutionRecord {
  executionId: string
  planItemId: string
  status: 'completed' | 'partial' | 'failed' | 'cancelled' | 'unsupported' | 'not_applicable'
  coveredScopes: string[]
  skippedScopes: Array<{ scope: string; reasonCode: string }>
}

interface CoverageAssessment {
  coverageAssessmentId: string
  reviewId: string
  reviewPlanId: string
  reviewPlanRevision: number
  status: 'sufficient' | 'insufficient' | 'failed'
  criticalGapPlanItemIds: string[]
  reasonCodes: string[]
  policyId: string
  policyVersion: string
  createdAt: string
}
```

`InitialReviewPlan` 在任何 Parser 执行前创建；ArtifactModel 形成后再追加 `ExpandedReviewPlan`。Coverage 由最终 Plan revisions 与 `ExecutionRecord[]` 对账计算，不能由执行器自己宣称"我完成了"。

### 9.1 展开计划必须有深度上限（问题：递归展开无上限）

**问题**：Skill 的文件引用图可能多层嵌套（脚本A引用B，B引用C……），若展开轮次没有上限，一个精心构造的循环引用 Skill 可能诱导审查器无限展开分析计划。

**规则**：

```text
ExpandedReviewPlan.expansionDepth 存在硬编码上限（默认 3，可由 ReviewProfile 覆盖但设有全局最大值 5）。
达到上限后，剩余未展开的引用路径记入 CoverageAssessment.reasonCodes，
标记为 'expansion_depth_exceeded'，不再继续展开，不视为失败，但必须在报告中可见。
引用图中检测到循环（A→B→A）时，立即终止该分支展开并记录 'circular_reference_detected'，
不依赖深度上限自然截断循环（避免依赖上限掩盖真实的循环引用问题本身，
循环引用本身也应该产生一条 RuleMatchEvent）。
```

### 9.2 Rule 所需证据部分缺失时的处理（问题：适用性判断不明确）

**问题**：一条 Rule 依赖多个 Analyzer 产出的 Evidence，若其中一个 Analyzer 执行失败，另一个成功，现有设计未区分"规则不适用"与"覆盖不足无法判断"。

**规则**：

```text
Rule 执行前先检查 requiredEvidenceKinds 是否全部拿到对应 Evidence：

- 全部所需 Evidence 都存在，且判断后确实不满足触发条件 → 结果为 "未命中"（正常通过）
- 全部所需 Evidence 都存在，且判断后满足触发条件 → 产出 RuleMatchEvent
- 任意所需 Evidence 因为对应 ExecutionRecord.status ∈ {failed, cancelled, partial} 而缺失
  → 该 Rule 的执行状态记为 'blocked_by_upstream_failure'，
     不是 'not_applicable'，不是"未命中"，
     必须计入 CoverageAssessment.criticalGapPlanItemIds（若该 Rule 的 gatingClass 为 critical）
- 任意所需 Evidence 因为对应 Analyzer 的 gate 判断为 false 而不存在（正常的"不适用"路径）
  → 该 Rule 状态记为 'not_applicable'，附 reasonCode 说明具体是哪个 gate 条件不满足
```

对安全类 Rule（如 Secret 扫描），`blocked_by_upstream_failure` 必须在报告中以醒目方式区别于"未命中"展示，绝不能让"分析器执行失败"和"确实没有 Secret"在界面上看起来一样。

---

## 10. Baseline / Suppression：与 Event 去重完全分离

### 10.1 分层匹配

不存在万能稳定 Key。匹配必须先处于明确 Scope（同一 `artifactId`，同项目/仓库/分支或 Profile），跨 Scope 默认不自动匹配：

```ts
interface FindingMatchRecord {
  findingMatchId: string
  baselineScopeId: string
  previousSnapshotId: string
  currentSnapshotId: string
  previousFindingIds: string[]
  currentFindingIds: string[]
  state: 'new' | 'existing' | 'changed' | 'resolved' | 'regressed' | 'ambiguous' | 'unknown_due_to_coverage'
  method: 'exact' | 'stable_subject' | 'rule_migration' | 'heuristic'
  matcherPolicyId: string
  matcherPolicyVersion: string
  confidence?: number
  reasonCodes: string[]
  userOverrideRecordId?: string
  createdAt: string
}
```

匹配优先级：（1）完全相同 occurrence；（2）相同 Rule Family + Registry 声明的稳定 `subjectKey`；（3）Rule Registry 中声明的 `supersedes` 版本迁移（见 §6）；（4）启发式匹配只生成 `ambiguous`，不自动改变门禁或 Suppression 结果。

### 10.2 Coverage 不足时不能标 resolved

只有当旧 Finding 所在路径/Subject 被当前 Parser 和 Rule 成功覆盖、且确实未再观察到时，才能标 `resolved`。若相关 Analyzer 未运行、语言不支持、Parser partial 或路径被跳过，只能标 `unknown_due_to_coverage`，不能把"没检查到"写成"已修复"。

### 10.3 Baseline 与 Suppression 分开

Baseline 只描述历史变化；Suppression/accepted risk 是带操作者、理由、范围、有效期和政策的追加记录（`DispositionRecord`，见 §11.2）。模糊 Baseline 匹配不能自动触发 Suppression。**被审 Skill 自带的 baseline/suppression 配置默认不可信，不能用于隐藏自身 Finding**——Baseline 来源只能是受信项目配置、CI 配置或 Verity 自己的审计存储。

---

## 11. Finding 的关系与用户处置

```ts
interface FindingRelation {
  relationId: string
  snapshotId: string
  fromFindingId: string
  toFindingId: string
  relation: 'supports' | 'related_to' | 'enables' | 'conflicts_with' | 'same_fix_target'
  method: 'deterministic' | 'semantic_suggestion' | 'user_confirmed'
  producerExecutionId?: string
  createdAt: string
}

type FindingDispositionAction = 'acknowledge' | 'accept_risk' | 'suppress' | 'remove_suppression'

interface DispositionRecord {
  dispositionRecordId: string
  findingId: string
  action: FindingDispositionAction
  actorId: string
  reason: string
  scope: string
  policyId: string
  policyVersion: string
  createdAt: string
  expiresAt?: string
}
```

语义建议关系（`method = semantic_suggestion`）不得改变 Finding 身份或删除 Finding。Disposition 过期后由政策重新计算效果，不篡改历史记录。

---

## 12. Provider 与数据出境

必须在第一次调用任何外部模型之前完成，但不阻塞纯静态 Walking Skeleton。

### 12.1 出境流程

```text
ArtifactSnapshot
  → Local Data Classification
  → Secret/PII Preflight
  → Redaction + Context Minimization
  → SanitizedEgressView
  → Provider Adapter
```

Provider Adapter 只能接收 `SanitizedEgressView`，不能直接接收原始 Snapshot。所有使用 LLM 的组件（Contract Extractor、Candidate Generator、Validator、Remediation Generator、未来 Runtime Judge）都必须经过这条路径。

### 12.2 Secret 扫描不是绝对保证

已检测 Secret 必须移除；Preflight failed/unsupported 时禁止调用外部 Provider，只能改用本地模型或终止语义阶段；Preflight partial 时只能发送已成功分类、明确切出的安全片段。

### 12.3 每次调用的最小出境清单

```ts
interface EgressManifest {
  egressId: string
  policyId: string
  providerId: string
  modelId: string
  sentObjectRefs: string[]       // 本地对象引用，不含原始正文
  dataClassification: string
  redactionTransformsApplied: string[]
  authorizationRecordId: string
  region?: string
  retentionSetting?: string
  trainingOptOut: boolean
  requestLogPolicy: string
  timestamp: string
  outcome: 'success' | 'error' | 'timeout' | 'cancelled'
}
```

`RedactionMap`（占位符 Token → 原始 Snapshot 位置的映射）只存本地，不进入报告导出物。

### 12.4 `RedactionMap` 的存储与销毁（问题：脱敏还原地图本身是敏感资产）

**问题**：`RedactionMap` 记录了"如何把占位符还原成原始 Secret 所在位置"，如果它跟报告一起被导出或被不当访问，等于把还原原始敏感内容的钥匙一起交出去了。

**规则**：

```text
1. RedactionMap 不允许出现在任何 ReportProjection、JSON/Markdown/SARIF 导出物中，
   属于代码层面的物理隔离（不同的存储位置、不同的序列化路径），不依赖运行时判断。
2. RedactionMap 必须加密存储（at-rest encryption），密钥与审查会话绑定。
3. 默认策略：审查会话结束（ReviewRun 进入 completed/failed/cancelled 终态）后，
   RedactionMap 默认销毁；如需保留用于调试，必须显式的调试授权，
   且保留期限由单独的政策字段控制，不能默认无限期保留。
4. Provider 占位符使用每次请求随机或序号 token，不使用稳定 HMAC
   （防止跨请求关联同一敏感值的位置）。
```

### 12.5 成本攻击防护（问题：候选生成/验证调用无总量上限）

**问题**：现有设计限制了单次 LLM 调用的超时/重试/最大输出，但没有限制一次审查总共能触发多少次候选生成和验证调用。构造大量看似矛盾条款的恶意 Prompt，可能诱导系统发起大量调用，放大审查方自身的成本。

**规则**：

```text
ReviewProfile.maxCandidateGenerationCalls 和 maxValidationCalls 是硬性上限（见 §9 ReviewProfile 定义）。
达到上限后：
  - 已产出但未验证的候选，其 CandidateAssessment.state 标记为 'pending'，
    并在 CoverageAssessment 中记为 'validation_budget_exhausted'
  - 不静默丢弃，必须在报告中可见"因预算限制，以下N个候选未完成验证"
  - 默认预算值需要基于制品大小分档（比如按 ArtifactModel 的候选来源数量分级），
    避免固定值对大型 Skill 包过于严苛，也避免无上限被滥用
```

### 12.6 配置类字符串同样是注入面（问题：仅制品内容被脱敏，配置输入未设防）

**问题**：现有数据出境设计只考虑了"制品内容"发送前的脱敏，但规则元数据（自定义 `remediationTemplate`、自定义 Control 标题、导入的 Baseline 理由字段）如果被恶意配置注入了诱导文本，后续这些字符串被拼进发给 LLM 的 Prompt 时，构成一条未设防的注入路径。

**规则**：

```text
任何会被拼入发送给 LLM 的 Prompt 模板的配置类字符串（remediationTemplate、
Control 标题、用户自定义 Rule 的 title/description、导入的 Disposition.reason），
在写入 Registry/存储时必须先经过与制品内容同等级别的：
  - 长度上限校验
  - 已知注入模式的静态扫描（复用 §Skill 危险模式检测的部分规则）
  - 明确的分隔符包裹（防止配置字符串被 LLM 误解析为指令边界之外的新指令）
不允许"配置是我们自己写的所以天然可信"这种假设，
一旦支持用户自定义规则/模板（即便只是团队内部使用），这个假设就不成立。
```

### 12.7 Provider 输出同样不可信

严格 Schema 和大小限制；不允许工具调用、命令执行或自动访问输出中的链接；HTML/Markdown 必须转义；原始响应和错误日志再次脱敏；输出只能成为 Candidate、Validation 或 Remediation 草案，不能改写 Evidence/RuleMatch；Prompt delimiter 和 system instruction 只能降低 Prompt Injection 风险，不是安全保证。BYOK、可配置 base URL、代理和 trace 也属于威胁面，必须防止凭据入日志、恶意 endpoint 和 SSRF。

---

## 13. Safe Intake

### 13.1 ZIP

streaming 解压并实时执行单文件/总字节/文件数/CPU/时间/内存限制；不信任 central directory 中的 size/ratio；拒绝或显式标记 encrypted、损坏、绝对路径、NUL、盘符、UNC、反斜杠逃逸；规范化后重复路径或大小写碰撞直接拒绝；symlink/hardlink/特殊条目只记录不跟随不打开；V1 默认不递归解包嵌套 archive；验证必须在写盘前或流式写盘过程中完成。

### 13.2 目录

**TOCTOU 处理（问题：复制过程中文件被修改的时间窗未堵住）**

```text
规则：目录复制到隔离 Snapshot 时，采用"边读边计算摘要"，不是"复制完再校验"：
  - 每个文件在被复制的同一次读取流中同步计算内容摘要
  - 复制完成后立即再做一次轻量 stat 检查（mtime/size），
    若与复制开始前的初始 stat 不一致，标记该文件为 'intake_race_detected'
  - 检测到竞争时，默认策略是保留"复制流中实际读到的内容"作为 Snapshot 内容
    （而不是重新读取，避免竞争窗口被无限放大），
    但必须在 snapshotManifestDigest 对应条目中记录 raceDetected = true，
    该文件的分析结论在报告中附带"检测到读取期间发生变化"的提示
```

no-follow 读取；拒绝 device/FIFO/socket 等特殊文件；symlink 只记录 target 字符串；复制到受限临时 Snapshot；后续 Analyzer 只读取不可变 Snapshot，不再读取用户原目录。

### 13.3 GitHub URL / Git

限定 `https://github.com/...`；拒绝 `file://`、SSH/scp、git/ext 协议、本地路径和 URL 内嵌凭据；限制重定向并防止 SSRF/DNS rebinding/内网地址；不继承宿主 Git config/credential helper/SSH agent/cookie；禁止 Hook/Submodule/LFS/smudge filter；限制历史深度/pack/object/delta 大小数量/CPU/时间；ref 解析后固定完整 immutable OID；对选中树计算 Verity SHA-256；报告和日志中的 URL 移除 token/query/凭据。私有仓库支持单独设计，不自动复用宿主凭据。

---

## 14. Remediation 与自动 Patch

### 14.1 V1 首先只提供结构化建议

包括修改目标、修改原则、受影响约束、示例、验证建议、是否由模型生成及可信度。是建议，不直接写盘。

### 14.2 自动应用是独立能力门禁

```ts
interface PatchSetProposal {
  patchSetId: string
  proposalDigest: string
  baseSnapshotId: string
  baseContentRootDigest: string
  sourceFindingIds: string[]
  applyMode: 'independent' | 'transactional_all_or_none'
  edits: PatchEdit[]
  postApplyChecks: ValidationCheckRef[]
}

interface PatchEdit {
  operation: 'replace'   // 首版只开放 replace
  filePath: string
  baseFileDigest: string
  sourceEncoding: string
  sourceByteRange: { start: number; end: number }
  expectedRangeDigest: string
  replacement: string
}

interface ValidationCheckRef {
  validatorId: string
  validatorVersion: string
  paramsSchemaId: string
  params: Record<string, unknown>
}
```

安全要求：不使用自由文本 `validationSteps: string[]`；`postApplyChecks` 只能调用 Verity 自己白名单化、版本化的 Validator，绝不执行被审 Artifact 提供的命令；`params` 必须通过严格 JSON Schema 校验，V1 Validator 禁止 shell/subprocess/网络；不强制持久化 `beforeText`；Diff 从本地 base Snapshot 即时生成，Secret 只显示脱敏内容；首版禁止 fuzzy rebase；路径规范化并确认位于 Artifact Root 内；不修改 symlink/special/binary/包外文件；限制 Patch 文件数/总大小；用户确认绑定 `proposalDigest` 和精确渲染的 Diff，任何变化都要重新确认；多文件采用事务式 all-or-none；全部成功后生成新 Snapshot 并重新审查，重新审查前不把 Finding 标 resolved。ZIP/GitHub 来源默认导出修订副本或 patch，不直接修改原 ZIP 或远端仓库。

---

## 15. 报告是不可信内容出口

用户和模型内容默认按纯文本渲染；Markdown/HTML 使用严格 allowlist sanitizer；禁止脚本/iframe/事件属性/活动内容；配置 CSP；外链使用安全属性并明确提示；不自动打开 file/custom scheme/模型生成的链接；JSON/对象解析防止 prototype pollution；证据和 Remediation 永远不作为可信富文本。

未确认内容展示规则：confirmed → Findings；insufficient evidence → "需要更多证据"；validation failed/unvalidated → Coverage/未完成检查；rejected → 默认只在审计详情；不允许为了首屏简洁删除底层对象。

---

## 16. Verdict：产品语义与 Coverage 正交

```ts
type SubjectDecision =
  | { engine: 'prompt'; outcome: 'ready' | 'needs_revision' }
  | { engine: 'skill'; outcome: 'low_detected_risk' | 'review_required' | 'do_not_install' }

interface ReviewDecision {
  subject?: SubjectDecision
  coverage: 'sufficient' | 'insufficient' | 'failed'
  policyId: string
  policyVersion: string
  reasonCodes: string[]
}
```

政策规则：已知 High/Critical 风险不能因 Coverage 不足被隐藏，两个结论同时显示；无已知问题但 Coverage 不足时不输出 `ready`/`low_detected_risk`，只显示 Coverage 不足；Decision 输入可包括 Finding、Severity、Coverage、门禁配置、Disposition、Baseline，但不能读取模型自由文本总结来临时决定结果；CLI/Web/JSON/Markdown/SARIF 使用同一个版本化 Policy Engine。

---

## 17. V1 范围声明（避免隐性假设）

- 一次 Review 只对应一个 Artifact；批量扫描延期。
- Skill 首批支持语言矩阵：Markdown + YAML frontmatter（Manifest）、Shell、JavaScript/TypeScript、Python AST、`package.json`/常见 lockfile/`requirements.txt`/`pyproject.toml`。Binary/未知语言只记录 Coverage 缺口。
- Prompt 范围：文本输入 + 明确 role/source；模板必须明确方言；完整多消息 `PromptBundle` 延期。
- Heuristic Observation 只能成为 Candidate，不能绕过 ValidationPolicy 直接进入阻断项。
- 用户反馈作为独立的 Disposition/Feedback Record，不直接改写旧 Finding。
- Control Registry 首版使用少量稳定 ID 常量，Control 只表达审计目标，不产生 Finding，不作为"已完全覆盖"的证明。

---

## 18. 供应链与测试语料卫生

### 18.1 规则注册表的供应链完整性（问题：配置可被篡改）

```text
内置默认 Rule/FindingType/ValidationPolicy 定义编译进构建产物，
不允许运行时通过外部文件任意改写（尤其是 defaultSeverity 和 remediationTemplate）。
用户自定义规则走单独的、明确标注为"较低信任"的加载路径，
加载时同样要过 §12.6 的注入检测，且默认不允许自定义规则声明 High/Critical 严重度
覆盖内置规则的结论（只能新增，不能降级已有内置规则）。
```

### 18.2 Golden Fixture 语料卫生（问题：测试样本来源未检查）

```text
所有缺陷注入/干净样本 Fixture 在纳入测试库前必须确认：
  - 不含真实 Secret（若来自参考项目片段，须先用 Secret 扫描器自扫一遍再纳入）
  - 若片段来自外部开源项目，记录来源仓库、commit、许可证类型
  - 许可证要求署名的，在 Fixture 目录的 NOTICE 文件中登记
```

---

## 19. 并发与多租户执行期隔离（问题：只考虑了数据保留隔离，未考虑执行期隔离）

**问题**：现有设计在"历史与数据保留"提到多租户隔离，但若 Verity 作为共享服务运行，并发审查之间还存在执行期资源冲突：临时目录冲突、LLM 速率限制预算是否被单个恶意提交挤占。

**规则**：

```text
1. 每次 ReviewRun 使用独立的临时工作目录（含随机不可预测的路径片段），
   不同 ReviewRun 之间不共享临时文件系统命名空间。
2. §12.5 的 maxCandidateGenerationCalls/maxValidationCalls 预算是"每个 ReviewRun 独立"的，
   不是全局共享池；若底层 Provider 账号存在全局速率限制，
   需要有独立的排队/退避机制，防止单个 ReviewRun 的限流影响其他租户的 ReviewRun 判定为"validation_failed"。
3. 数据保留期隔离（谁能看到谁的历史报告）与本节的执行期资源隔离是两个独立的政策维度，
   不能用同一套配置字段表达。
```

---

## 20. Phase 0 验收清单（完整 19 项，逐项对应本文档章节）

| # | 问题 | 解决方案所在章节 | 验收标准 |
|---|---|---|---|
| 1 | DetectionEvent 职责重叠 | §5 | Evidence/RuleMatch/Candidate 三个独立类型已定义且互不复用字段 |
| 2 | precise_location 序列化未定义 | §4.2 | canonical 序列化规则冻结，含缺失字段占位测试 |
| 3 | subject_key 生成规则未定义 | §8 | 每个 findingType 在 Registry 中声明 subjectSchema，通过校验测试 |
| 4 | rule_version 升级制造历史重复 | §6 | supersedes 字段强制声明，Registry 加载时校验 |
| 5 | 验证器输出可能夹带新问题 | §7.2 | 对抗测试：诱导 rationale 夹带新问题，验证不会进入 Finding |
| 6 | 候选生成器可能选择性框定证据 | §7.3 | evidenceSufficiencyChallenge 字段无法产出新 claim，结构测试通过 |
| 7 | deterministic/semantic Finding 边界矛盾 | §7.4 | 架构测试：不存在能让 LLM 组件过滤 Critical deterministic Finding 的代码路径 |
| 8 | eventDedupKey 引用随机 ID 而非指纹 | §5.1/5.2 | 两次独立运行对相同内容产生相同 eventDedupKey 的回归测试 |
| 9 | Rule 部分证据缺失时状态不明确 | §9.2 | blocked_by_upstream_failure 与 not_applicable 分离测试 |
| 10 | 目录/ZIP 递归展开无上限 | §9.1 | 构造循环引用 fixture，验证 expansionDepth 生效且循环被检测 |
| 11 | 目录复制 TOCTOU | §13.2 | 复制期间修改文件的竞争测试，raceDetected 正确标记 |
| 12 | 一次 Review 是否支持多 Artifact 未声明 | §2 | V1 范围文档显式声明批量扫描延期 |
| 13 | RedactionMap 存储与访问控制未定义 | §12.4 | RedactionMap 不出现在任何导出物的序列化测试 |
| 14 | 候选生成/验证调用无总量上限 | §12.5 | 构造大量候选的 fixture，验证预算生效且可见 |
| 15 | 配置类字符串未设防注入 | §12.6 | 恶意 remediationTemplate 注入测试 |
| 16 | 并发/多租户执行期隔离未设计 | §19 | 并发 ReviewRun 临时目录与预算隔离测试 |
| 17 | 规则注册表供应链完整性未保护 | §18.1 | 自定义规则不能覆盖内置规则严重度的测试 |
| 18 | Golden Fixture 语料卫生未检查 | §18.2 | 全部现有 fixture 过一遍 Secret 扫描 + 许可证登记 |
| 19 | Baseline/去重范围混淆（跨版本 vs 扫描内） | §5.2/§10 | 明确分离的两套机制各自的单元测试 |

---

## 21. 开发计划（按能力设门禁）

### Phase 0：最小领域语义与测试地基

交付：最小 JSON Schema（Artifact/Snapshot/Review/Evidence/RuleMatch/Candidate/Validation/Finding）；能表达完整对象图的 Golden Fixture；覆盖上表 19 项的单元测试；Core 状态机与不变量测试；基础威胁模型。**不等待 Baseline 或自动 Fix 设计完成即可进入下一阶段。**

### Phase 1：Walking Skeleton

文本 Prompt + 最小 Skill 文件夹；安全 Snapshot；每个引擎至少一个确定性 Rule；Evidence → RuleMatch → Finding 完整闭环；ReviewPlan/Execution/Coverage 最小闭环；JSON 输出；极简 Web 报告（从第一版就做不可信内容转义和最小 CSP）。**不接外部 LLM，不接 ZIP/Git，不自动修改文件。**

### Phase 2：Prompt 确定性闭环

Prompt/System Prompt/Template 分型；首批 5-10 条高价值确定性规则；Contract Model；确定性 Remediation；Severity 与版本化 Policy（该阶段结论仅用于产品预览，不开放 CI 门禁）。

### Phase 3：Skill 确定性闭环

明确 Skill 规范版本；Manifest/引用/文件图/Secret/Unicode/危险命令；最小能力词表（文件、进程、网络、凭据、配置、安装）；支持语言矩阵；目录和 ZIP 安全门禁；GitHub URL 放在本阶段后段，安全 Gate 通过后开放。

### Phase 4：语义候选与验证

Data Egress Gateway；SanitizedEgressView/EgressManifest/RedactionMap；一个 Prompt Candidate Generator；一个 Candidate Validator；一个 Skill 声明-观察差异 Candidate；CandidateAssessment/ValidationPolicy；§7.2/7.3 的对抗测试；超时/拒答/格式错误/Prompt Injection/Provider 故障测试。

### Phase 5：完整报告、Baseline 与 CI

双轴 Decision Policy 扩展到确定性与语义结果，开放正式 CI 门禁；完整 Web 报告和历史；用户 Feedback/Disposition；FindingRelation；Baseline/Suppression/Diff；历史/日志保留期限、删除、加密、导出、多租户隔离验收；SARIF；GitHub Action。

### Phase 6：可选自动 Patch

PatchSet；本地 Diff；事务式 all-or-none；回滚；重新审查；Secret 和并发修改安全测试。

### 后续版本

V1.5 Prompt 黑盒、V2 Skill 沙箱：保留接口边界即可，相关技术 ADR 不阻塞 V1 Walking Skeleton。

---

## 22. 能力门禁总表

| 能力门禁 | 启用前必须完成 |
|---|---|
| Walking Skeleton | 身份分层、Evidence→RuleMatch→deterministic Finding、最小 ReviewPlan/Coverage、语义对象独立性核心不变量 |
| 新增 Rule | Rule/Analyzer 接口、Location、Event 去重作用域、supersedes 声明、正反 Golden Fixture |
| 目录输入 | 不可变 Snapshot、no-follow、TOCTOU 控制 |
| ZIP 输入 | streaming 资源限制、路径规范、symlink/encrypted 处理 |
| GitHub URL | 协议/域名范围、SSRF、凭据隔离、ref 固定、资源上限 |
| 首次调用外部 LLM | §12 全部（数据出境、成本预算、配置注入防护） |
| 语义候选/验证 | §7.2/7.3 对抗测试通过 |
| 正式 Verdict/CI | Severity、Coverage、Decision Policy、多格式一致性 |
| Baseline/Suppression | subjectKey taxonomy、分层匹配、Coverage 约束 |
| 自动 Patch | PatchSet、字节锚点、事务语义、用户确认、回滚 |
| 任何 Web 报告 | 不可信内容纯文本输出、CSP、报告攻击测试 |
| 共享服务部署 | §19 并发隔离 |

---

## 附：与 v0.1 的关系

v0.1 中未被本版修改的内容（Prompt/Skill 两套引擎定位、Analyzer/Rule/Matcher/Extractor 分工、精确去重原则、四层审查级别 L0-L3 定义）继续有效，本文档不重复列出，具体分工细节参见对话讨论记录（`CHANGELOG.md`）。v0.1 全文保留作为历史存档，不再作为实施依据。
