import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, test, vi } from "vitest";
import App from "./App";
import { cookieHealthGoogleOnlyPayload, lockedEdgeCookieDetail } from "./test/appFixtures";
import { installAppHarness, mockState } from "./test/appHarness";

installAppHarness();

describe("App · cookies 导入与校验", () => {
  test("integrates cookies controls into the analyzer panel", async () => {
    const user = userEvent.setup();
    render(<App />);

    const analyzer = (await screen.findByRole("heading", { name: "解析链接" })).closest("form");
    expect(analyzer).toBeInTheDocument();
    expect(within(analyzer as HTMLElement).getByText("未上传 cookies")).toBeInTheDocument();
    expect(
      within(analyzer as HTMLElement).getByText(/未配置 cookies 时 YouTube 媒体流 403 概率显著上升/)
    ).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Cookies" })).not.toBeInTheDocument();

    const file = new File(["cookie"], "cookies.txt", { type: "text/plain" });
    expect(within(analyzer as HTMLElement).getByText("选择 cookies")).toBeInTheDocument();
    expect(within(analyzer as HTMLElement).queryByText("选择 cookies.txt")).not.toBeInTheDocument();
    await user.upload(within(analyzer as HTMLElement).getByLabelText("选择 cookies"), file);

    await waitFor(() => {
      expect(fetch).toHaveBeenCalledWith(
        "/api/cookies",
        expect.objectContaining({ method: "POST", body: expect.any(FormData) })
      );
    });
    expect(await within(analyzer as HTMLElement).findByText("已启用 cookies")).toBeInTheDocument();
    expect(
      within(analyzer as HTMLElement).queryByText(/未配置 cookies 时 YouTube 媒体流 403 概率显著上升/)
    ).not.toBeInTheDocument();

    await user.click(within(analyzer as HTMLElement).getByRole("button", { name: "清除 cookies" }));

    await waitFor(() => {
      expect(fetch).toHaveBeenCalledWith("/api/cookies", expect.objectContaining({ method: "DELETE" }));
    });
  });

  test("imports browser cookies from the analyzer panel", async () => {
    const user = userEvent.setup();
    render(<App />);

    const analyzer = (await screen.findByRole("heading", { name: "解析链接" })).closest("form") as HTMLElement;
    expect(within(analyzer).queryByText("浏览器 cookies 来源")).not.toBeInTheDocument();
    expect(within(analyzer).getByRole("option", { name: "自动检测浏览器" })).toBeInTheDocument();
    await user.selectOptions(within(analyzer).getByLabelText("浏览器 cookies 来源"), "edge");
    await user.click(within(analyzer).getByRole("button", { name: "从浏览器导入" }));

    await waitFor(() => {
      expect(fetch).toHaveBeenCalledWith(
        "/api/cookies/from-browser",
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({ browser: "edge", close_browser_if_locked: false })
        })
      );
    });
    expect(await within(analyzer).findByText("已启用 cookies")).toBeInTheDocument();
  });

  test("shows locked Edge cookies prompt and imports after confirmation", async () => {
    mockState.browserCookieImportLocked = true;
    const user = userEvent.setup();
    render(<App />);

    const analyzer = (await screen.findByRole("heading", { name: "解析链接" })).closest("form") as HTMLElement;
    await user.selectOptions(within(analyzer).getByLabelText("浏览器 cookies 来源"), "edge");
    await user.click(within(analyzer).getByRole("button", { name: "从浏览器导入" }));

    expect(await within(analyzer).findByText(lockedEdgeCookieDetail.message)).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(lockedEdgeCookieDetail.message);
    await user.click(within(analyzer).getByRole("button", { name: "关闭 Edge 并导入" }));

    await waitFor(() => {
      expect(fetch).toHaveBeenCalledWith(
        "/api/cookies/from-browser",
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({ browser: "edge", close_browser_if_locked: true })
        })
      );
    });
    expect(await within(analyzer).findByText("已启用 cookies")).toBeInTheDocument();
  });

  test("retries playlist analyze after confirmed locked Edge cookies import", async () => {
    mockState.analyzeLockedByEdgeCookies = true;
    const user = userEvent.setup();
    render(<App />);

    await user.type(screen.getByLabelText("视频或 playlist 链接"), "https://youtube.com/playlist?list=abc");
    await user.click(screen.getByRole("button", { name: "解析链接" }));

    expect((await screen.findAllByText(lockedEdgeCookieDetail.message)).length).toBeGreaterThan(0);
    expect(screen.queryByText("Batch")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "关闭 Edge 并导入" }));

    expect(await screen.findByText("Batch")).toBeInTheDocument();
    const analyzeCalls = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.filter(([input]) =>
      String(input).endsWith("/api/analyze")
    );
    expect(analyzeCalls).toHaveLength(2);
  });

  test("verifies cookies and reports the login verdict with domain evidence", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.click(await screen.findByRole("button", { name: "校验 cookies（联网确认登录态）" }));

    await waitFor(() => {
      expect(mockState.cookieVerifyBodies).toEqual([{ deep: true }]);
    });

    expect(await screen.findByText(/登录态有效 —— 登录态有效：YouTube 已识别为已登录/)).toBeInTheDocument();
    expect(screen.getByText(/youtube\.com 域上：23/)).toBeInTheDocument();
    expect(screen.getByText(/SID、HSID、SAPISID、LOGIN_INFO/)).toBeInTheDocument();
    // 收窄到「联网校验」那一行：单独 /已登录/ 会同时命中结论行和这一行。
    expect(screen.getByText(/联网校验：已登录/)).toBeInTheDocument();
  });

  test("explains a google.com-only cookie jar and how to fix it", async () => {
    const user = userEvent.setup();
    mockState.cookieHealth = cookieHealthGoogleOnlyPayload;
    render(<App />);

    await user.click(await screen.findByRole("button", { name: "校验 cookies（联网确认登录态）" }));

    expect(await screen.findByText(/域名不对，等于没配/)).toBeInTheDocument();
    expect(await screen.findByText(/「只导出到 \.google\.com」的典型症状/)).toBeInTheDocument();
  });

  test("can run the cookie check offline without touching the network", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.click(await screen.findByRole("button", { name: "只做离线体检" }));

    await waitFor(() => {
      expect(mockState.cookieVerifyBodies).toEqual([{ deep: false }]);
    });
  });
});
