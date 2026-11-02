/**
 * `request()` 的超时兜底（ai/bug-fix/025）。
 *
 * 这里只测「挂住」这一件事：它不产生错误、不产生日志，只会让界面停在 loading 上，
 * 所以**只能靠计时器抓**。用假定时器把 60 s / 180 s 走完，比等真实时间可靠得多。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ANALYZE_TIMEOUT_MS,
  ApiError,
  REQUEST_TIMEOUT_MS,
  analyzeUrl,
  createBiliQrcode,
  listJobs,
  updateSettings
} from "./api";

/** 一个「永远不回话」的 fetch：只有信号被 abort 时才拒绝（与真实 fetch 的行为一致）。 */
function hangingFetch() {
  const signals: AbortSignal[] = [];
  const fn = vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
    const signal = init?.signal ?? undefined;
    if (signal) signals.push(signal);
    return new Promise<Response>((_resolve, reject) => {
      signal?.addEventListener("abort", () => reject(new DOMException("The operation was aborted.", "AbortError")));
    });
  });
  return { fn, signals };
}

/** 记录 Promise 是否已经落定（不关心成功还是失败）。 */
function track(promise: Promise<unknown>) {
  const state = { settled: false, error: undefined as unknown };
  void promise.catch((error) => {
    state.settled = true;
    state.error = error;
  });
  return state;
}

describe("request 的超时兜底", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("后端一直不回话时，到点抛出一句能照做的超时错误", async () => {
    const { fn, signals } = hangingFetch();
    vi.stubGlobal("fetch", fn);

    const state = track(listJobs());

    await vi.advanceTimersByTimeAsync(REQUEST_TIMEOUT_MS - 1);
    expect(state.settled).toBe(false);
    // 说明「还没到点」时会话仍然开着 —— 这正是用户看到的「正在读取…」。
    expect(signals[0]?.aborted).toBe(false);

    await vi.advanceTimersByTimeAsync(1);

    expect(state.settled).toBe(true);
    expect(state.error).toBeInstanceOf(ApiError);
    const error = state.error as ApiError;
    expect(error.status).toBe(0);
    expect(error.message).toContain("请求超时");
    expect(error.message).toContain("60 秒");
    expect(error.message).toContain("/api/jobs");
    // 必须真的把连接掐掉，否则后端那条请求会在后台继续跑。
    expect(signals[0]?.aborted).toBe(true);
  });

  it("超时消息要点名**是哪个动作**，而不只是给一个 URL", async () => {
    // 错误横幅是页面级的：上一条别的动作遗留的红条会一直挂着，用户会把它读成
    // 「我刚点的这个失败了」—— 实测有人把「重试投稿超时」读成「扫码登录失败」（ai/bug-fix/031）。
    const { fn } = hangingFetch();
    vi.stubGlobal("fetch", fn);

    const state = track(createBiliQrcode());

    await vi.advanceTimersByTimeAsync(REQUEST_TIMEOUT_MS);
    expect(state.settled).toBe(true);
    const message = (state.error as ApiError).message;
    expect(message).toContain("「扫码登录」");
    // URL 仍然要在：它才是排障时唯一能贴给别人的东西。
    expect(message).toContain("/api/bilibili/login/qrcode");
  });

  it("没有动作名的请求保持原样（不凭空造一个「」）", async () => {
    const { fn } = hangingFetch();
    vi.stubGlobal("fetch", fn);

    const state = track(listJobs());
    await vi.advanceTimersByTimeAsync(REQUEST_TIMEOUT_MS);

    // 判据是「没有前缀」，不能查「」——消息正文里本来就有「连「失败」都没回来」。
    expect((state.error as ApiError).message.startsWith("请求超时")).toBe(true);
  });

  it("解析链接的预算更长，但同样有上限", async () => {
    const { fn } = hangingFetch();
    vi.stubGlobal("fetch", fn);

    const state = track(analyzeUrl("https://youtu.be/x"));

    // 60 s 时它**不该**被判超时：解析要真的连 YouTube，慢是正常的。
    await vi.advanceTimersByTimeAsync(REQUEST_TIMEOUT_MS);
    expect(state.settled).toBe(false);

    await vi.advanceTimersByTimeAsync(ANALYZE_TIMEOUT_MS - REQUEST_TIMEOUT_MS);
    expect(state.settled).toBe(true);
    expect((state.error as ApiError).message).toContain("180 秒");
  });

  it("正常返回不受影响，定时器也被清掉", async () => {
    const fetchMock = vi.fn(async () => Response.json([{ id: "job-1" }]));
    vi.stubGlobal("fetch", fetchMock);

    await expect(listJobs()).resolves.toEqual([{ id: "job-1" }]);
    // 定时器没清掉的话，测试进程会在 60 s 后凭空 abort 一次。
    expect(vi.getTimerCount()).toBe(0);
  });

  it("HTTP 错误仍按原来的口径报（不被当成超时）", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({ detail: "保存失败" }, { status: 500 })));

    await expect(updateSettings({ default_resolution: "1080p" })).rejects.toMatchObject({
      name: "ApiError",
      status: 500,
      message: "保存失败"
    });
    expect(vi.getTimerCount()).toBe(0);
  });
});
