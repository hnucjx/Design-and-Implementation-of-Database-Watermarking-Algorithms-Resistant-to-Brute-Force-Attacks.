import { describe, expect, test, vi } from "vitest";
import { BILI_CALL_TIMEOUT_MS, runHealthCheck } from "./biliHealth";
import { biliStatusPayload } from "../test/appFixtures";
import type { BiliStatus } from "../contracts/types";

/**
 * 「重新体检」的执行策略。
 *
 * 这里测的是**判据**：探多久、超时退到哪、**失败**退到哪、什么时候才真的抛。
 * 界面只负责显示文案，所以可以用二十毫秒的时限把几条分支各测一遍 —— 不必在界面测试里
 * 摆弄假定时器（那是上一版写法，它把别的用例一起拖进了超时）。
 */

// 只用来区分「哪一份结论回来了」，所以一个不同身份的对象就够。
const OFFLINE: BiliStatus = { ...biliStatusPayload };

describe("体检策略", () => {
  test("账号探测按时回来就用它的结论，不额外做离线体检", async () => {
    const loadOffline = vi.fn(async () => OFFLINE);
    const result = await runHealthCheck(async () => biliStatusPayload, loadOffline, 50);

    expect(result.timedOut).toBe(false);
    expect(result.probeError).toBeNull();
    expect(result.status).toBe(biliStatusPayload);
    expect(loadOffline).not.toHaveBeenCalled();
  });

  test("探测迟迟不回 → 退回离线结论，并标记 timedOut", async () => {
    const loadProbe = () => new Promise<BiliStatus>(() => {});
    const loadOffline = vi.fn(async () => OFFLINE);
    const result = await runHealthCheck(loadProbe, loadOffline, 20);

    expect(result.timedOut).toBe(true);
    expect(result.probeError).toBeNull();
    expect(result.status).toBe(OFFLINE);
    expect(loadOffline).toHaveBeenCalledTimes(1);
  });

  test("探测**失败**（不是慢）也退回离线结论，并把原因带出来", async () => {
    // 原先这里直接抛：界面于是只留一条页顶红条、**面板还是上一次的旧结论** ——
    // 用户既不知道投稿工具现在还在不在，也不知道那条红条指哪件事。
    // 离线那一半（工具/登录文件）本来就不需要网络，没理由因为探测失败就整块不给。
    const loadOffline = vi.fn(async () => OFFLINE);
    const result = await runHealthCheck(
      async () => Promise.reject(new Error("upstream connect failed")),
      loadOffline,
      50
    );

    expect(result.timedOut).toBe(false);
    expect(result.probeError).toContain("upstream connect failed");
    expect(result.status).toBe(OFFLINE);
    expect(loadOffline).toHaveBeenCalledTimes(1);
  });

  test("连离线那一半也失败时才抛 —— 那才是真的读不到", async () => {
    const boom = new Error("offline also failed");
    await expect(
      runHealthCheck(async () => Promise.reject(new Error("probe down")), async () => Promise.reject(boom), 50)
    ).rejects.toBe(boom);
  });

  test("超时那一半也不成功后，错误同样抛出去（不能吞成一句「体检完成」）", async () => {
    const boom = new Error("offline also failed");
    await expect(
      runHealthCheck(() => new Promise<BiliStatus>(() => {}), async () => Promise.reject(boom), 10)
    ).rejects.toBe(boom);
  });

  test("兜底时限必须**大于后端自己那 20 秒**，否则「超时」是假话", () => {
    // 原来是 12 秒 —— 比后端 `bili_http.DEFAULT_TIMEOUT`（20 s）短，于是任何一次 12~20 秒的
    // 正常往返都被报成超时，用户每次「重新体检」都只拿到离线结论。见 ai/bug-fix/031。
    expect(BILI_CALL_TIMEOUT_MS).toBeGreaterThan(20_000);
    expect(BILI_CALL_TIMEOUT_MS).toBe(25_000);
  });
});
