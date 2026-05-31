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

## 推荐写作流程

1. 阅读 [文档中心](index.md) 与 [维护 checklist](maintenance.md#变更-checklist)，确认需要同步更新的文档。
2. 修改 Markdown 和必要的 `.puml` 源文件。
3. 运行 `python scripts\docs.py render`。
4. 运行 `python scripts\docs.py check` 和 `git diff --check`。
5. 涉及代码行为时，再执行 [测试文档](testing.md#自动测试命令) 中的完整验证。
6. 使用 `git status -sb` 确认 `.puml` 与 `.svg` 成对提交，且 `.tools/` 未进入 Git。
