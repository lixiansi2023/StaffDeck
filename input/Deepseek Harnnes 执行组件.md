我先探索一下这个平台后端，确认 "Dsh" 具体指什么以及它在项目里是如何使用的。
"Dsh" 在仓库里出现得比较隐晦。让我更精确地查找它到底指什么，以及后端如何使用它。
"Dsh" 是 **DeepSeek Harness（dsh）**，即本仓库的 `harness-engine`（包名 `@deepseek-ai/dsh-*`），基于 Cordis 的依赖注入/插件架构。后端通过它作为 Agent 运行时。让我深入了解后端的集成方式与插件扩展机制。
后端是 Python（FastAPI），而 dsh（harness-engine）是 TypeScript/Cordis 运行时。让我弄清两者如何集成，以及 dsh 自身的插件配置/扩展机制。
后端通过 `backend/vendor/harness-v3/` 拉取并构建 dsh 运行时，并用 `backend/src/staffdeck_harness/` 这个 Python 包桥接。下面我深入读 dsh 的插件配置与扩展文档，以给出准确指引。
我已掌握 dsh 的核心插件模型。再看 StaffDeck 后端具体如何安装/拉起 harness-v3，以及架构文档里的 profile/bundle 配置机制，确保回答贴合本平台。
让我确认 StaffDeck 后端具体如何与 harness 对接（是唯一在 backend 里提到 cordis 的文件），以便区分"改 dsh 运行时插件"与"扩 StaffDeck 自身能力"。
先澄清一个关键点：你提到的 **Dsh = DeepSeek Harness（deepseek-harness）**，仓库里就是 `harness-engine/`（上游源码）和它 vendored 出来的 `backend/vendor/harness-v3/`。它是一个"**全插件、基于 Cordis 依赖注入**"的 Agent 运行时——没有特权核心，所有能力都是平等挂载的插件。

在 StaffDeck 里，后端是这样用它的（`backend/src/staffdeck_harness/bridge/worker.py`）：

```36:48:backend/src/staffdeck_harness/bridge/worker.py
PROFILE_NAME = "sdk"
PATCH_FILENAME = "staffdeck.patch.yml"
...
DISABLED_ROWS: tuple[str, ...] = (
    "tool-bash", "tool-pwsh", "tool-fs", ... "web", "tool-subagent", ... "plan-mode", "goal", ...
)
```

worker 会：① 建一个 **deployment 专属的 `HARNESS_V3_HOME`**（绝不用 `~/.dsh`）；② 写一个 `staffdeck.patch.yml` 补丁（设置中性 persona、禁用引擎原生工具/让模型只走 StaffDeck 的 MCP 代理、挂载 `mcp__staffdeck__*` 能力 server、指向 OpenAI 兼容网关）；③ 用官方 Python SDK 的 `HarnessClient`（stdio JSON-RPC）启动 `dsh --profile sdk --patch <ours>`，每个 Bridge 会话池一个进程。

下面分「配置」和「扩展」两部分说，并落到本平台的实际操作。

---

## 一、dsh 插件系统的本质

一切都是 Cordis 插件。一个插件导出一个 `apply(ctx)`，通过 `ctx` 贡献能力，且**所有注册都是 effect**——插件卸载时自动撤销：

```ts
// 最小插件（函数形态）
import type { Context } from '@deepseek-ai/cordis'
export const name = 'hello'
export function apply(ctx: Context) {
  console.log('hello from my first plugin')
}
```
（另外还有对象形态 `{ name, apply }` 和类形态 `class X extends Service`，需要暴露 service 时才用类。）

应用本身 = 一份 `cordis.yml` 插件树（条目顺序无关，依赖顺序由 `inject` 决定）。

---

## 二、如何「配置」dsh 插件

配置有三种层级，从下到上叠加：

1. **Bundle**：Cordis 配置行 + 代码的分发包，声明在 `package.json` 的 `dsh.bundle`（patch 文件）/`dsh.profile`（它堆叠的 bundle 列表）。
2. **Profile**：命名组合（仓库自带 `web`/`headless`/`sdk`/`sdk-minimal`/`acp`），列出堆叠的 bundle + 用户自己的 `cordis.patch.yml`。
3. **Patch overlay**：按 `id` 定位某行、整段替换其 `config`，或插入新行；顺序为 `bundle 层 → profile 的 cordis.patch.yml → home 级 → --patch 覆盖层`。

**插件自身的配置项**通过 Schemastery schema 校验（不是任意对象），加载时校验失败会**直接 loud-fail 终止进程**：

```ts
export const Config = Schema.object({
  greeting: Schema.string().default('Hello'),
  targets: Schema.array(String).default(['world']),
})
export function apply(ctx: Context, config: Config) { /* ... */ }
```
```yaml
- name: './config-demo.ts'
  config:
    targets: ['alpha', 'beta']
    greeting: !!js process.env.DEMO_GREETING ?? 'Hello'   # 仅 config / disabled 支持 !!js
```

### 在 StaffDeck 平台里「配置」dsh 的实际位置
- 引擎版本：`backend/vendor/harness-v3/fetch-and-build.sh` 第 11 行的 `REF="${2:-v0.1.2-alpha.2}"`，改它 + 重跑脚本即可升降级并重新打补丁（`patch-null-tool-call-chunks.py`）。
- 运行时加载哪些插件/禁用哪些/指向哪个模型网关：**`worker.py` 里 `DISABLED_ROWS`、`RUNTIME_PERSONA`**，以及它生成的 `staffdeck.patch.yml`（运行时落在 `HARNESS_V3_HOME` 下，可直接 `cat` 查看）。要增删被禁用的引擎原生工具，就改 `DISABLED_ROWS`；要改 persona 就改 `RUNTIME_PERSONA`。

---

## 三、如何「扩展」dsh 新插件

按 `extension-cookbook.md` 的几种典型形态，注册都走 `ctx` 的扩展点（**microkernel：没有任何行去改 agent loop**）：

- **工具插件**：`ctx.tools.register(...)`（MCP 来的工具也是走这里）。用 `defineTool` 写一等公民工具。
- **Hook / 权限插件**：`ctx.on('tools/pre-execute', async (exec, next) => { ... return next() })` 返回 `allow`/`deny`/`ask` 决策；需要单调最终拒绝用 `ctx.tools.guard()`，需要包生命周期用 `tools/execute`，做结果变换用 `tools/post-execute`，只读观察用 `tools/result`。
- **UI 插件**：`ctx.on('session/event', ...)` 订阅 `assistant/chunk`、turn/step 边界、工具活动，输入回写用 `agent.followup()` / `agent.steer()`。
- **外部协议驱动**（如 ACP）：适配一个 wire peer 到 `ctx.agents`，用 `AgentHandle.dispose()` 收尾。
- **LLM 适配器**：`LlmAdapter` 子类 + `registerAdapter`（参考 `dsh-llm-deepseek`）。
- **能力缝（capability seam）**：完整扩展点是 **Service Definition / Service Provider / Consumer** 三个角色，演进时只拆角色、不拆单角色。

### 分发成可安装的插件
- 把代码 + 一个 `cordis.patch.yml` 放进 `packages/bundle/<name>/`，在 `package.json` 写 `dsh.bundle` 指向 patch 文件。
- 发布出去后，关联 GitHub `dsh-plugin` topic 方便他人发现（`harness-engine/CONTRIBUTING.md` 第 15 行）。
- **热重载免费**：因为注册都是 `ctx.effect`，vendored HMR 直接生效，无需重启。

---

## 四、调试与查看

```sh
# 看某 profile 实际启动的插件树（StaffDeck 用的是 sdk）
dsh --profile sdk --dump-config
# 你机器上 StaffDeck 实际拉起的树：看 HARNESS_V3_HOME 下的 staffdeck.patch.yml
```

---

## 五、落地到本平台的两条路径（重要区分）

- **想给模型加一个"业务能力/工具"**：在 StaffDeck 的 **Python 侧**做——`backend/src/staffdeck_harness/capabilities/`、`modules/`、`contracts/` 下加 `mcp__staffdeck__*` 工具，经 MCP server 暴露给 dsh 引擎即可（`worker.py` 已挂载该 server）。这**不需要写 dsh 的 TS 插件**。
- **确实要改/加 dsh 引擎侧的 TS 插件**（比如新的 agent 循环扩展点、原生工具）：进入 `harness-engine/`（或 vendored 的 `harness-v3` checkout）写 TS 插件 → 改 bundle 的 `cordis.patch.yml` → `pnpm run build` → 重跑 `fetch-and-build.sh` 重新构建并打补丁。

## 关键文档索引（都在 `harness-engine/docs/`）
- 插件入门：`cordis-tutorial/01-first-plugin.md`、`03-services.md`、`05-config.md`、`06-composition-and-hmr.md`
- 扩展范式：`cookbook/extension-cookbook.md`、`adding-a-tool.md`、`adding-an-llm-adapter.md`、`adding-a-package.md`
- 系统/分层：`architecture.md` 的 "Profiles and bundles" / "Application launch"
- 平台接入点：`backend/src/staffdeck_harness/bridge/worker.py`

需要的话，我可以帮你在 `worker.py` 里具体示范如何新增一个被禁用/启用的引擎插件，或在 `capabilities/` 下加一个 `mcp__staffdeck__*` 工具。