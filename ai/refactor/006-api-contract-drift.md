# 006 - 契约漂移校验：单一来源是运行时的 `app.openapi()`

| 项 | 值 |
| --- | --- |
| 计划时间 | 2026-10-02 17:19 +08:00 |
| 实施时间 | 2026-10-02 17:19 ~ 17:27 +08:00 |
| 依据 | [refactor.md](refactor.md) §4.1 第 3 项「`openapi.yaml` 与 `types.ts` 是两份手写的同一契约」 |
| 起点 commit | `beb5b2c`（R5 提交后） |
| 本轮提交 | `ffe096c`（见文末「提交与回滚」） |
| 结论 | **完成（采用次优形态）**。新增 `scripts/check_api_contract.py`：以运行时 `app.openapi()` 为单一来源，校验 `types.ts` 的 15 个接口字段与 `openapi.yaml` 的 48 个 API 表面条目；**两次注入式取证**证明它抓得住漂移；未改一行契约、未新增依赖 |

---

## 1. 计划

### 1.1 目标

[refactor.md §4.1](refactor.md#41-后续候选清单登记为本轮三轮不做此后逐项单独一轮) 第 3 项：

> **`openapi.yaml` 与 `frontend/src/types.ts` 是两份手写的同一契约**（28 operations / 206 行类型）。
> 理想形态是单一来源 + 生成，但生成器会引入工具链与 npm 依赖，违反 §3.1 第 6 条，需单独决策。

同一个契约有三份表达：

| 表达 | 位置 | 谁写的 | 会不会自动跟随代码 |
| --- | --- | --- | --- |
| **可执行契约** | `app.openapi()`（运行时生成） | 由 `schemas.py` + 各路由的响应模型推导 | **会** |
| 人类可读文档 | `docs/openapi.yaml` | 手写 | 不会 |
| 前端类型 | `frontend/src/types.ts` | 手写 | 不会 |

三份里只有第一份不会漂。本项要解决的是「后两份悄悄与第一份分叉，直到运行时才炸」。

### 1.2 边界：本轮**不做**的事

1. **不引入代码生成器** —— 这是本项**唯一**必须让步的地方，理由见 §2.1。
2. **不新增任何依赖**：PyYAML 做**软依赖**（有则多校验一层，没有就跳过并**打印原因**）。
3. **不改任何契约**：本轮 `types.ts` / `openapi.yaml` / `schemas.py` / 路由**一行不动**
   —— 唯一的改动是**新增**一个校验脚本。因此「行为不变」是平凡的（没有行为被改）。
4. **不做深度结构比对**，理由见 §2.2。
5. **不做类型比对**（只比字段名），这是本项**最大**的覆盖缺口，见 §5 第 2 条。

### 1.3 风险与对策

| 风险 | 对策 |
| --- | --- |
| 校验脚本永远绿、其实抓不住真漂移（「看起来写了不等于真的生效」） | 两次**注入式取证**：主动制造两种漂移，确认脚本 EXIT=1 并精确报出（§4.2） |
| 校验过严 → 长期红着没人看 → 变成噪音 | 「有意差异」登记成**显式常量**；只有**未登记**的差异才失败（§2.3） |
| 依赖 PyYAML，CI 里没装 → 静默跳过，让人误以为已经校验过 | 软依赖 + **显式打印跳过原因**；并把这一事实写进记录（§5 第 5 条）与文档 |

---

## 2. 实施方案

### 2.1 为什么是「校验」而不是「生成」

生成 `types.ts` / `openapi.yaml` 的三条路都会撞 [§3.1 第 6 条](refactor.md#31-目标与非目标)：

| 想生成 | 需要什么 | 撞哪条 |
| --- | --- | --- |
| `types.ts` | npm 侧的类型生成工具（`openapi-typescript` 之类） | 前端不新增 npm 包 |
| `openapi.yaml` | 一个 YAML 序列化器 | 后端不新增 pip 包；且**会丢掉手写内容**（见下） |
| 两者 | 自研生成器 + 模板 + 维护成本 | 不新增依赖，但生成器的保真度要长期维护 |

更关键的是：**手写文档里有 FastAPI 自动 schema 表达不了的东西** —— `example` / `default` /
`servers` / `tags` / 领域化的错误命名（`Error` 而不是 `HTTPValidationError`）。
生成会把它们全部抹平，把「给人看的文档」降级成「schema 的转写」。

所以本轮取 **「单一来源 + 漂移校验」**：单一来源 = `app.openapi()`；两份手写副本**不生成**，
但任何未登记的漂移都会让 CI 红。**候选 3 的「理想形态」没有达成，达成的是约束下的次优形态**
—— 这一点在 §5 第 1 条明确承认。

### 2.2 为什么不深度比对（实测数据）

调研脚本 `tmp_acceptance/probe_contract.py` 的输出：

```text
runtime paths: 24 / yaml paths: 24   （operations 28 vs 28，路径集合相同）
runtime schemas: 27 / yaml schemas: 28
剔除 description 后全等: False ｜ 差异条数: 731

主要来源：
  - title             runtime 有（FastAPI 自动加），yaml 没有
  - example / default yaml 有（手写增强），runtime 没有
  - nullable 表达     runtime 用 anyOf: [T, null]，yaml 用 type: T
  - 文档特有          servers / tags / components.parameters / components.responses
  - 运行时特有        Body_upload_cookies_api_cookies_post / HTTPValidationError
```

731 条差异会**淹没**真信号（少一个字段）。因此本轮的校验**只盯两处**：

| 校验对象 | 判据 | 为什么选它 |
| --- | --- | --- |
| `types.ts` | 每个接口的**字段名集合**与对应运行时 schema **完全相同** | 前端读了一个后端不返回的字段，是最常见的分叉 |
| `openapi.yaml` | **路径 × 方法**集合 + **paths 引用的 schema** 集合 | 「文档里的接口不存在 / 文档引用了不存在的 schema」是最危险的漂移 |

### 2.3 登记在案的两组「有意差异」

`docs/openapi.yaml` 在两处**刻意**不跟随 FastAPI 的自动命名，写成常量：

```python
DOCUMENT_SCHEMA_SURPLUS = {"CookieImportError", "Error"}                      # 领域化错误命名
RUNTIME_SCHEMA_SURPLUS  = {"Body_upload_cookies_api_cookies_post", "HTTPValidationError"}
```

判据是「差异**恰好等于**这两组」。多一个或少一个都会失败 —— 差异本身**被文档化**了，
而不是被静默容忍。

### 2.4 接口名映射表（`SCHEMA_PAIRS`）

后端用 `Read` 后缀区分读/写模型（`JobItemRead` / `JobItem` 是两件事），前端只消费读模型，
所以名字对不上。这张表是**显式**的，15 个可比对 + 2 个标记为 `None`：

```text
JobItem → JobItemRead      Job → JobRead            Settings → SettingsRead
ProxyTestResult → ProxyTestRead                     Diagnostics → DiagnosticsRead
CookieHealth → CookieHealthRead                     其余 9 个同名
EnvironmentRisk → None（运行时里是 array of object(str→str)，没有具名 schema）
ApiErrorDetail  → None（错误 detail 不在运行时的 schemas 里）
```

标 `None` 的**不是跳过**：它们仍要求「必须存在于 `types.ts`」，且脚本会额外检查
「`types.ts` 里没有未登记的接口」—— 新加的接口必须进这张表，否则失败。

---

## 3. 实施情况

| 指标 | 值 |
| --- | --- |
| 新增 | `scripts/check_api_contract.py`（245 行） |
| 改动的既有文件 | **无**（本轮只新增） |
| 校验覆盖 | `types.ts` 15 个接口；`openapi.yaml` 48 个条目（28 operations + 20 引用 schema） |
| 新增依赖 | **零**（PyYAML 为软依赖，见 §5 第 5 条） |

---

## 4. 验证

### 4.1 门槛（[refactor.md §5](refactor.md#5-每轮的固定流程dod) 第 3 步）

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| **契约校验** | `python scripts/check_api_contract.py` | **EXIT=0**（`types.ts` 15 接口 + `openapi.yaml` 48 条目全部一致） |
| 后端测试 | `./.venv/Scripts/python.exe -m pytest backend/tests -q -p no:cacheprovider --basetemp=tmp_pytest/runNNN` | **354 passed**（未改后端源码，回归确认） |
| 前端测试 | `cd frontend && npx vitest run --environment jsdom` | **69 passed**（未改前端源码） |
| 代码锚点 | `python scripts/check_doc_anchors.py` | 无漂移 |
| 文档链接与 UML | `python scripts/docs.py check` | 通过 |
| 空白错误 | `git diff --check` | 无输出 |

### 4.2 取证：证明它抓得住（[§3 第 6 条](refactor.md#3-判定准则)）

「校验脚本能通过」是弱证据 —— 一个永远返回 0 的脚本也能通过。强证据是**主动注入漂移**：

| 实验 | 注入 | 期望 | 实测 |
| --- | --- | --- | --- |
| 1 | 在 `types.ts` 的 `JobItem` 里加 `zzz_probe_field: string` | 字段集合不一致 | **EXIT=1**，报 `JobItem ↔ JobItemRead 字段不一致：types.ts 多 ['zzz_probe_field']` |
| 2 | 把 `openapi.yaml` 的 `/health` 改名 `/health-probe` | 路径集合不一致 | **EXIT=1**，报 `路径 × 方法不一致：文档多 [('/health-probe', 'get')]，文档少 [('/health', 'get')]` |

两次注入后均已还原，`git diff frontend/src/types.ts docs/openapi.yaml` 为空。
还原后复跑：**EXIT=0**。

### 4.3 同步的文档

| 文档 | 改动 |
| --- | --- |
| `docs/api.md` | 补「契约漂移校验」：说明单一来源是 `app.openapi()`、校验命令与覆盖范围 |
| `refactor.md` §4.1 第 3 项 / §6 / 顶部时间 | 状态就地更新 |

---

## 5. 未覆盖 / 如实说明

1. **候选 3 的「理想形态」没有达成。** 本轮交付的是「单一来源 + 漂移校验」，不是「单一来源 + 生成」。
   两份手写副本**仍然存在**，只是分叉会被拦。这是 [§3.1 第 6 条](refactor.md#31-目标与非目标)
   约束下的让步，不是遗漏 —— 若要真正生成，需要先决策「是否放开依赖约束」。

2. **字段名比对不校验类型 —— 这是本轮最大的覆盖缺口。**
   `JobItem.progress` 从 `number` 改成 `string`、`Settings.proxy` 从 `string | null` 改成 `string`，
   **都不会被抓到**（字段名没变）。做类型比对需要一套 TS ↔ OpenAPI 的类型映射，
   而 nullable（`anyOf` vs `type`）、`Record<string, X>`、联合字面量三种表达在两侧写法不同，
   误报率高到会让校验失去意义。**这条要按「已知缺口」对待，不要当成已经覆盖。**

3. **`EnvironmentRisk` / `ApiErrorDetail` 没有字段比对。** 运行时那边前者是
   `array of object(str→str)`（内联）、后者根本不在 schemas 里，因此只做「接口必须存在」的存在性断言。
   它们恰恰是**前端比后端更具体**的两个类型 —— 若后端真的改了这两个结构，本轮校验不会响。

4. **`openapi.yaml` 只比「API 表面」。** 不比请求体字段、响应体字段、状态码、参数位置。
   也就是说「文档说 `/api/jobs` 返回 `JobRead`，而代码确实返回 `JobRead`」这类**结构内部**的漂移不查；
   查的是「文档提到了一个代码里不存在的路径 / 引用了不存在的 schema」。

5. **CI 里 `openapi.yaml` 那一半可能被跳过。** 脚本对 PyYAML 是**软依赖**：装了就多校验一层，
   没装就打印「跳过（本机没有 PyYAML）」并**仍以 0 退出**。本机开发环境里有 PyYAML（传递依赖），
   所以本地是全量校验；但**如果 CI 环境没有它，CI 实际只强校验 `types.ts`**。
   这一点必须写清楚，否则会误以为 CI 覆盖了 `openapi.yaml`。
   （不把 PyYAML 加进 `pyproject.toml` 是刻意的：那属于「新增依赖」，需要单独决策。）

6. **脚本有副作用**：`import app.main` 会执行 `main.py` 末尾模块级的 `app = create_app()`，
   用默认 settings 在**仓库根**建 `data/`（含日志与 sqlite）。该目录本就在 `.gitignore` 第 23 行，
   但这是脚本的副作用而不是设计意图 —— 如实记录，以免有人看到凭空出现的 `data/` 时误判。

---

## 6. 风险与回滚

| 项 | 值 |
| --- | --- |
| 风险等级 | 低（纯新增脚本；不改任何运行时代码） |
| 回滚命令 | `git revert <hash>` |
| 回滚影响 | 无。删掉脚本即回到「三份契约靠自觉保持一致」的状态 |

---

## 7. 关联

- **上游**：[refactor.md](refactor.md) §4.1 第 3 项、§3.1 第 6 条（本轮的让步来自它）。
- **互补**：[007](007-ci-and-layers.md) 会把这个脚本接进 CI —— 届时「漂移让 CI 红」才真正成立；
  在 007 之前，本脚本只是「一个可以手动跑的命令」。
- **同类**：[004](004-artifact-paths.md) / [005](005-safe-delete.md) 都是「把只能靠跑起来才知道的事
  变成能直接跑的命令」；本轮是同一思路在**跨端契约**上的应用。

---

## 8. 提交与回滚

| 项 | 值 |
| --- | --- |
| 提交 hash | `ffe096c`（2026-10-02 收尾一轮回填） |
| 回滚命令 | `git revert ffe096c` |
| 记录约定 | 与 [bug-fix](../bug-fix/README.md) / [ui](../ui/README.md) 一致：下次触碰 `ai/refactor/` 时回填 hash |
