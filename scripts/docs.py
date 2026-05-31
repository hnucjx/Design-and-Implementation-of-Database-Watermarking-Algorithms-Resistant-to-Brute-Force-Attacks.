"""Bootstrap, render, and validate the repository documentation toolchain."""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.error import URLError
from urllib.parse import unquote
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
DOCS_DIR = ROOT / "docs"
DIAGRAMS_DIR = DOCS_DIR / "diagrams"
ASSETS_DIR = DOCS_DIR / "assets" / "diagrams"
TOOLS_DIR = ROOT / ".tools" / "docs"

PLANTUML_VERSION = "1.2026.5"
PLANTUML_FILENAME = f"plantuml-mit-{PLANTUML_VERSION}.jar"
PLANTUML_URL = (
    "https://repo1.maven.org/maven2/net/sourceforge/plantuml/"
    f"plantuml-mit/{PLANTUML_VERSION}/{PLANTUML_FILENAME}"
)
PLANTUML_SHA256 = "0043b32c7fa8f173e5a762f750fbf73a0e2feb274f5ee3ddc440fe8164317aa9"

MARKDOWN_LINK_RE = re.compile(r"!?\[[^\]]*]\(([^)]+)\)")


class DocumentationToolError(RuntimeError):
    """Raised when the local documentation toolchain is unavailable or invalid."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require_command(name: str, install_hint: str) -> str:
    command = shutil.which(name)
    if command:
        return command
    raise DocumentationToolError(f"缺少 `{name}`。请先安装：{install_hint}")


def download_with_resume(url: str, destination: Path, retries: int = 5) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(f"{destination.suffix}.part")

    for attempt in range(1, retries + 1):
        downloaded = partial.stat().st_size if partial.exists() else 0
        headers = {"Range": f"bytes={downloaded}-"} if downloaded else {}
        request = Request(url, headers=headers)
        try:
            print(f"下载 {PLANTUML_FILENAME}：第 {attempt}/{retries} 次尝试，已缓存 {downloaded} bytes")
            with urlopen(request, timeout=60) as response:
                append = downloaded > 0 and getattr(response, "status", None) == 206
                base_size = downloaded if append else 0
                content_length = response.headers.get("Content-Length")
                expected_size = base_size + int(content_length) if content_length else None
                with partial.open("ab" if append else "wb") as output:
                    shutil.copyfileobj(response, output, length=1024 * 1024)
            if expected_size is not None and partial.stat().st_size != expected_size:
                raise OSError(
                    f"下载不完整：期望 {expected_size} bytes，实际 {partial.stat().st_size} bytes"
                )
            partial.replace(destination)
            return
        except (OSError, URLError) as exc:
            if attempt == retries:
                raise DocumentationToolError(f"下载 PlantUML 失败：{exc}") from exc
            time.sleep(min(2**attempt, 8))


def ensure_plantuml_jar() -> Path:
    jar = TOOLS_DIR / PLANTUML_FILENAME
    if jar.exists() and sha256(jar) == PLANTUML_SHA256:
        return jar
    if jar.exists():
        jar.unlink()

    for integrity_attempt in range(1, 4):
        download_with_resume(PLANTUML_URL, jar)
        actual = sha256(jar)
        if actual == PLANTUML_SHA256:
            return jar
        jar.unlink(missing_ok=True)
        print(
            f"PlantUML SHA-256 校验失败：期望 {PLANTUML_SHA256}，实际 {actual}；"
            f"重新下载 {integrity_attempt}/3"
        )
    raise DocumentationToolError("PlantUML SHA-256 连续校验失败，请检查网络代理或 Maven Central 连通性。")


def bootstrap() -> tuple[str, str, Path]:
    java = require_command("java", "Windows 可运行 `winget install Microsoft.OpenJDK.21`")
    dot = require_command("dot", "Windows 可运行 `winget install Graphviz.Graphviz`")
    jar = ensure_plantuml_jar()
    print(f"Java：{java}")
    print(f"Graphviz：{dot}")
    print(f"PlantUML：{jar.relative_to(ROOT)}")
    print(f"PlantUML SHA-256：{PLANTUML_SHA256}")
    return java, dot, jar


def render_to(diagrams_dir: Path, assets_dir: Path) -> None:
    java, _, jar = bootstrap()
    diagrams = sorted(diagrams_dir.glob("*.puml"))
    if not diagrams:
        raise DocumentationToolError(f"没有找到 UML 源文件：{diagrams_dir}")
    assets_dir.mkdir(parents=True, exist_ok=True)
    relative_output = Path(os.path.relpath(assets_dir, start=diagrams_dir))
    subprocess.run(
        [java, "-jar", str(jar), "-tsvg", *map(str, diagrams), "-o", str(relative_output)],
        cwd=ROOT,
        check=True,
    )


def render() -> None:
    render_to(DIAGRAMS_DIR, ASSETS_DIR)
    print(f"已渲染 {len(list(DIAGRAMS_DIR.glob('*.puml')))} 张 UML 图。")


def markdown_files() -> list[Path]:
    return [ROOT / "README.md", *sorted(DOCS_DIR.rglob("*.md"))]


def local_link_errors() -> list[str]:
    errors: list[str] = []
    for markdown in markdown_files():
        for line_number, line in enumerate(markdown.read_text(encoding="utf-8").splitlines(), start=1):
            for raw_target in MARKDOWN_LINK_RE.findall(line):
                target = raw_target.strip().split(maxsplit=1)[0].strip("<>")
                if not target or target.startswith(("#", "http://", "https://", "mailto:")):
                    continue
                relative_path = unquote(target.split("#", 1)[0])
                if relative_path and not (markdown.parent / relative_path).resolve().exists():
                    errors.append(f"{markdown.relative_to(ROOT)}:{line_number}: 找不到 `{target}`")
    return errors


def generated_diagram_errors() -> list[str]:
    errors: list[str] = []
    source_stems = {source.stem for source in DIAGRAMS_DIR.glob("*.puml")}
    asset_stems = {asset.stem for asset in ASSETS_DIR.glob("*.svg")}
    for orphan in sorted(asset_stems - source_stems):
        errors.append(f"UML SVG 没有对应源文件：{(ASSETS_DIR / f'{orphan}.svg').relative_to(ROOT)}")

    with tempfile.TemporaryDirectory(prefix="youtube-downloader-docs-") as temporary:
        temporary_root = Path(temporary)
        temporary_diagrams = temporary_root / "diagrams"
        temporary_assets = temporary_root / "assets"
        shutil.copytree(DIAGRAMS_DIR, temporary_diagrams)
        render_to(temporary_diagrams, temporary_assets)

        for source in sorted(DIAGRAMS_DIR.glob("*.puml")):
            expected = ASSETS_DIR / f"{source.stem}.svg"
            rendered = temporary_assets / f"{source.stem}.svg"
            if not expected.exists():
                errors.append(f"缺少 UML SVG：{expected.relative_to(ROOT)}")
            elif expected.read_bytes() != rendered.read_bytes():
                errors.append(f"UML SVG 需要重新渲染：{expected.relative_to(ROOT)}")
    return errors


def check() -> None:
    errors = [*local_link_errors(), *generated_diagram_errors()]
    if errors:
        raise DocumentationToolError("\n".join(errors))
    print("文档检查通过：本地链接有效，UML SVG 与 PlantUML 源一致。")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("bootstrap", "render", "check"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.command == "bootstrap":
            bootstrap()
        elif args.command == "render":
            render()
        else:
            check()
    except (DocumentationToolError, subprocess.CalledProcessError) as exc:
        print(f"文档工具失败：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
