import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, test, vi } from "vitest";
import App from "../../App";
import { installAppHarness, mockState } from "../../test/appHarness";

installAppHarness();

/** 任务行上的「暂停」按钮（`aria-label` 是「暂停 <任务标题>」）。 */
function pauseButton() {
  return screen.findByRole("button", { name: "暂停 Running video" });
}

/**
 * 页面级错误回执条的行为。
 *
 * 这一组只测**行为**（出现 / 关闭 / 替换 / 收回 / 不移动页面）。它到底吸不吸顶、
 * 在 scrollY=1200 时还看不看得见，jsdom 量不出来（不做布局、不算 sticky）——
 * 那部分由 `styles.test.ts` 的源文不变式与 `tmp_acceptance/ui_error_banner.py` 的
 * 真实浏览器探针负责（见 ai/ui/009）。
 */
describe("App · 页面级错误回执条", () => {
  test("shows a task-center failure in the receipt bar instead of a block at the top of the document", async () => {
    const user = userEvent.setup();
    mockState.jobActionFailure = "任务已经不在队列里了";
    render(<App />);

    await user.click(await pauseButton());

    const banner = await screen.findByRole("alert");
    expect(banner).toHaveClass("error-banner");
    expect(banner).toHaveTextContent("任务已经不在队列里了");
    // 旧的页顶块用的是 `.alert`（上传弹窗里仍在使用），页面级回执不许退回去。
    expect(banner).not.toHaveClass("alert");
  });

  test("keeps the page still when the receipt appears", async () => {
    const user = userEvent.setup();
    const scrollTo = vi.spyOn(window, "scrollTo").mockImplementation(() => undefined);
    mockState.jobActionFailure = "任务已经不在队列里了";
    render(<App />);

    await user.click(await pauseButton());
    await screen.findByRole("alert");

    // 出现错误时**不许替用户滚到某处**：那会把他正在读的那一段抢走。
    expect(scrollTo).not.toHaveBeenCalled();
    expect(document.documentElement.scrollTop).toBe(0);
  });

  test("stays until the user closes it", async () => {
    const user = userEvent.setup();
    mockState.jobActionFailure = "任务已经不在队列里了";
    render(<App />);

    await user.click(await pauseButton());
    await screen.findByRole("alert");

    // 不自动缩回：错误里常带着要照着做的信息，消失了就读不到了。
    await new Promise((resolve) => window.setTimeout(resolve, 400));
    expect(screen.getByRole("alert")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "关闭错误提示" }));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  test("closes on Escape once the focus is inside the bar", async () => {
    const user = userEvent.setup();
    mockState.jobActionFailure = "任务已经不在队列里了";
    render(<App />);

    await user.click(await pauseButton());
    const close = await screen.findByRole("button", { name: "关闭错误提示" });
    close.focus();
    await user.keyboard("{Escape}");

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  test("replaces the previous receipt instead of stacking two of them", async () => {
    const user = userEvent.setup();
    render(<App />);

    mockState.jobActionFailure = "第一次失败";
    await user.click(await pauseButton());
    expect(await screen.findByRole("alert")).toHaveTextContent("第一次失败");

    mockState.jobActionFailure = "第二次失败";
    await user.click(await pauseButton());

    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("第二次失败"));
    expect(screen.getAllByRole("alert")).toHaveLength(1);
  });

  test("takes the receipt back when the next action starts", async () => {
    const user = userEvent.setup();
    mockState.jobActionFailure = "任务已经不在队列里了";
    render(<App />);

    await user.click(await pauseButton());
    expect(await screen.findByRole("alert")).toBeInTheDocument();

    // 下一条动作一发起，上一条红条就该消失 —— 留着它，用户会拿一条过期的失败去解释
    // 刚刚做成的另一件事（`api.ts` 的 timeoutError 注释里记着同一类误读）。
    mockState.jobActionFailure = null;
    await user.click(screen.getByRole("button", { name: "重启 Paused video" }));

    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
  });

  test("uses the same bar for a cookies failure raised from the analyzer", async () => {
    const user = userEvent.setup();
    mockState.browserCookieImportLocked = true;
    render(<App />);

    const analyzer = (await screen.findByRole("heading", { name: "解析链接" })).closest("form") as HTMLElement;
    await user.selectOptions(within(analyzer).getByLabelText("浏览器 cookies 来源"), "edge");
    await user.click(within(analyzer).getByRole("button", { name: "从浏览器导入" }));

    // 解析面板里那条就地提示是 `role="status"`，所以这里能找到的 `alert` 只有回执条本身。
    const banner = await screen.findByRole("alert");
    expect(banner).toHaveClass("error-banner");
    expect(banner).toHaveTextContent("Edge 正在运行");
  });
});
