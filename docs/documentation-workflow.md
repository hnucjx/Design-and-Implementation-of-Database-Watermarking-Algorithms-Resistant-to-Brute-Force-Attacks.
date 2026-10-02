# 文档写作与生成环境

适用读者：需要修改 Markdown、更新 UML 图或审查文档一致性的开发者和维护者。

项目将文档工具链作为工程交付物维护。仓库内的 [docs.py](../scripts/docs.py) 是统一入口：它使用 Python 标准库完成环境初始化、PlantUML 渲染、本地 Markdown 链接检查和 UML 产物一致性检查。开发人员不需要自行安装全局 `plantuml` CLI，也不需要手工寻找 jar。

## 交付策略

| 组成 | 交付方式 | 原因 |
| --- | --- | --- |
| 文档工具入口 | 随仓库提交 `scripts/docs.py`。 | Windows、macOS 和 Linux 可复用同一流程；不额外引入 Python 包。 |
| PlantUML | 首次执行时下载固定版本 `plantuml-mit-1.2026.5.jar` 到 `.tools/docs/`。 | jar 较大，不纳入 Git；版本和 SHA-256 固定，保证可复现。 |
| Java | 开发机系统依赖。 | PlantUML 运行时要求；脚本会在缺失时给出安装提示。 |
| Graphviz | 开发机系统依赖。 | 复杂 UML 布局要求；脚本会在缺失时给出安装提示。 |
| SVG | 随仓库提交到 `docs/assets/diagrams/`。 | GitHub 和本地 Markdown 可直接浏览，无需先搭建工具链。 |

`.tools/` 已加入 [.gitignore](../.gitignore)，用于保存可重新生成的本机工具缓存，不应提交。

仓库根目录的 [.editorconfig](../.editorconfig) 约束 Markdown 和 PlantUML 使用 UTF-8、LF、末尾换行并移除行尾空格。支持 EditorConfig 的 IDE 会自动应用这些写作约定。

## 首次初始化

在仓库根目录执行：

```powershell
python scripts\docs.py bootstrap
```

该命令会：

1. 检查 `java` 和 Graphviz 的 `dot` 是否在 `PATH` 中。
2. 自动下载固定版本 PlantUML jar 到 `.tools/docs/`。
3. 校验 jar 的 SHA-256，拒绝使用不匹配的文件。
4. 输出实际使用的 Java、Graphviz 和 PlantUML 路径。

Windows 缺少系统依赖时，可先执行：

```powershell
winget install Microsoft.OpenJDK.21
winget install Graphviz.Graphviz
```

macOS 可使用 Homebrew：

```bash
brew install openjdk graphviz
```

Ubuntu/Debian 可使用：

```bash
sudo apt-get install default-jre graphviz
```

安装后如终端尚未识别新路径，请重新打开终端再运行 `bootstrap`。

## 更新 UML 图

修改 `docs/diagrams/*.puml` 后执行：

```powershell
python scripts\docs.py render
```

脚本会将全部 UML 源统一渲染为 `docs/assets/diagrams/*.svg`。提交时，`.puml` 和对应 `.svg` 必须一起进入 Git。

## 文档一致性检查

提交前执行：

```powershell
python scripts\docs.py check
```

检查范围：

- `README.md` 与 `docs/**/*.md` 中的本地链接目标是否存在。
- 每个 `docs/diagrams/*.puml` 是否都有对应 SVG，以及是否遗留没有源文件的 SVG。
- 当前 SVG 是否由固定版本 PlantUML 根据当前 `.puml` 源生成。

该命令不会修改已提交 SVG；发现 UML 产物过期时，先运行 `render`，再检查差异。

## 代码行锚点检查

文档里大量使用 `../backend/app/main.py#L98` 这类行锚。代码一改，行号就会漂移，而 Markdown 链接不会报错 —— 它会静默地指向错误的行。用脚本核对：

```powershell
python scripts\check_doc_anchors.py          # 只报告，有漂移时退出码 1
python scripts\check_doc_anchors.py --fix    # 能唯一确定符号的锚点直接重算
```

判定方式是「链接标签是否等于该行的符号名」：标签是符号名（如 `[resolve_proxy](../backend/app/proxy.py#L169)`）就能自动重算；标签是文件名或散文（如 `[main.py](../backend/app/main.py#L117)`）会列进「待人工复核」—— 那是正常项，不是漂移。

但要注意 **`--fix` 只重算「标签是符号名」的那一批**：标签是**文件名**的行锚同样会因行号漂移而失效，脚本不会修它，只能人工改。实测（2026-10-02）：在 `SettingsPanel.tsx` 的组件函数前插入一个说明浮层组件后，指向它的 `#L18` 全部失效 —— 标签是符号名的 `architecture.md` / `implementation.md` 被 `--fix` 自动改成 `#L64`，而标签是文件名的 `design.md` 仍停在 `L18`，只进了「待人工复核」列表。所以**在一个被行锚引用的文件里插行之后，先看一遍「待人工复核」里有没有指向它的条目**，别只看有没有报漂移。

改动 `backend/app/**` 或 `frontend/src/**` 之后跑一次，把结果并进同一个 commit，不要让锚点漂移累积。

## 渲染器版本敏感性

`check` 的一致性判定是**逐字节比较**：它把 `docs/diagrams/*.puml` 复制到临时目录，用当前机器的 Java、Graphviz 和固定版本 PlantUML 现场渲染，再与 `docs/assets/diagrams/*.svg` 逐文件比对。

由此产生一个必须显式接受的约束：

- PlantUML jar 版本由脚本固定并校验 SHA-256，因此不构成变量。
- **Graphviz 版本是变量**。不同 Graphviz 版本的布局算法调整会改变 `dot` 输出的坐标，从而改变 SVG 字节；内容语义不变，但 `check` 会报告「UML SVG 需要重新渲染」。
- 换机器、升级 Graphviz、或首次在未渲染过的环境执行 `check` 时，正确做法是运行 `python scripts\docs.py render`，确认 SVG 差异仅来自布局，然后连同 `.puml` 一起提交。

本仓库最近一次渲染基线：

| 项 | 值 |
| --- | --- |
| PlantUML | `1.2026.5`（`plantuml-mit`） |
| Java | Oracle JDK 25（`java 25.0.3`） |
| Graphviz | 15.1.1 |

该表只用于解释差异来源，不是硬性要求；升级时更新此表并重新渲染即可。

## 推荐写作流程

1. 阅读 [文档中心](index.md) 与 [维护 checklist](maintenance.md#变更-checklist)，确认需要同步更新的文档。
2. 修改 Markdown 和必要的 `.puml` 源文件。
3. 运行 `python scripts\docs.py render`。
4. 运行 `python scripts\docs.py check`、`python scripts\check_doc_anchors.py` 和 `git diff --check`。
5. 涉及代码行为时，再执行 [测试文档](testing.md#自动测试命令) 中的完整验证。
6. 涉及界面时，重拍 `docs/assets/screenshots/` 下受影响的截图并替换；截图不得包含本机用户名路径等个人信息。
7. 使用 `git status -sb` 确认 `.puml` 与 `.svg` 成对提交，且 `.tools/` 未进入 Git。
