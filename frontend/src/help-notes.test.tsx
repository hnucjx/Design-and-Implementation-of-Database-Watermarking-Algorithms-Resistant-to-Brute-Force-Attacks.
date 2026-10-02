import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, test } from "vitest";
import App from "./App";
import { ensureExpanded, installAppHarness } from "./test/appHarness";

installAppHarness();

describe("App · 帮助浮层", () => {
  test("documents the three ways to obtain cookies and warns about the unsafe one", async () => {
    const user = userEvent.setup();
    render(<App />);

    const head = await screen.findByRole("button", { name: "说明：Cookie 获取方式" });
    await ensureExpanded(head);

    expect(screen.getByText("python scripts/export_cookies_via_cdp.py")).toBeInTheDocument();
    expect(screen.getByText(/导出脚本（推荐）/)).toBeInTheDocument();
    expect(screen.getByText(/Get cookies\.txt LOCALLY/)).toBeInTheDocument();
    expect(screen.getByText(/不支持的方式：/)).toBeInTheDocument();
    expect(screen.getByText(/破坏浏览器的 cookie 库/)).toBeInTheDocument();
  });

  test("keeps every help note collapsed until asked for, and closes it on demand", async () => {
    const user = userEvent.setup();
    render(<App />);

    const helpIds = [
      "说明：Cookie 获取方式",
      "说明：Cookie 校验结论解读",
      "说明：常见代理软件的本地端口",
      "说明：浏览器可访问而应用不可访问时的排查顺序",
      // 设置里的这一项：文案改成「单视频并发下载数」之后，aria2c 的两个生效条件
      // （默认关闭 + 只在默认方式失败后才作为后备）都搬进了说明浮层，所以它也必须默认收起。
      "说明：单视频并发下载的作用与启用方式"
    ];
    for (const name of helpIds) {
      expect(await screen.findByRole("button", { name })).toHaveAttribute("aria-expanded", "false");
    }
    // 收起状态下，说明正文不在 DOM 里。
    expect(screen.queryByText("Cookie 获取方式")).not.toBeInTheDocument();

    const head = screen.getByRole("button", { name: "说明：Cookie 获取方式" });
    await user.click(head);
    expect(head).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("dialog", { name: "Cookie 获取方式" })).toBeInTheDocument();

    await user.keyboard("{Escape}");
    expect(head).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("dialog", { name: "Cookie 获取方式" })).not.toBeInTheDocument();
  });

  test("remembers an expanded help note across reloads", async () => {
    const user = userEvent.setup();
    render(<App />);

    const head = await screen.findByRole("button", { name: "说明：Cookie 获取方式" });
    await user.click(head);
    expect(head).toHaveAttribute("aria-expanded", "true");

    cleanup();
    render(<App />);

    expect(await screen.findByRole("button", { name: "说明：Cookie 获取方式" })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("dialog", { name: "Cookie 获取方式" })).toBeInTheDocument();
  });
});
