import { readFile, writeFile } from 'node:fs/promises'

import { aggregateVotes } from '@butler/core/orchestrator/voteAggregator'
import { runDocumentProfile } from '@butler/core/orchestrator/documentProfiler'
import { judgeSkillWithModel } from '@butler/core/orchestrator/llmJudgeEngine'
import { runStaticCheckEngine } from '@butler/core/orchestrator/staticCheckEngine'
import { loadBuiltinSkills } from '@butler/core/skillLoader/loadBuiltinSkills'
import type {
  ModelConfig,
  SkillDefinition,
} from '@butler/types/reviewReport.types'

type Observation = 'present' | 'absent' | 'inconclusive' | 'error'

interface PacketItem {
  itemId: string
  objectType: 'prompt' | 'skill'
  targetRisk: {
    definition: string
    falsificationQuestion: string
  }
  artifact: {
    files: Array<{ path: string; content: string }>
  }
}

interface RunnerConfig {
  packetPath: string
  outputPath: string
  apiKeyEnv: string
  baseUrl: string
  models: Array<{
    modelId: string
    inputPricePerMillion: number
    outputPricePerMillion: number
  }>
  repetitions: number
  maxOutputTokens: number
  maxConcurrency: number
  maxTotalCalls: number
  maxTotalTokens: number
  maxSpendUsd: number
  maxRequestBytes: number
  maxResponseBytes: number
  requestTimeoutMs: number
  configurationFingerprint: string
  skillMap: Record<string, string[]>
}

interface Packet {
  schemaVersion: number
  protocolId: string
  protocolVersion: string
  systemId: string
  corpusFingerprint: string
  items: PacketItem[]
}

function requireObject(value: unknown, name: string): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error(`${name} must be an object`)
  }
  return value as Record<string, unknown>
}

function artifactText(item: PacketItem): string {
  if (!Array.isArray(item.artifact?.files) || item.artifact.files.length === 0) {
    throw new Error('packet artifact is empty')
  }
  if (item.objectType === 'prompt' && item.artifact.files.length === 1) {
    return item.artifact.files[0].content
  }
  return item.artifact.files
    .map((file) => `===== FILE: ${file.path} =====\n${file.content}`)
    .join('\n\n')
}

async function readBoundedResponse(
  response: Response,
  maxBytes: number,
): Promise<Uint8Array> {
  const declaredLength = response.headers.get('content-length')
  if (
    declaredLength !== null
    && /^\d+$/.test(declaredLength)
    && Number(declaredLength) > maxBytes
  ) {
    await response.body?.cancel().catch(() => undefined)
    throw new Error('butler reference response exceeds byte budget')
  }
  if (!response.body) return new Uint8Array()

  const reader = response.body.getReader()
  const chunks: Uint8Array[] = []
  let totalBytes = 0
  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      if (!value) continue
      totalBytes += value.byteLength
      if (totalBytes > maxBytes) {
        await reader.cancel().catch(() => undefined)
        throw new Error('butler reference response exceeds byte budget')
      }
      chunks.push(value)
    }
  } finally {
    reader.releaseLock()
  }

  const body = new Uint8Array(totalBytes)
  let offset = 0
  for (const chunk of chunks) {
    body.set(chunk, offset)
    offset += chunk.byteLength
  }
  return body
}

function installBudgetedFetch(config: RunnerConfig) {
  const originalFetch = globalThis.fetch.bind(globalThis)
  const prices = new Map(
    config.models.map((model) => [model.modelId, model]),
  )
  let reservedCalls = 0
  let reservedTokens = 0
  let reservedSpendUsd = 0

  globalThis.fetch = async (input, init = {}) => {
    const url = typeof input === 'string' ? input : input instanceof URL
      ? input.toString()
      : input.url
    const expected = `${config.baseUrl.replace(/\/$/, '')}/chat/completions`
    if (url !== expected || init.method !== 'POST') {
      throw new Error('butler reference adapter refused unexpected network target')
    }
    if (typeof init.body !== 'string') {
      throw new Error('butler reference adapter requires a JSON request body')
    }
    const wire = requireObject(JSON.parse(init.body), 'Butler request')
    const modelId = wire.model
    if (typeof modelId !== 'string' || !prices.has(modelId)) {
      throw new Error('butler reference adapter refused an unconfigured model')
    }
    wire.max_tokens = config.maxOutputTokens
    wire.stream = false
    const body = JSON.stringify(wire)
    const requestBytes = Buffer.byteLength(body, 'utf8')
    if (requestBytes > config.maxRequestBytes) {
      throw new Error('butler reference request exceeds byte budget')
    }
    const inputTokenBound = requestBytes + 1024
    const tokenReservation = inputTokenBound + config.maxOutputTokens
    const price = prices.get(modelId)!
    const spendReservation = (
      inputTokenBound * price.inputPricePerMillion
      + config.maxOutputTokens * price.outputPricePerMillion
    ) / 1_000_000
    if (
      reservedCalls + 1 > config.maxTotalCalls
      || reservedTokens + tokenReservation > config.maxTotalTokens
      || reservedSpendUsd + spendReservation > config.maxSpendUsd + 1e-12
    ) {
      throw new Error('butler reference run budget exhausted')
    }
    reservedCalls += 1
    reservedTokens += tokenReservation
    reservedSpendUsd += spendReservation

    const timeout = AbortSignal.timeout(config.requestTimeoutMs)
    const signal = init.signal
      ? AbortSignal.any([init.signal, timeout])
      : timeout
    const response = await originalFetch(input, {
      ...init,
      body,
      redirect: 'error',
      signal,
    })
    const responseBody = await readBoundedResponse(
      response,
      config.maxResponseBytes,
    )
    return new Response(responseBody, {
      status: response.status,
      statusText: response.statusText,
      headers: response.headers,
    })
  }

  return () => ({
    schemaVersion: 1,
    method: 'utf8_request_bytes_plus_1024_and_max_output_reservation',
    maxCalls: config.maxTotalCalls,
    maxTotalTokens: config.maxTotalTokens,
    maxSpendUsd: config.maxSpendUsd,
    reservedCalls,
    reservedTokens,
    reservedSpendUsd: Number(reservedSpendUsd.toFixed(8)),
  })
}

function modelConfigs(config: RunnerConfig): ModelConfig[] {
  return config.models.map((model) => ({
    id: model.modelId,
    label: model.modelId,
    provider: 'custom',
    baseUrl: config.baseUrl,
    modelId: model.modelId,
    selected: true,
  }))
}

async function evaluateItem(
  item: PacketItem,
  skillsById: Map<string, SkillDefinition>,
  config: RunnerConfig,
  apiKey: string,
): Promise<Observation> {
  const skillIds = config.skillMap[item.targetRisk.falsificationQuestion]
  if (!Array.isArray(skillIds) || skillIds.length === 0) {
    return 'error'
  }
  const skills = skillIds.map((skillId) => skillsById.get(skillId))
  if (skills.some((skill) => !skill)) {
    return 'error'
  }
  const targetSp = artifactText(item)
  const models = modelConfigs(config)
  const reviewId = crypto.randomUUID()
  try {
    const profile = await runDocumentProfile({
      targetSp,
      model: models[0],
      apiKey,
      reviewId,
    })
    let sawError = false
    for (const skill of skills as SkillDefinition[]) {
      const staticResult = runStaticCheckEngine(skill, targetSp)
      if (staticResult.issues.some((issue) => issue.status === 'found')) {
        return 'present'
      }
      if (skill.execution_mode === 'static_check') {
        continue
      }
      const outputs = await Promise.all(models.map((model) =>
        judgeSkillWithModel({
          skill,
          targetSp,
          scenarioHint: (
            `Evaluate only this declared risk boundary: `
            + `${item.targetRisk.definition} `
            + item.targetRisk.falsificationQuestion
          ),
          documentProfile: profile.documentProfile,
          staticResult,
          model,
          apiKey,
          reviewId,
        }),
      ))
      if (outputs.some((output) => Boolean(output.error))) {
        sawError = true
      }
      if (aggregateVotes(outputs).some((issue) => issue.status === 'found')) {
        return 'present'
      }
    }
    return sawError ? 'error' : 'absent'
  } catch {
    return 'error'
  }
}

async function main() {
  const configPath = process.argv[2]
  if (!configPath) throw new Error('runner config path is required')
  const config = JSON.parse(await readFile(configPath, 'utf8')) as RunnerConfig
  const packet = JSON.parse(
    await readFile(config.packetPath, 'utf8'),
  ) as Packet
  if (
    packet.systemId !== 'butler'
    || !Array.isArray(packet.items)
    || !Array.isArray(config.models)
    || config.models.length < 2
    || config.models.length > 3
    || config.repetitions < 2
    || !Number.isInteger(config.maxConcurrency)
    || config.maxConcurrency < 1
    || config.maxConcurrency > 8
  ) {
    throw new Error('Butler reference runner configuration is invalid')
  }
  const apiKey = process.env[config.apiKeyEnv]
  if (!apiKey) throw new Error('Butler reference credential is missing')
  const budgetSnapshot = installBudgetedFetch(config)
  const skillsById = new Map(
    loadBuiltinSkills().map((skill) => [skill.id, skill]),
  )
  const observations = packet.items.map((item) => ({
    itemId: item.itemId,
    runs: Array<Observation>(config.repetitions),
  }))
  const tasks = packet.items.flatMap((item, itemIndex) =>
    Array.from({ length: config.repetitions }, (_, repetition) => ({
      item,
      itemIndex,
      repetition,
    })))
  let nextTask = 0
  async function worker() {
    while (nextTask < tasks.length) {
      const task = tasks[nextTask]
      nextTask += 1
      observations[task.itemIndex].runs[task.repetition] = await evaluateItem(
        task.item,
        skillsById,
        config,
        apiKey,
      )
    }
  }
  await Promise.all(
    Array.from(
      { length: Math.min(config.maxConcurrency, tasks.length) },
      () => worker(),
    ),
  )
  const output = {
    observations: {
      schemaVersion: 1,
      protocolId: packet.protocolId,
      protocolVersion: packet.protocolVersion,
      systemId: packet.systemId,
      configurationFingerprint: config.configurationFingerprint,
      corpusFingerprint: packet.corpusFingerprint,
      repetitions: config.repetitions,
      observations,
    },
    budget: budgetSnapshot(),
  }
  await writeFile(config.outputPath, `${JSON.stringify(output)}\n`, 'utf8')
}

await main()
