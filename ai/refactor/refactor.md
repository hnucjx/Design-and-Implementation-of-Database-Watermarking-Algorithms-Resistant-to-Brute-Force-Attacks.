# 重构总纲（refactor charter）

> 最后更新：**2026-10-02 17:10 +08:00** ｜ 基线 commit：`3643e76`（main）
> 本文件是重构的**总纲与索引**。每轮重构的「计划 / 实施方案 / 实施情况」各自成文，见 [§6 轮次索引](#6-轮次索引)。

本仓库已有的三份记录目录各管一件事，不要混：
[`ai/bug-fix/`](../bug-fix/README.md) 记**功能坏了**、[`ai/ui/`](../ui/README.md) 记**界面不对**、
`ai/refactor/`（本目录）记**功能没坏但结构该动**。

---

## 1. 这份总纲解决什么问题

到 2026-08-14，本仓库已经做过 7 轮结构重构（[§7](#7-历史迭代2026-08-14-前共-7-轮)），把降级策略、读模型投影、
cookies 导入、格式工具、前端工具、任务中心、测试夹具陆续拆了出去。当时基线是 **后端 75 / 前端 28**。

此后近两个月，仓库的功能面显著变厚（**后端 289 / 前端 69**，见 [§2](#2-基线快照)），但结构上留了三处
「上一轮没动、现在最贵」的欠账：

| 位置 | 现状 | 为什么现在是问题 |
| --- | --- | --- |
| `frontend/src/App.tsx` | **1128 行**，一个文件里塞了 1 个状态容器 + 7 个内部组件（5 个展示组件 + 2 个基础件）+ 5 个文案/映射函数 | 展示改版要碰状态容器；状态改动要通读 1100 行。两者变化频率完全不同，却被绑在一个文件里 |
| `backend/app/main.py` | **722 行**，其中 `create_app` 一个函数 **458 行**、内联声明 **28 条路由** | 组合根同时承担了「路由声明 + 请求校验 + 路径解析 + 设置读写」。新增一个接口要在一个 458 行的函数里找位置 |
| `backend/app/job_manager.py` | **1174 行**，其中「清晰度降级」的**决策 + 原因 + 用户文案**散在 10 个方法里 | 降级是本项目历史上唯一反复出缺陷的策略（[010](../bug-fix/010-unselectable-probe-raise-skips-the-fallback.md) 整段降级曾是死代码），而它的判据至今只能靠跑整条下载链路来验 |

本总纲据此定下路线图（[§4](#4-路线图)），并把「什么样才算做完」写成可执行判据（[§3](#3-判定准则) + [§5](#5-每轮的固定流程dod)）。

---

## 2. 基线快照

**取证时间：2026-10-02 16:03 ~ 16:06 +08:00 ｜ 基线 commit：`3643e76`**

| 项 | 值 | 取证命令 |
| --- | --- | --- |
| 后端测试 | **289 passed**（72.56s，1 warning） | `./.venv/Scripts/python.exe -m pytest backend/tests -q -p no:cacheprovider --basetemp=tmp_pytest/run100` |
| 前端测试 | **69 passed**（13.45s） | `cd frontend && npx vitest run --environment jsdom` |
| 前端构建 | 通过 | `cd frontend && npm run build` |
| 后端模块 | 29 个 `.py`（`__init__.py` 仅 1 行，计 28 个模块），合计 6026 行 | `ls backend/app/*.py \| wc -l` |
| 前端源码 | 13 个文件，合计 4699 行 | `wc -l frontend/src/**` |
| 文档锚点 | 无漂移（另有 111 处「待人工复核」，属正常项） | `python scripts/check_doc_anchors.py` |
| UML | 15 张 `.puml` + 15 张 `.svg` | `ls docs/diagrams` |

**规模最大的 8 个文件**（改之前先看这张表，它基本就是「重构候选」的排序）：

| 文件 | 行数 | 备注 |
| --- | --- | --- |
| `backend/app/ytdlp_service.py` | 1182 | 90 个方法，其中约 20 个是上一轮留下的兼容代理 |
| `backend/app/job_manager.py` | 1174 | 队列/worker/状态机 + 降级策略 + 文案 |
| `frontend/src/App.test.tsx` | 1521 | 单文件 69 个用例 |
| `frontend/src/App.tsx` | 1128 | 见 §1 |
| `backend/app/main.py` | 722 | 见 §1 |
| `frontend/src/components/JobQueue.tsx` | 512 | 已是独立组件，边界清楚 |
| `backend/tests/test_api.py` | 2208 | — |
| `backend/app/schemas.py` | 256 | 线上契约，**不动** |

### 2.1 每轮结束后的「刷新」（随轮次滚动更新）

上表是**写这份总纲时**的快照。每轮重构都会改动其中若干项，因此每轮结束时把新值记在这里，
而不是回头改写上表（保留"改之前长什么样"的证据）。

| 项 | 写总纲时（R1 前） | R2 后 | **R3 后（当前）** | 取证命令 |
| --- | --- | --- | --- | --- |
| 后端模块 | 29 个 `.py` / 6026 行 | 32 个 `.py` / 5791 行，另有 `routers/` 8 个 `.py` / 596 行 | **33 个 `.py`**，另有 `routers/` 8 个 | `ls backend/app/*.py \| wc -l`、`wc -l backend/app/*.py \| tail -1` |
| 前端源码 | 13 个文件 / 4699 行 | 23 个文件 / 4790 行（R1 后） | **23 个文件 / 4790 行** | `find frontend/src -name '*.ts' -o -name '*.tsx' \| wc -l` |
| `backend/app/main.py` | 722 行 | 130 行 | **130 行** | `wc -l backend/app/main.py` |
| `backend/app/job_manager.py` | 1174 行 | 1174 行 | **1142 行**（−32，判定外移到新模块） | `wc -l backend/app/job_manager.py` |
| `frontend/src/App.tsx` | 1128 行 | 446 行（R1 后） | **446 行** | `wc -l frontend/src/App.tsx` |
| 后端测试 | 289 passed | 289 passed | **320 passed** | 见上表命令 |
| 前端测试 | 69 passed | 69 passed | **69 passed** | 见上表命令 |
| 文档锚点 | 无漂移 / 111 处待复核 | 无漂移 / 148 处待复核 | **无漂移 / 153 处待复核** | `python scripts/check_doc_anchors.py` |

---

## 3. 判定准则

**这一节是判断「该不该动、动完算不算好」的依据。**前四条是现代前后端工程通用准则，后两条是本仓库
用真实缺陷换来的教训 —— 它们在本仓库的命中率比前四条更高。

| # | 准则 | 落到本仓库的判据 |
| --- | --- | --- |
| 1 | **单一变化原因**：一个模块只应该因为一类原因被修改 | 展示改版不该碰状态容器；路由新增不该碰路径解析 |
| 2 | **依赖方向单一且向下**：`L0 入口 → L1 契约 → L2 编排 → L3 领域 → L4 基础设施 → L5 纯工具` | 箭头只向下（见 [design.md](../../docs/design.md#模块职责矩阵)）。L3 不许 import L2/L0 |
| 3 | **组合根只做装配** | `create_app` 里出现业务分支即为越界 |
| 4 | **纯逻辑与 IO 分离**：判据是「能不能不起进程、不碰网络、不写库地单测它」 | 降级决策、路径候选、错误分类都应可纯函数化 |
| 5 | **同一概念只允许有一处计算**（本仓库教训） | 同一个量在两处各算一次，就是明天的分叉。[010](../bug-fix/010-unselectable-probe-raise-skips-the-fallback.md) 的降级判据、[009](../bug-fix/009-local-dev-port-is-occupied-and-unconfigurable.md) 的端口值、[002](../ui/002-side-column-overflow-breaks-the-page.md) 的排版约束，全是这一类 |
| 6 | **「看起来写了」不等于「真的生效」**（本仓库教训） | 上一轮 [010](../bug-fix/010-unselectable-probe-raise-skips-the-fallback.md)（降级挂在错误的信号上）与 [003](../ui/003-language-trigger-label-overflows-the-page.md)（`text-overflow: ellipsis` 从未生效）同属一类。**重构里凡声称「行为不变」，必须给出可执行的对照取证，不能只写「已确认无变化」** |

### 3.1 目标与非目标

**目标**：在不改变对外行为的前提下，让「改一处」只碰一个文件；让高频变化的部分（展示、路由、文案）
与低频稳定的部分（契约、状态机、下载参数）分居不同模块。

**非目标（硬约束，任一被破坏即视为本轮失败）：**

1. **不改 HTTP 线上形状**：路径、方法、状态码、字段名、字段语义、错误 `detail` 结构全部保持。
2. **不改数据库语义**：表名、列名、状态机取值、补列顺序全部保持。
3. **不改环境变量**：名字、默认值、优先级全部保持。
4. **不改下载行为**：选流、降级、重试层级、cookies 刷新时机全部保持。
5. **不改用户可见文案**：界面与 API 里出现的每一句中文都不动（文案调整属另一件事）。
6. **不新增运行时依赖**：后端不新增 pip 包，前端不新增 npm 包（`devDependencies` 亦不新增）。
7. **不追求覆盖率数字**：本轮只做结构，新增测试仅用于给新抽出的纯逻辑兜底。

---

## 4. 路线图

| 轮次 | 主题 | 目标 | 状态 |
| --- | --- | --- | --- |
| **R1** | 前端视图层解耦 | `App.tsx` 的 6 个内部组件 + 3 个文案函数外移，`App.tsx` 只留状态编排与布局 | 见 [001](001-frontend-view-layer.md) |
| **R2** | 后端 API 层解耦 | `main.py` 的 24 条路由按领域拆成 `routers/`，路由支撑逻辑下沉，`create_app` 退化为装配根 | 见 [002](002-backend-api-layer.md) |
| **R3** | 后端领域层：清晰度降级决策纯化 | 「降级候选 + 降级原因 + 用户文案」从 `job_manager.py` 抽成可单测的领域模块 | 见 [003](003-resolution-decisions.md) |

顺序是刻意的：**R1 风险最低（纯前端、无 IO）、R2 影响面最大（线上契约的入口）、R3 价值最高
（唯一反复出缺陷的策略）**。R1 同时用来验证 §5 的流程本身跑得通。

### 4.1 后续候选清单（登记为「本轮三轮不做」，此后逐项单独一轮）

写在这里是为了**防止单轮范围膨胀**：发现了就登记；后续**一项一轮**地做（每项一次 commit + push）。
状态在每轮完成后就地更新。

1. **产物路径有两条计算链。** `job_artifacts.py` 的 `output_file` / `item_folder` / `job_folder`
   （R2 从 `main.py` 原样搬出）与 `job_manager.py` 的 `_item_output_paths` 各算一次「这个条目的文件在哪」，命中 §3 第 5 条。
   收敛它们需要同时改 API 层与编排层，与 R2 的边界重叠，拆成独立一轮更干净。
   **✅ 已完成 → [004](004-artifact-paths.md)（2026-10-02）**
2. **`job_manager.py` 的「安全删除」白名单**（`_delete_output_files` / `_is_under_allowed_root`）
   是路径安全的承重逻辑，却只能靠跑任务来验 —— 它应该和 R3 同类地纯函数化，但属另一个主题。
3. **`openapi.yaml` 与 `frontend/src/types.ts` 是两份手写的同一契约**（28 operations / 206 行类型）。
   理想形态是单一来源 + 生成，但生成器会引入工具链与 npm 依赖，违反 §3.1 第 6 条，需单独决策。
4. **没有 lint / format / CI 门槛**：仓库无 `.github/`、无 ruff/eslint/prettier 配置。
   引入它们会对 6000+ 行既有代码产生大量机械改动，属独立议题。
5. **`frontend/src/App.test.tsx`（1521 行 / 69 用例）未按功能拆分。**
6. **`ytdlp_service.py` 里约 20 个上一轮留下的兼容代理方法**（`_format_selector` 等），
   现在只剩「保持测试与调用点不破」的作用，可评估回收。

---

## 5. 每轮的固定流程（DoD）

一轮 = 一个主题 = 一个 commit = 一次 push。**以下 5 步全绿才算完成**，缺哪步就在记录里如实标未完成。

| 步 | 动作 | 判据 |
| --- | --- | --- |
| 1 | 写**计划**（边界、不动什么、风险） | 计划里必须写明「本轮不做的事」 |
| 2 | 实施（一次只做一个主题） | `git status` 只出现本轮范围内的文件 |
| 3 | **验证** | 后端 `320 passed`、前端 `69 passed`、`npm run build` 通过；改了 `backend/app/**` 或 `frontend/src/**` 后跑 `python scripts/check_doc_anchors.py`（需 `--fix`）与 `python scripts/docs.py check` |
| 4 | 写**记录**（本目录 `00N-*.md`） | 含明确时间戳；含「未覆盖 / 如实说明」小节 |
| 5 | 单独 `git commit` + `git push` | `git ls-remote` 校验 sha；提交列表显式列文件，**不用 `git add -A`** |

**环境注意（本机沙箱专有，别踩第二次）：**

- 后端测试必须给 `--basetemp` 指到仓库内，否则 pytest 收尾清理系统 Temp 会被 safe-delete 拦截，
  **连摘要行都打不出来**：
  `./.venv/Scripts/python.exe -m pytest backend/tests -q -p no:cacheprovider --basetemp=tmp_pytest/runNNN`
- **`--basetemp` 指的目录必须不存在**（R2 实测）。它若已存在，pytest 会在会话启动时装整个
  `rmtree` 它；工作区内一次删 137 个文件会触发沙箱批量删除守卫
  （`SAFE_DELETE_BULK_CONFIRM_REQUIRED`，阈值 50），表现为**每个用 `tmp_path` 的用例
  error at setup**（实测 104 passed / 185 errors），而错误栈指向 `pytest_asyncio` 的 fixture 钩子，
  **看起来像插件坏了**。换一个从未用过的 `runNNN` 即恢复。
- 后端依赖只在仓库根 `.venv`（`sqlalchemy` / `sqlmodel` / `yt_dlp` 都在那里）；
  managed 解释器跑 pytest 会 6 个模块收集失败，那是环境问题不是代码问题。
- `npm run build` 的 `emptyDir` 走 `rmSync` 会被拦，需要时改用 `--outDir` 指向新目录。
- **`module-dependencies.svg` 的渲染是不确定的**（同一份源码两种字节，实测各 3 次）：
  `docs.py check` 报它「需要重新渲染」时先重跑一次复核，别急着改文档。

---

## 6. 轮次索引

「提交」列按 [bug-fix](../bug-fix/README.md) / [ui](../ui/README.md) 同样的约定处理：记录与改动在同一次提交里，
hash 只能在**下一次触碰本目录时**回填；查某条改动用 `git log --oneline -- ai/refactor/NNN-*.md`。

| # | 标题 | 提交 | 一句话 |
| --- | --- | --- | --- |
| [001](001-frontend-view-layer.md) | 前端视图层解耦：`App.tsx` 只留状态编排 | `c385646` | 1128 行里 7 个内部组件 + 5 个纯函数外移到 9 个新文件，展示改版与状态编排从此可以各改各的 |
| [002](002-backend-api-layer.md) | 后端 API 层解耦：`main.py` 退化为装配根 | `046e4de` | 458 行的 `create_app` 拆成 7 个 `APIRouter` + 3 个支撑模块（`api_context` / `api_support` / `job_artifacts`），新增接口不必再在巨型函数里找位置 |
| [003](003-resolution-decisions.md) | 降级决策纯化：从「跑一次下载才知道」到「可直接单测」 | 回填 | 判定收进 `resolution_decisions.py`（4 个纯函数），`job_manager` 只剩 IO 与状态写入；新增 31 例单测，8 个场景在 HEAD 与重构后逐字段相同 |
| [004](004-artifact-paths.md) | 产物路径收敛：候选链只有一个计算处 | 回填 | `job_artifacts` 与 `job_manager._item_output_paths` 的候选链下沉到 `output_paths.py`；12 场景 × 3 入口的旧/新逐项对照（§4.1 第 1 项） |

---

## 7. 历史迭代（2026-08-14 前，共 7 轮）

原文为英文流水账，此处保留为索引（commit 已回填）。这 7 轮的共同点是**「按职责边界外移」**，
与本总纲 §3 的准则同源。

| 轮次 | 主题 | 提交 | 备注 |
| --- | --- | --- | --- |
| H1 | 集中降级策略（reason 常量 + 响应构造） | `7d4a216` | 降级是领域逻辑，不是路由接线 |
| H2 | 抽出任务读模型投影 | `fede7bd` | 路由只留 404 包装 |
| H3 | 抽出浏览器 cookies 导入器 | `f476ef4` | 把 Edge 锁库 / DPAPI / CDP 从下载服务里剥离 |
| H4 | 抽出 yt-dlp 格式工具 | `2319a0b` | 选择器、分辨率/格式提取、降级候选 |
| H5 | 抽出前端工具函数 | `213d8a6` | `formatting.ts` / `quality.ts` |
| H6 | 抽出任务中心组件 | `3e2db9e` | `components/JobQueue.tsx` |
| H7 | 整理测试夹具 | `55d38b4` | `backend/tests/fakes.py`、`frontend/src/test/appFixtures.ts` |

7 轮之后基线为 **后端 75 / 前端 28**；本轮（2026-10-02）基线为 **289 / 69**。
