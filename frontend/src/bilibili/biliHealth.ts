import type { BiliStatus } from "../contracts/types";

/**
 * 「重新体检」的**执行策略**：带超时地探一次账号，超时或失败都退回离线结论。
 *
 * 为什么这一步值得单独成一个纯模块：`GET /api/bilibili/status?probe_account=true` 会**真的往
 * B站 发一次请求**。本机实测过两种「迟迟不回」：
 *   - 修复前：IPv6 是黑洞，一个请求走 **160 秒**（见 ai/bug-fix/031）；
 *   - 修复后：正常情况下 0.4~0.6 秒，但 B站 慢的时候仍可能用满后端自己那 20 秒预算。
 * 没有超时的话，点按钮之后界面一动不动 —— 用户只能认为它坏了。
 *
 * 组件里只留文案，判据（探多久、超时/失败各退到哪）都在这里，于是可以用
 * 二十毫秒的假时钟把它测透，不必在界面测试里摆弄假定时器。
 */

/**
 * 等一次「会真的打 B站 的请求」的预算。
 *
 * **必须大于后端自己那 20 秒**（`bili_http.DEFAULT_TIMEOUT`）：前端若比后端先放弃，报出来的
 * 「超时 / 读不到」就是一句假话 —— 后端明明还在合法地等，界面却说它没回。
 * 原来是 12 秒，比 20 小，于是**任何一次 12~20 秒的正常往返都被报成超时**：
 * 点「重新体检」永远只拿到离线结论（见 ai/bug-fix/031）。
 *
 * 25 秒 = 20 秒上游预算 + 建连/序列化/线程调度的余量。
 *
 * 同一个数**只写这一处**：体检（本模块）与「按需读合集列表」（`App.tsx`）等的是同一类请求，
 * 两处各写一个 12_000 正是这条缺陷的来源之一。
 */
export const BILI_CALL_TIMEOUT_MS = 25_000;

/** 超时哨兵。它必须与网络错误区分开：两者都退回离线体检，但**说法不同**。 */
const TIMEOUT = Symbol("bili-health-timeout");

export interface HealthCheckResult {
  status: BiliStatus;
  /** true = 账号探测没在时限内回来，`status` 是**离线**结论（工具/登录文件的存在性）。 */
  timedOut: boolean;
  /**
   * 账号探测**失败了**（连不上、后端 5xx、返回 502）。`status` 同样是离线结论 ——
   * 离线那一半（投稿工具、登录文件）根本不需要网络，凭什么因为探测失败就整块不给？
   * 界面据此写一句「为什么只有离线结论」。
   */
  probeError: string | null;
}

function withTimeout<T>(promise: Promise<T>, ms: number): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(() => reject(TIMEOUT), ms);
    promise.then(
      (value) => {
        clearTimeout(timer);
        resolve(value);
      },
      (error: unknown) => {
        clearTimeout(timer);
        reject(error);
      }
    );
  });
}

/**
 * 先探账号；超时**或失败**都退回离线结论。
 *
 * 三条边界：
 * 1. **超时**：探测还在跑，只是没在预算内回来 —— `timedOut=true`。
 * 2. **失败**：探测已经明确报错（代理/网络/后端 502）—— `probeError` 带原因。
 *    原先这里直接抛出，界面于是只留一条红条、**面板还是上一次的旧结论**：
 *    用户既不知道现在的工具还在不在，也不知道那条红条指哪件事。
 * 3. 连**离线**那一步也失败（后端整个挂了）—— 那才是真的读不到，抛出。
 */
export async function runHealthCheck(
  loadProbe: () => Promise<BiliStatus>,
  loadOffline: () => Promise<BiliStatus>,
  timeoutMs: number = BILI_CALL_TIMEOUT_MS
): Promise<HealthCheckResult> {
  try {
    return { status: await withTimeout(loadProbe(), timeoutMs), timedOut: false, probeError: null };
  } catch (error) {
    if (error === TIMEOUT) {
      return { status: await loadOffline(), timedOut: true, probeError: null };
    }
    return {
      status: await loadOffline(),
      timedOut: false,
      probeError: error instanceof Error ? error.message : String(error)
    };
  }
}
