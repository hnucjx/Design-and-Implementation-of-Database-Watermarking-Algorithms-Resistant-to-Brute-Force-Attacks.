import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, test, vi } from "vitest";
import App from "../App";
// 必须 import：否则 `getComputedStyle` 一律返回空串，选中态的配色断言会静默地
// 变成「断言一个空值不等于某个颜色」—— 两种情况都能过，测试却是空的。
import "../styles.css";
import {
  biliJobUploadsPayload,
  biliPrefillCollectionPayload,
  biliPrefillSinglePayload,
  biliStatusLoggedInPayload,
  biliStatusPayload,
  biliUploadFailedPayload,
  biliUploadPayload,
  jobPayload,
  playlistJobPayload
} from "../test/appFixtures";
import { installAppHarness, mockState } from "../test/appHarness";

installAppHarness();

/**
 * B站 上传的界面测试。
 *
 * 出厂夹具里的任务**全都没有 `output_path`**（它们在测下载，没下载完），所以上传按钮默认是
 * 禁用的 —— 这本身就是一条要测的行为（「没下载好就不给传」）。要测「能传」的路径，用
 * `downloadedJob()` 现造一个。
 *
 * 三类断言分得比较清：
 *
 * 1. **服务端说了什么，界面就显示什么**：徽标文案、硬限数字、状态措辞全部来自 payload。
 *    界面自己造一套文案，两处必然漂移 —— 所以测试里出现的中文都直接来自夹具。
 * 2. **前端送上去的就是用户填的**：断言请求体，而不是断言「点了按钮」。
 * 3. **重试动作要区分措辞**：`failed` 是「重试」，`unverified` 是「重新核对」——
 *    把「待核对」说成失败，用户会去重投一条**已经投上去**的稿件。
 */
const DOWNLOADED_PATH = "D:\\videos\\running.mp4";

/** 页顶那条上传提示（`App` 里的 `.notice`）。`role="status"` 在别处也用，只能按类名取。 */
function notice(): HTMLElement | null {
  return document.querySelector(".notice");
}

/**
 * 「已填入上一次投稿…」那句提示。
 *
 * **不能 `getByText(/已填入/)`**：那句话里的「上一次投稿」包在 `<strong>` 里，
 * 文本被拆成多个节点，正则整段匹配不到（RTL 会明确告诉你 text is broken up）。
 * 所以按 `<p class="hint">` 整段取，再判 `textContent`。
 */
function reuseHint(dialog: HTMLElement): HTMLElement | null {
  const found = [...dialog.querySelectorAll("p.hint")].find((p) => p.textContent?.includes("已填入上一次投稿"));
  return (found as HTMLElement | undefined) ?? null;
}

/** 一个「视频已经下载好」的单视频任务。 */
function downloadedJob(overrides: Record<string, unknown> = {}) {
  return {
    ...jobPayload,
    status: "succeeded",
    completed_items: 1,
    items: [
      {
        ...jobPayload.items[0],
        status: "succeeded",
        progress: 100,
        output_path: DOWNLOADED_PATH
      }
    ],
    ...overrides
  };
}

/** 一个两个条目都已下载好的 playlist 任务。 */
function downloadedPlaylistJob() {
  return {
    ...playlistJobPayload,
    status: "running",
    items: playlistJobPayload.items.map((item) => ({
      ...item,
      output_path: `D:\\videos\\${item.id}.mp4`
    }))
  };
}

/** 任务级 B站 记录。`uploads` 与 `season` 按需覆盖。 */
function jobUploads(uploads = [biliUploadPayload], overrides: Record<string, unknown> = {}) {
  return {
    ...biliJobUploadsPayload,
    job_id: "job-running",
    uploads: uploads.map((upload) => ({ ...upload, job_id: "job-running" })),
    ...overrides
  } as typeof biliJobUploadsPayload;
}

/** 打开单视频上传表单。 */
async function openSingleDialog(prefill: typeof mockState.biliPrefill = biliPrefillSinglePayload) {
  const user = userEvent.setup();
  mockState.jobs = [downloadedJob()];
  // 与 `openCollectionDialog` 同一条规矩：**显式赋值**。`biliPrefill` 每用例被 harness
  // 重置成单视频预填，只在它为 null 时才赋值会让「改过预填的用例」悄悄用回默认值。
  mockState.biliPrefill = prefill;
  render(<App />);
  const button = await screen.findByRole("button", { name: /上传到 B 站 Running video/ });
  await user.click(button);
  return { user, dialog: await screen.findByRole("dialog") };
}

describe("App · B站 上传入口", () => {
  test("keeps the button disabled until the video is downloaded", async () => {
    render(<App />);

    const button = await screen.findByRole("button", { name: /上传到 B 站 Running video/ });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("title", expect.stringContaining("视频还没下载好"));
    // 按钮上带文字而不是纯图标：需求原话是「B站图标 + 上传语义」，触屏上 tooltip 看不到。
    expect(within(button).getByText("上传")).toBeInTheDocument();
  });

  test("enables the button and prefills from the clicked item", async () => {
    const user = userEvent.setup();
    mockState.jobs = [downloadedJob()];
    render(<App />);

    const button = await screen.findByRole("button", { name: /上传到 B 站 Running video/ });
    expect(button).toBeEnabled();
    await user.click(button);

    await waitFor(() => {
      expect(mockState.biliPrefillQueries).toEqual([{ jobId: "job-running", itemId: "item-running" }]);
    });
  });

  test("prefills a whole playlist without an item id", async () => {
    const user = userEvent.setup();
    mockState.jobs = [downloadedPlaylistJob()];
    render(<App />);

    const button = await screen.findByRole("button", { name: /上传到 B 站 Playlist batch/ });
    expect(button).toBeEnabled();
    await user.click(button);

    // 合集走整批：`item_id` 必须是 null，否则服务端会按单视频预填。
    await waitFor(() => {
      expect(mockState.biliPrefillQueries).toEqual([{ jobId: "job-playlist", itemId: null }]);
    });
  });

  test("keeps the row button disabled while one playlist item is still missing", async () => {
    mockState.jobs = [
      {
        ...playlistJobPayload,
        items: [
          { ...playlistJobPayload.items[0], output_path: DOWNLOADED_PATH },
          { ...playlistJobPayload.items[1], output_path: null }
        ]
      }
    ];
    render(<App />);

    const button = await screen.findByRole("button", { name: /上传到 B 站 Playlist batch/ });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("title", expect.stringContaining("还有视频没下载好"));
  });

  test("paints the badge the backend sent, without inventing state of its own", async () => {
    mockState.jobs = [downloadedJob()];
    mockState.biliUploads = [jobUploads()];
    render(<App />);

    const button = await screen.findByRole("button", { name: /B 站已上传；点击进入 B 站 Running video/ });
    expect(within(button).getByText("已上传")).toBeInTheDocument();
    expect(within(button).queryByText("未上传")).not.toBeInTheDocument();
  });

  test("makes 已上传 an entry point to B站 instead of a second upload", async () => {
    const user = userEvent.setup();
    const open = vi.spyOn(window, "open").mockImplementation(() => null);
    mockState.jobs = [downloadedJob()];
    mockState.biliUploads = [jobUploads()];
    render(<App />);

    const button = await screen.findByRole("button", { name: /B 站已上传；点击进入 B 站 Running video/ });
    // tooltip 与无障碍名是**同一句**：视觉与读屏不能说两件事。
    expect(button).toHaveAttribute("title", "B 站已上传；点击进入 B 站");
    expect(button).toBeEnabled();

    await user.click(button);

    expect(open).toHaveBeenCalledWith(
      "https://www.bilibili.com/video/BV1xx411c7mD",
      "_blank",
      "noopener,noreferrer"
    );
    // 关键：**没有**开上传表单。开着表单就等于在暗示「再投一遍」，
    // 而用户点这一下只是想看看它在 B站 上长什么样。
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(mockState.biliPrefillQueries).toEqual([]);
    expect(mockState.biliUploadBodies).toEqual([]);
  });

  test("still opens B站 when the local video file is gone", async () => {
    const user = userEvent.setup();
    const open = vi.spyOn(window, "open").mockImplementation(() => null);
    // 传完就把本地文件删掉是常态。稿件已经在平台上了，这个按钮不该因此连链接都点不开 ——
    // 「视频还没下载好」这条禁用理由是给**上传**用的，与「打开已上传的页面」无关。
    mockState.jobs = [downloadedJob({ items: [{ ...downloadedJob().items[0], output_path: null }] })];
    mockState.biliUploads = [jobUploads()];
    render(<App />);

    const button = await screen.findByRole("button", { name: /B 站已上传；点击进入 B 站 Running video/ });
    expect(button).toBeEnabled();
    await user.click(button);
    expect(open).toHaveBeenCalledWith(
      "https://www.bilibili.com/video/BV1xx411c7mD",
      "_blank",
      "noopener,noreferrer"
    );
  });

  test("falls back to the upload path when the record has no link at all", async () => {
    const user = userEvent.setup();
    mockState.jobs = [downloadedJob()];
    // 徽标可以由「合集已归集」派生而来，那条路径下可能一条带 bvid 的稿件都没有。
    // 这时「点击重新上传」是**事实**，不是误导 —— 保持老路比一个点下去没反应的绿按钮好。
    mockState.biliUploads = [
      jobUploads([{ ...biliUploadPayload, bvid: null, video_url: null }], {
        badge: "uploaded",
        badge_text: "已上传",
        badge_tone: "ok"
      })
    ];
    render(<App />);

    const button = await screen.findByRole("button", { name: /B站：已上传 Running video/ });
    expect(button).toHaveAttribute("title", "B站：已上传 · 点击重新上传");
    await user.click(button);
    await waitFor(() =>
      expect(mockState.biliPrefillQueries).toEqual([{ jobId: "job-running", itemId: "item-running" }])
    );
  });

  test("an uploaded collection item opens its own page, not the first one", async () => {
    const user = userEvent.setup();
    const open = vi.spyOn(window, "open").mockImplementation(() => null);
    mockState.jobs = [downloadedPlaylistJob()];
    mockState.biliUploads = [
      jobUploads(
        [
          {
            ...biliUploadPayload,
            id: "bili-p1",
            item_id: "item-playlist-1",
            title: "Part one",
            bvid: "BV1one",
            video_url: "https://www.bilibili.com/video/BV1one"
          },
          {
            ...biliUploadPayload,
            id: "bili-p2",
            item_id: "item-playlist-2",
            title: "Part two",
            bvid: "BV1two",
            video_url: "https://www.bilibili.com/video/BV1two"
          }
        ],
        { job_id: "job-playlist" }
      )
    ];
    render(<App />);

    // 点第 2 集却打开第 1 集，比不给链接更糟：用户会以为那就是第 2 集。
    await user.click(await screen.findByRole("button", { name: /B 站已上传；点击进入 B 站 Part two/ }));
    expect(open).toHaveBeenCalledTimes(1);
    expect(open).toHaveBeenCalledWith("https://www.bilibili.com/video/BV1two", "_blank", "noopener,noreferrer");
  });

  test("stays disabled while an upload is in flight, and promises nothing", async () => {
    const user = userEvent.setup();
    mockState.jobs = [downloadedJob()];
    mockState.biliUploads = [jobUploads([], { badge: "uploading", badge_text: "上传中", badge_tone: "busy" })];
    render(<App />);

    const button = await screen.findByRole("button", { name: /B站：上传中 Running video/ });
    expect(button).toBeDisabled();
    // 这一档以前提示「点击重新上传」—— 一个灰着的按钮还许诺点击，是自相矛盾的。
    expect(button).toHaveAttribute("title", "正在上传中；跑完会自动变成「已上传」或「失败」");
    expect(button.getAttribute("title")).not.toContain("点击");

    await user.click(button);
    expect(mockState.biliPrefillQueries).toEqual([]);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  test("待核对 只重新核对，绝不重新投稿", async () => {
    const user = userEvent.setup();
    mockState.jobs = [downloadedJob()];
    mockState.biliRetryAction = "reverify";
    // 「待核对」不是失败：投稿命令已经成功过一次，平台上很可能已经有这条稿件 ——
    // 重投就是这个视频的第二遍，而平台侧撤不回来。
    mockState.biliUploads = [
      jobUploads(
        [
          {
            ...biliUploadPayload,
            status: "unverified",
            status_text: "待核对",
            badge: "unverified",
            badge_text: "待核对",
            badge_tone: "warn",
            error: "已提交，但还没在 B站 看到它",
            advice: "B站 的稿件列表有延迟，稍后再查一次"
          }
        ],
        { badge: "unverified", badge_text: "待核对", badge_tone: "warn" }
      )
    ];
    render(<App />);

    const button = await screen.findByRole("button", {
      name: /B 站还没确认这条稿件；点击重新核对（不会重投） Running video/
    });
    expect(button).toBeEnabled();

    await user.click(button);

    // 走的是「重新核对」那条接口（服务端对 `unverified` 只回读平台列表，不发投稿）。
    await waitFor(() => expect(mockState.biliRetryUploadIds).toEqual(["bili-1"]));
    expect(mockState.biliPrefillQueries).toEqual([]);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(mockState.biliUploadBodies).toEqual([]);
    await waitFor(() => expect(notice()).toHaveTextContent("已重新核对。"));
  });

  test("部分失败 只补没成功的那几条，不碰已经上传的", async () => {
    const user = userEvent.setup();
    mockState.jobs = [downloadedPlaylistJob()];
    mockState.biliRetryAction = "reupload";
    mockState.biliUploads = [
      jobUploads(
        [
          { ...biliUploadPayload, id: "bili-ok", item_id: "item-playlist-1", title: "Part one" },
          { ...biliUploadFailedPayload, id: "bili-bad", item_id: "item-playlist-2", title: "Part two" }
        ],
        { job_id: "job-playlist", badge: "partial", badge_text: "部分失败", badge_tone: "warn" }
      )
    ];
    render(<App />);

    const button = await screen.findByRole("button", { name: /重试没成功的 1 条 Playlist batch/ });
    expect(button).toHaveAttribute("title", "有 1 条还没投上去；点击只重试这几条，不会重投已上传的");

    await user.click(button);

    // **只碰失败的那条**：已发布的 `bili-ok` 一次都不能被提交。
    await waitFor(() => expect(mockState.biliRetryUploadIds).toEqual(["bili-bad"]));
    expect(mockState.biliPrefillQueries).toEqual([]);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(mockState.biliUploadBodies).toEqual([]);
    await waitFor(() => expect(notice()).toHaveTextContent("已重新排队。"));
  });

  test("失败 仍然走投稿表单（没有记录在平台上，元数据可能还要改）", async () => {
    const user = userEvent.setup();
    mockState.jobs = [downloadedJob()];
    mockState.biliUploads = [
      jobUploads([biliUploadFailedPayload], { badge: "failed", badge_text: "上传失败", badge_tone: "bad" })
    ];
    render(<App />);

    const button = await screen.findByRole("button", { name: /B站：上传失败 Running video/ });
    expect(button).toHaveAttribute("title", "B站：上传失败 · 点击重新上传");
    await user.click(button);
    await waitFor(() =>
      expect(mockState.biliPrefillQueries).toEqual([{ jobId: "job-running", itemId: "item-running" }])
    );
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
  });
});

describe("App · B站 上传进度", () => {
  /** 距今 N 秒的 ISO 时刻：计时是从**服务端**那个起点算的，不是从点击那一刻。 */
  function secondsAgo(seconds: number): string {
    return new Date(Date.now() - seconds * 1000).toISOString();
  }

  /** 某一步的状态：`is-done` / `is-active` / 空。按标签取，不按下标 —— 下标会随节点顺序变。 */
  function stepState(label: string): string {
    const steps = [...document.querySelectorAll(".bili-step")];
    const step = steps.find((element) => element.textContent?.includes(label));
    expect(step, `找不到「${label}」这一节`).toBeDefined();
    return step!.className;
  }

  test("上传中：卡片里说清在第几步、在等什么、已经等了多久", async () => {
    mockState.jobs = [downloadedJob()];
    mockState.biliUploads = [
      jobUploads(
        [{ ...biliUploadPayload, status: "uploading", status_text: "上传中", progress: 0.2, bvid: null, video_url: null, started_at: secondsAgo(65) }],
        { badge: "uploading", badge_text: "上传中", badge_tone: "busy" }
      )
    ];
    render(<App />);

    expect(await screen.findByText("上传进度")).toBeInTheDocument();
    expect(screen.getByText("正在传输视频文件")).toBeInTheDocument();
    // 「已用」是本地按秒推进的：biliup 在传输这一段可能几分钟不发一条输出，
    // 只靠 SSE 刷新的话界面会静止，用户分不清是在跑还是卡死。
    expect(screen.getByText(/已用 01:0[4-6]/)).toBeInTheDocument();
    // 排队已走完、传输进行中 —— 两者不能都亮，也不能都灰。
    expect(stepState("排队")).toContain("is-done");
    expect(stepState("传输")).toContain("is-active");
    expect(stepState("提交")).not.toContain("is-active");
  });

  test("单视频不显示「第几条」；合集显示序号与已确认条数", async () => {
    mockState.jobs = [downloadedPlaylistJob()];
    mockState.biliUploads = [
      jobUploads(
        [
          { ...biliUploadPayload, id: "bili-p1", item_id: "item-playlist-1", title: "Part one", status: "published", progress: 1 },
          {
            ...biliUploadPayload,
            id: "bili-p2",
            item_id: "item-playlist-2",
            title: "Part two",
            status: "uploading",
            status_text: "上传中",
            progress: 0.2,
            bvid: null,
            video_url: null,
            started_at: secondsAgo(10)
          }
        ],
        { job_id: "job-playlist", badge: "uploading", badge_text: "上传中", badge_tone: "busy" }
      )
    ];
    render(<App />);

    expect(await screen.findByText(/第 2\/2 条 · Part two/)).toBeInTheDocument();
    expect(screen.getByText(/已有 1\/2 条确认在平台上/)).toBeInTheDocument();
  });

  test("审核中：四个节点走完，明说没有百分比可读", async () => {
    mockState.jobs = [downloadedJob()];
    mockState.biliUploads = [
      jobUploads(
        [
          {
            ...biliUploadPayload,
            status: "reviewing",
            status_text: "审核中",
            progress: 0.95,
            started_at: secondsAgo(900),
            finished_at: secondsAgo(720)
          }
        ],
        { badge: "uploading", badge_text: "上传中", badge_tone: "busy" }
      )
    ];
    render(<App />);

    expect(await screen.findByText("已提交，B 站 审核中")).toBeInTheDocument();
    // 计时给的是**上传用时**（900 秒 → 720 秒 = 3 分钟）：审核等的是平台，不该记进我们的传输。
    expect(screen.getByText("上传用时 03:00")).toBeInTheDocument();
    // 审核不是第五步：四个节点全都算走完，不能留一个在闪。
    for (const label of ["排队", "传输", "提交", "核对"]) {
      expect(stepState(label)).toContain("is-done");
    }
    expect(document.querySelector(".bili-step.is-active")).toBeNull();
  });

  test("审核中：秒表停在「上传用时」上，不再替平台审核计时", async () => {
    mockState.jobs = [downloadedJob()];
    mockState.biliUploads = [
      jobUploads(
        [
          {
            ...biliUploadPayload,
            status: "reviewing",
            status_text: "审核中",
            progress: 0.95,
            // 起点 = 终点 ⇒ `上传用时 00:00`。若时钟还在推进，两秒后这里必然变成别的数字。
            started_at: secondsAgo(0),
            finished_at: secondsAgo(0)
          }
        ],
        { badge: "uploading", badge_text: "上传中", badge_tone: "busy" }
      )
    ];
    render(<App />);

    expect(await screen.findByText("上传用时 00:00")).toBeInTheDocument();
    // 实测反例（ai/ui/008）：Ep 125 那次上传只用了 52 秒，界面一路涨到「已提交 6:41:18」，
    // 因为它量的是**等审核**的时间，而这块东西标题叫「上传进度」。
    await new Promise((resolve) => window.setTimeout(resolve, 2200));
    expect(screen.getByText("上传用时 00:00")).toBeInTheDocument();
    expect(screen.queryByText(/已提交 \d/)).toBeNull();
  });

  test("已发布 / 失败 / 待核对都不显示进度块（已经有结论了）", async () => {
    const cases = [
      ["uploaded", "已上传", "ok", [biliUploadPayload], /B 站已上传；点击进入 B 站 Running video/],
      ["failed", "上传失败", "bad", [biliUploadFailedPayload], /B站：上传失败 Running video/],
      [
        "unverified",
        "待核对",
        "warn",
        [{ ...biliUploadPayload, status: "unverified" as const, progress: 0.95 }],
        /B 站还没确认这条稿件/
      ]
    ] as const;
    for (const [badge, text, tone, uploads, name] of cases) {
      mockState.jobs = [downloadedJob()];
      mockState.biliUploads = [jobUploads([...uploads], { badge, badge_text: text, badge_tone: tone })];
      const { unmount } = render(<App />);
      // 以**按钮的徽标文案**为「数据已经到了」的锚点：比「等一拍」可靠。
      await screen.findByRole("button", { name });
      expect(screen.queryByText("上传进度")).toBeNull();
      unmount();
    }
  });
});

describe("App · B站 上传表单（单视频）", () => {
  test("prefills every field from the server and takes the limits from it too", async () => {
    const { dialog } = await openSingleDialog();

    expect(within(dialog).getByLabelText("标题")).toHaveValue("Unsupported resolution");
    expect(within(dialog).getByLabelText("简介")).toHaveValue("简介");
    expect(within(dialog).getByLabelText("标签（用英文逗号分隔）")).toHaveValue("编程,数据库");
    expect(within(dialog).getByLabelText("分区")).toHaveValue("122");
    expect(within(dialog).getByLabelText(/上传后置顶一条评论/)).toHaveValue(
      "本视频由个人工作流同步上传，欢迎在评论区交流。"
    );
    // 字数上限来自 `prefill.limits`：前端写死一份常量迟早会出现「界面说没超、提交回来 400」。
    expect(within(dialog).getByText("22/80")).toBeInTheDocument();
    // 标签那一行同时报「几个」与「多长」，两个上限都来自服务端。
    expect(within(dialog).getByText("2/10 个 · 6/200 字")).toBeInTheDocument();
  });

  test("focuses the title so the user can start typing right away", async () => {
    const { dialog } = await openSingleDialog();
    expect(within(dialog).getByLabelText("标题")).toHaveFocus();
  });

  test("says out loud when the values come from the previous submission", async () => {
    const { dialog } = await openSingleDialog({
      ...biliPrefillSinglePayload,
      metadata: { ...biliPrefillSinglePayload.metadata!, title: "上次写的标题" },
      reused: true
    });

    // 不说出来，用户会以为这些值是应用按视频标题猜的 —— 一个「被猜中」的表单
    // 比空表单更不敢改，而它恰恰是他自己上次亲手填的那一份。
    expect(reuseHint(dialog)).not.toBeNull();
    expect(within(dialog).getByLabelText("标题")).toHaveValue("上次写的标题");
  });

  test("stays quiet about reuse when the values were derived, not remembered", async () => {
    const { dialog } = await openSingleDialog();

    expect(reuseHint(dialog)).toBeNull();
  });

  test("submits exactly what the user typed", async () => {
    const user = userEvent.setup();
    mockState.jobs = [downloadedJob()];
    mockState.biliSubmitResult = jobUploads();
    render(<App />);
    await user.click(await screen.findByRole("button", { name: /上传到 B 站 Running video/ }));
    const dialog = await screen.findByRole("dialog");

    const title = within(dialog).getByLabelText("标题");
    await user.clear(title);
    await user.type(title, "换过的标题");
    await user.click(within(dialog).getByLabelText(/上传后置顶一条评论/));
    await user.clear(within(dialog).getByLabelText(/上传后置顶一条评论/));
    await user.click(within(dialog).getByLabelText(/自动生成封面/));
    await user.click(within(dialog).getByRole("button", { name: "开始上传" }));

    await waitFor(() => expect(mockState.biliUploadBodies).toHaveLength(1));
    expect(mockState.biliUploadBodies[0]).toEqual({
      // `job_id` 取自**服务端的预填结果**（不是界面自己拼的 job），`item_id` 取自点的那一条。
      job_id: "job-format-failed",
      item_id: "item-running",
      // 版权与来源是**服务端预填的默认值原样回传**（转载 + 那条视频自己的原始链接），
      // 界面上没动过就不该被前端改写。
      metadata: {
        title: "换过的标题",
        desc: "简介",
        tags: "编程,数据库",
        tid: "122",
        copyright: "2",
        source: "https://youtu.be/format"
      },
      make_cover: false,
      comment: "",
      // 单视频的默认归集方式是「不归集」（服务端 `DEFAULT_SINGLE_GROUP_MODE`，随预填一起回来），
      // 用户没动过就照传。`season_id` 在非 `existing` 时**根本不提交这个字段** ——
      // 带上一个残留编号会让服务端按「有 season_id 就不建合集」处理。
      group_mode: "none",
      season_title: "Unsupported resolution"
    });
  });

  test("closes the dialog, updates the badge and reports the start", async () => {
    const user = userEvent.setup();
    mockState.jobs = [downloadedJob()];
    mockState.biliSubmitResult = jobUploads();
    render(<App />);
    await user.click(await screen.findByRole("button", { name: /上传到 B 站 Running video/ }));
    const dialog = await screen.findByRole("dialog");

    await user.click(within(dialog).getByRole("button", { name: "开始上传" }));

    await waitFor(() =>
      // 回执里必须带上归集去向：用户刚在表单里做过这个选择，不提它就没法确认生效了。
      expect(notice()).toHaveTextContent("已开始上传《Unsupported resolution》，不归集 —— 跑完这里会变成「已上传」。")
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(
      await screen.findByRole("button", { name: /B 站已上传；点击进入 B 站 Running video/ })
    ).toBeInTheDocument();
  });

  test("blocks the submit on an over-long title but never truncates the input", async () => {
    const { user, dialog } = await openSingleDialog();

    const title = within(dialog).getByLabelText("标题");
    const tooLong = "标".repeat(90);
    await user.clear(title);
    await user.type(title, tooLong);

    expect(within(dialog).getByText("标题超出 10 个字")).toBeInTheDocument();
    // 「只报错不截断」：静默截断会让用户以为发出去的是完整内容。
    expect(title).toHaveValue(tooLong);
    expect(within(dialog).getByRole("button", { name: "开始上传" })).toBeDisabled();
  });

  test("says how many characters over right next to the title box, not only in the list below", async () => {
    const { user, dialog } = await openSingleDialog();

    const title = within(dialog).getByLabelText("标题");
    await user.clear(title);
    await user.type(title, "标".repeat(90));

    // 原先四个计数只渲染数字，超限与合规长得一模一样（同一档灰字），
    // 用户得先读数字、再去别处比对上限才知道自己超了。
    const budget = document.getElementById("bili-title-budget") as HTMLElement;
    expect(budget).toHaveTextContent("90/80");
    expect(budget).toHaveTextContent("超出 10 个字");
    expect(budget.className).toContain("is-over");
    // 同一个事实也要对读屏与 CSS 可见（不是只给眼睛看的红字）。
    expect(title).toHaveAttribute("aria-invalid", "true");
  });

  test("a title within the limit carries no warning at all", async () => {
    const { dialog } = await openSingleDialog();

    const budget = document.getElementById("bili-title-budget") as HTMLElement;
    expect(budget).toHaveTextContent("22/80");
    expect(budget.className).not.toContain("is-over");
    expect(within(dialog).getByLabelText("标题")).not.toHaveAttribute("aria-invalid");
    // 单视频没有「渲染后」这回事 —— 那一行只为合集存在。
    expect(within(dialog).queryByText(/渲染后最长/)).not.toBeInTheDocument();
  });

  test("defaults to 转载 and prefills the original link of that very video", async () => {
    const { dialog } = await openSingleDialog();

    // 默认转载：这个工作流搬的都是从别处下载来的视频，默认「自制」会把每一单都填成假信息。
    expect(within(dialog).getByRole("radio", { name: "转载" })).toBeChecked();
    expect(within(dialog).getByRole("radio", { name: "自制" })).not.toBeChecked();
    // 来源不是让用户从零敲的 —— 那条视频本来就是从哪个链接下来的，我们知道。
    expect(within(dialog).getByLabelText(/原视频链接/)).toHaveValue("https://youtu.be/format");
  });

  test("hides the source box for 自制 and brings it back on switching back", async () => {
    const { user, dialog } = await openSingleDialog();

    await user.click(within(dialog).getByRole("radio", { name: "自制" }));

    // 自制稿没有「出处」这回事。留着输入框只会让人填一个不会被提交的值。
    expect(within(dialog).queryByLabelText(/原视频链接/)).not.toBeInTheDocument();
    expect(within(dialog).getByText("自制：这条视频是你自己的作品，不需要填出处。")).toBeInTheDocument();
    // 切走时**不清空**来源：用户来回切一下不该把已经打好的链接弄丢。
    // 真提交时带不带由服务端 `effective_source` 决定（自制稿恒不提交）。
    await user.click(within(dialog).getByRole("radio", { name: "转载" }));
    expect(within(dialog).getByLabelText(/原视频链接/)).toHaveValue("https://youtu.be/format");
  });

  test("marks the picked 版权 with the accent colour through :has()", async () => {
    const { user, dialog } = await openSingleDialog();

    // 选中态是 `.bili-choice-option:has(input:checked)` 上色的。这条断言能成立的前提是
    // 「用例 import 了样式表」—— 没 import 时 `getComputedStyle` 一律返回空串，
    // 读到的空值与「选择器不支持」是两回事（已在 ai/feature 的 04-testing §4.2 记下这个坑）。
    const labelOf = (name: "自制" | "转载") =>
      within(dialog).getByRole("radio", { name }).closest(".bili-choice-option") as HTMLElement;
    const accent = "rgb(251, 114, 153)"; // #fb7299
    expect(getComputedStyle(labelOf("转载")).borderColor).toBe(accent);
    expect(getComputedStyle(labelOf("自制")).borderColor).not.toBe(accent);

    await user.click(within(dialog).getByRole("radio", { name: "自制" }));

    // 换档之后配色跟着换 —— 只断言「选中项有颜色」不够，那样两档同时亮着也会过。
    expect(getComputedStyle(labelOf("自制")).borderColor).toBe(accent);
    expect(getComputedStyle(labelOf("转载")).borderColor).not.toBe(accent);
  });

  test("sends 自制 with no source when the user switches to it", async () => {
    const user = userEvent.setup();
    mockState.jobs = [downloadedJob()];
    mockState.biliSubmitResult = jobUploads();
    render(<App />);
    await user.click(await screen.findByRole("button", { name: /上传到 B 站 Running video/ }));
    const dialog = await screen.findByRole("dialog");

    await user.click(within(dialog).getByRole("radio", { name: "自制" }));
    await user.click(within(dialog).getByRole("button", { name: "开始上传" }));

    await waitFor(() => expect(mockState.biliUploadBodies).toHaveLength(1));
    // `biliUploadBodies` 的类型是 `Record<string, unknown>`，先取出来再断言字段 ——
    // 直接写 `.metadata.copyright` 会被 tsc 判成 `unknown`（运行时过得去，类型上是错的）。
    const metadata = mockState.biliUploadBodies[0].metadata as Record<string, unknown>;
    expect(metadata.copyright).toBe("1");
    // 来源字段确实还在 state 里（界面不清空它），但服务端 `effective_source` 会把它收成空串。
    expect(metadata.source).toBe("https://youtu.be/format");
  });

  test("blocks the submit when 转载 has no source at all", async () => {
    const { user, dialog } = await openSingleDialog();

    const source = within(dialog).getByLabelText(/原视频链接/);
    await user.clear(source);

    // 这是平台的硬要求（会被 B站 退回来），所以在输入框旁边就报，而不是让用户点了提交吃 400。
    expect(
      within(dialog).getByText("选了「转载」但没填原视频链接（转载稿必须填来源，留空会被 B站 退回来）")
    ).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "开始上传" })).toBeDisabled();
    expect(mockState.biliUploadBodies).toHaveLength(0);
  });

  test("keeps a rejected submit open so the message stays next to the form", async () => {
    const user = userEvent.setup();
    mockState.jobs = [downloadedJob()];
    mockState.biliSubmitFailure = "这个视频还没有下载好，没有可以上传的文件";
    render(<App />);
    await user.click(await screen.findByRole("button", { name: /上传到 B 站 Running video/ }));
    const dialog = await screen.findByRole("dialog");

    await user.click(within(dialog).getByRole("button", { name: "开始上传" }));

    expect(await within(dialog).findByText("这个视频还没有下载好，没有可以上传的文件")).toBeInTheDocument();
    // 表单不能关：用户正一边看着自己填的内容一边改，去页顶找红条等于让他重填一遍。
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  test("closes on Escape", async () => {
    const user = userEvent.setup();
    const { dialog } = await openSingleDialog();
    expect(dialog).toBeInTheDocument();

    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });
});

describe("App · B站 上传表单（合集）", () => {
  /** 打开合集表单：点条目行上的按钮（它只要求该条目已下载好）。 */
  async function openCollectionDialog(prefill: typeof mockState.biliPrefill = biliPrefillCollectionPayload) {
    const user = userEvent.setup();
    mockState.jobs = [downloadedPlaylistJob()];
    // **显式赋值，不在这里做「保留调用方设过的值」的小聪明**：`biliPrefill` 每用例被
    // harness 重置成单视频预填，本函数若只在它是 `null` 时才赋值，那些依赖
    // 「本函数会把它改成合集预填」的既有用例会开成单视频表单 —— 症状是找不到
    // 「把整个合集上传到 B 站」，看着像组件坏了，其实是自己把参数弄丢了。
    mockState.biliPrefill = prefill;
    mockState.biliSubmitResult = jobUploads();
    render(<App />);
    // playlist 正在跑 → 默认展开，条目行上的按钮可直接点。
    const button = await screen.findByRole("button", { name: /上传到 B 站 Part one/ });
    await user.click(button);
    return { user, dialog: await screen.findByRole("dialog") };
  }

  /**
   * 打开合集表单，并把**所有条目都标成就绪**。
   *
   * 默认夹具刻意留着一条未就绪的条目（界面上能看到「还没下载好」的提示），
   * 那会让提交按钮一直是灰的 —— 归集方式的用例关心的是另一个维度，
   * 不该被这个挡着。
   */
  async function openReadyCollectionDialog(seasonList?: typeof mockState.biliSeasonList) {
    if (seasonList !== undefined) mockState.biliSeasonList = seasonList;
    return openCollectionDialog({
      ...biliPrefillCollectionPayload,
      items: biliPrefillCollectionPayload.items.map((item) => ({ ...item, ready: true }))
    });
  }

  test("fills the template and previews what each video will become", async () => {
    const { dialog } = await openCollectionDialog();

    expect(within(dialog).getByRole("heading", { name: "把整个合集上传到 B 站" })).toBeInTheDocument();
    expect(within(dialog).getByLabelText("标题模板")).toHaveValue("{job} · 第 {index}/{count} 集");
    // 预览表用**当前**模板重算，改一个字就能看到每条会变成什么。
    expect(within(dialog).getByText("Playlist batch · 第 01/2 集")).toBeInTheDocument();
    expect(within(dialog).getByText("Playlist batch · 第 02/2 集")).toBeInTheDocument();
  });

  test("reuses the template and the collection name from the previous submission", async () => {
    const { dialog } = await openCollectionDialog({
      ...biliPrefillCollectionPayload,
      items: biliPrefillCollectionPayload.items.map((item) => ({ ...item, ready: true })),
      template: { ...biliPrefillCollectionPayload.template!, title: "{index} - {title}" },
      season_title: "上次起的合集名",
      reused: true
    });

    // 合集名**优先用上次填的那个**，退回任务名只在没有上次值时发生 ——
    // 让用户每次重投都重新打一遍合集名，和让他重新打标题是同一种沮丧。
    expect(within(dialog).getByLabelText("合集名")).toHaveValue("上次起的合集名");
    expect(within(dialog).getByLabelText("标题模板")).toHaveValue("{index} - {title}");
    expect(reuseHint(dialog)).not.toBeNull();
  });

  test("falls back to the job title for the collection name when nothing was reused", async () => {
    const { dialog } = await openReadyCollectionDialog();

    expect(within(dialog).getByLabelText("合集名")).toHaveValue("Playlist batch");
    expect(reuseHint(dialog)).toBeNull();
  });

  test("re-renders the preview from the edited template", async () => {
    const { dialog } = await openCollectionDialog();

    const title = within(dialog).getByLabelText("标题模板");
    // 用 `change` 而不是 `type`：模板里的大括号在 userEvent 里是**特殊键语法**，
    // 直接敲 `{index}` 会被当成按键而不是字面量。
    fireEvent.change(title, { target: { value: "第{index}集：{title}" } });

    expect(within(dialog).getByText("第01集：Part one")).toBeInTheDocument();
    expect(within(dialog).getByText("第02集：Part two")).toBeInTheDocument();
  });

  /**
   * 标题长度：判据量的是**渲染后的每一条**，不是模板本身。
   *
   * 夹具里那两条原标题一样长（都是 8 个字），验不出「点名的是哪一条」，
   * 所以这里把第 2 条换成一个明显更长的标题 —— 合集的真实数据正是这样长短不一的。
   */
  const TITLE_LIMIT = biliStatusPayload.limits.title;
  const LONG_SECOND_TITLE = "Part two that is much longer than the first one";
  /** 模板本身 51 个字，远在上限之内；替换掉占位符之后第 2 条会超。 */
  const TEMPLATE_WITH_SOURCE = "{job} · 第 {index}/{count} 集 · {title} · 原视频：{source}";

  function collectionWithLongSecondItem() {
    return {
      ...biliPrefillCollectionPayload,
      items: [
        { ...biliPrefillCollectionPayload.items[0], ready: true },
        { ...biliPrefillCollectionPayload.items[1], source_title: LONG_SECOND_TITLE, ready: true }
      ]
    };
  }

  /** 第 N 条按模板渲染后的标题。**用替换算出来**，不手数字符 —— 数错一个就变成另一个断言。 */
  function renderedSecond(template: string, index: string, source: string): string {
    return template
      .replace("{job}", "Playlist batch")
      .replace("{index}", index)
      .replace("{count}", "2")
      .replace("{title}", LONG_SECOND_TITLE)
      .replace("{source}", source);
  }

  test("checks the length of every rendered title, not the length of the template", async () => {
    const { dialog } = await openCollectionDialog(collectionWithLongSecondItem());

    const title = within(dialog).getByLabelText("标题模板");
    fireEvent.change(title, { target: { value: TEMPLATE_WITH_SOURCE } });

    const rendered = renderedSecond(TEMPLATE_WITH_SOURCE, "02", "https://youtu.be/two");
    const over = rendered.length - TITLE_LIMIT;
    // 前提：这一条确实超了，否则整条用例都在断言一个空集。
    expect(over).toBeGreaterThan(0);

    // 输入框旁边就写明「渲染后最长多少、第几条、超几个字」。
    expect(
      within(dialog).getByText(`渲染后最长 ${rendered.length}/${TITLE_LIMIT}（第 2 条，超出 ${over} 个字）`)
    ).toBeInTheDocument();
    // 问题列表点名**渲染后**超长的那一条（服务端回的是原始标题，前端给的是渲染结果）。
    expect(within(dialog).getByText(`第 2 条「${rendered}」渲染后超出 ${over} 个字`)).toBeInTheDocument();
    // 第 1 条渲染后是合规的，不该被一起点名。
    expect(within(dialog).queryByText(/第 1 条「/)).not.toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "开始上传全部" })).toBeDisabled();

    // 改回一个短模板就该放行 —— 只断言「按钮会灰」等于没测判据。
    fireEvent.change(title, { target: { value: "{title}" } });
    await waitFor(() =>
      expect(within(dialog).getByRole("button", { name: "开始上传全部" })).toBeEnabled()
    );
    expect(within(dialog).queryByText(/渲染后超出/)).not.toBeInTheDocument();
    expect(within(dialog).getByText(`渲染后最长 ${LONG_SECOND_TITLE.length}/${TITLE_LIMIT}（第 2 条）`)).toBeInTheDocument();
  });

  test("a template that fits everywhere leaves the rendered line unmarked", async () => {
    const { dialog } = await openCollectionDialog(collectionWithLongSecondItem());

    // 默认模板渲染出来的两条都远在上限之内。
    const line = within(dialog).getByText(/渲染后最长/);
    expect(line.className).not.toContain("is-over");
    expect(line).not.toHaveTextContent("超出");
    // 两条渲染后一样长时点名第 1 条（用户从上往下改，第一条更有指导性）。
    const first = "Playlist batch · 第 01/2 集";
    expect(within(dialog).getByText(`渲染后最长 ${first.length}/${TITLE_LIMIT}（第 1 条）`)).toBeInTheDocument();
  });

  test("tells which subtitle each entry will carry before submitting", async () => {
    const { dialog } = await openCollectionDialog();

    // 字幕是跟着稿件一起发到平台上的，用户应该在按下「上传」之前就知道有没有 ——
    // 而不是事后去看日志。文案由服务端给，前端不拼。
    expect(within(dialog).getByText("en · 人工")).toBeInTheDocument();
    expect(within(dialog).getByText("无字幕")).toBeInTheDocument();
  });

  test("names the entries that are not downloaded and refuses to start", async () => {
    const { dialog } = await openCollectionDialog();

    // 第 2 条在夹具里 `ready: false` —— 服务端会拒绝整批，界面必须先标出来，
    // 比让用户点了确认再吃一个 409 有用。
    expect(within(dialog).getByRole("alert")).toHaveTextContent("第 2 条「Part two」还没下载好，不能上传");
    expect(within(dialog).getByRole("button", { name: "开始上传全部" })).toBeDisabled();
  });

  test("shows the 版权 choice but no shared source box for a collection", async () => {
    const { dialog } = await openCollectionDialog();

    // 合集里每条视频的出处都不一样，一个共用的来源框在语义上就是错的 ——
    // 逐条来源由服务端渲染时各填各的（每条的原始链接）。
    expect(within(dialog).getByRole("radio", { name: "转载" })).toBeChecked();
    expect(within(dialog).queryByLabelText(/原视频链接/)).not.toBeInTheDocument();
  });

  test("submits the template plus the season fields the server prefill provided", async () => {
    const { user, dialog } = await openCollectionDialog();

    // 第 2 条未就绪会挡住提交，先把它从预填里摘掉（只留这一条要验的语义）。
    expect(within(dialog).getByRole("button", { name: "开始上传全部" })).toBeDisabled();
    await user.click(within(dialog).getByRole("button", { name: "取消" }));

    mockState.biliPrefill = {
      ...biliPrefillCollectionPayload,
      items: biliPrefillCollectionPayload.items.map((item) => ({ ...item, ready: true }))
    };
    await user.click(await screen.findByRole("button", { name: /上传到 B 站 Part one/ }));
    const reopened = await screen.findByRole("dialog");
    await user.click(within(reopened).getByRole("button", { name: "开始上传全部" }));

    await waitFor(() => expect(mockState.biliCollectionBodies).toHaveLength(1));
    expect(mockState.biliCollectionBodies[0]).toEqual({
      job_id: "job-playlist",
      template: {
        title: "{job} · 第 {index}/{count} 集",
        desc: "{title}\n原视频：{source}",
        tags: "编程",
        tid: "122",
        // 合集模板回传版权，但**没有**共用来源：逐条渲染时各取该条视频自己的原始链接，
        // 所以这里恒为空串（界面上也没有这个输入框）。
        copyright: "2",
        source: ""
      },
      // 合集名与简介界面上没有输入框：它们不该让用户填，直接用服务端给的值。
      season_title: "Playlist batch",
      season_desc: "Playlist batch（共 2 个视频，由本机下载工作流自动上传）",
      make_cover: true,
      comment: "本视频由个人工作流同步上传，欢迎在评论区交流。",
      // 归集方式默认「新建」—— 改动前的行为就是每次投完自动建一个。
      group_mode: "new"
    });
    await waitFor(() =>
      expect(notice()).toHaveTextContent("已开始上传《Playlist batch》的 2 个视频，逐个投完后会自动归入同一个合集。")
    );
  });

  // ---------------------------------------------------------------- 归集方式

  const twoSeasons = {
    seasons: [
      { season_id: 501, title: "C++ Weekly", desc: "每周一更", cover_url: "http://c/1.jpg", total: 22 },
      { season_id: 502, title: "数据库系统讲义", desc: "", cover_url: "", total: 0 }
    ],
    error: "",
    advice: ""
  };

  test("defaults to creating a new collection and prefills its name with the job title", async () => {
    const { dialog } = await openReadyCollectionDialog(twoSeasons);

    expect(within(dialog).getByRole("radio", { name: "新建合集" })).toBeChecked();
    expect(within(dialog).getByLabelText("合集名")).toHaveValue("Playlist batch");
    // 「加入已有」的下拉与「不归集」都不该出现 —— 单选三项互斥。
    expect(within(dialog).queryByLabelText("加进哪个合集")).not.toBeInTheDocument();
  });

  test("lists the account's own collections when 归入已有合集 is picked", async () => {
    const { user, dialog } = await openReadyCollectionDialog(twoSeasons);

    await user.click(within(dialog).getByRole("radio", { name: "加入已有合集" }));

    const select = await within(dialog).findByLabelText("加进哪个合集");
    expect(within(select).getByRole("option", { name: /C\+\+ Weekly（现有 22 个视频）/ })).toBeInTheDocument();
    expect(within(select).getByRole("option", { name: /数据库系统讲义/ })).toBeInTheDocument();
    // 没选具体合集之前不能提交 —— 服务端会拒（`validate_group_choice`）。
    expect(within(dialog).getByRole("button", { name: "开始上传全部" })).toBeDisabled();
    expect(within(dialog).getByText("选了「加入已有合集」但还没选是哪个合集")).toBeInTheDocument();
  });

  test("submits the chosen season id and drops it again when switching back to 新建", async () => {
    const { user, dialog } = await openReadyCollectionDialog(twoSeasons);

    await user.click(within(dialog).getByRole("radio", { name: "加入已有合集" }));
    await user.selectOptions(await within(dialog).findByLabelText("加进哪个合集"), "502");
    // 选中后显示该合集自己的简介（不是用户能改的东西）。
    await waitFor(() => expect(within(dialog).getByRole("button", { name: "开始上传全部" })).toBeEnabled());
    await user.click(within(dialog).getByRole("button", { name: "开始上传全部" }));

    await waitFor(() => expect(mockState.biliCollectionBodies).toHaveLength(1));
    expect(mockState.biliCollectionBodies[0]).toMatchObject({
      group_mode: "existing",
      season_id: 502
    });
  });

  test("never sends a stale season id alongside 新建合集", async () => {
    // 判据：`group_into_season` 的优先级是「有 season_id 就不建合集」。
    // 带着残留编号提交会让「新建合集」悄悄变成往一个老合集里塞视频 ——
    // 所以前端必须在切回新建时把它撤掉。
    const { user, dialog } = await openReadyCollectionDialog(twoSeasons);

    await user.click(within(dialog).getByRole("radio", { name: "加入已有合集" }));
    await user.selectOptions(await within(dialog).findByLabelText("加进哪个合集"), "502");
    await user.click(within(dialog).getByRole("radio", { name: "新建合集" }));
    await waitFor(() => expect(within(dialog).getByRole("button", { name: "开始上传全部" })).toBeEnabled());
    await user.click(within(dialog).getByRole("button", { name: "开始上传全部" }));

    await waitFor(() => expect(mockState.biliCollectionBodies).toHaveLength(1));
    expect(mockState.biliCollectionBodies[0]).toMatchObject({ group_mode: "new" });
    expect(mockState.biliCollectionBodies[0]).not.toHaveProperty("season_id");
  });

  test("submits 不归集 without asking for a season at all", async () => {
    const { user, dialog } = await openReadyCollectionDialog(twoSeasons);

    await user.click(within(dialog).getByRole("radio", { name: "不归集" }));

    // 「不归集」时连合集名输入框都不该出现 —— 没有要填的东西。
    expect(within(dialog).queryByLabelText("合集名")).not.toBeInTheDocument();
    expect(within(dialog).queryByLabelText("加进哪个合集")).not.toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "开始上传全部" }));

    await waitFor(() => expect(mockState.biliCollectionBodies).toHaveLength(1));
    expect(mockState.biliCollectionBodies[0]).toMatchObject({ group_mode: "none" });
    expect(notice()).toHaveTextContent("不归集");
  });

  test("lets the user rename a collection they are about to create", async () => {
    const { user, dialog } = await openReadyCollectionDialog(twoSeasons);

    const titleBox = within(dialog).getByLabelText("合集名");
    await user.clear(titleBox);
    await user.type(titleBox, "我的合集");
    await user.click(within(dialog).getByRole("button", { name: "开始上传全部" }));

    await waitFor(() => expect(mockState.biliCollectionBodies).toHaveLength(1));
    expect(mockState.biliCollectionBodies[0]).toMatchObject({ season_title: "我的合集" });
  });

  test("explains why there is nothing to pick and points at 新建 when the account has no collection", async () => {
    const { user, dialog } = await openReadyCollectionDialog({ seasons: [], error: "", advice: "" });

    await user.click(within(dialog).getByRole("radio", { name: "加入已有合集" }));

    expect(await within(dialog).findByText(/这个账号下还没有合集/)).toBeInTheDocument();
    expect(within(dialog).getByRole("radio", { name: "新建合集" })).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "开始上传全部" })).toBeDisabled();
  });

  test("shows the login advice instead of pretending the account has no collection", async () => {
    // 「没登录」与「真的没有合集」在界面上必须是两句话：都显示成空列表会让用户
    // 以为是自己还没建过合集，于是跑去新建一个重复的。
    const { user, dialog } = await openReadyCollectionDialog({
      seasons: [],
      error: "还没有登录 B站",
      advice: "点「扫码登录」用 B站 App 扫码，或导入一份已登录的 cookies.json"
    });

    await user.click(within(dialog).getByRole("radio", { name: "加入已有合集" }));

    expect(await within(dialog).findByText("点「扫码登录」用 B站 App 扫码，或导入一份已登录的 cookies.json")).toBeInTheDocument();
    expect(within(dialog).queryByText(/这个账号下还没有合集/)).not.toBeInTheDocument();
  });
});

/**
 * 归集方式对**单视频**也是开放的。
 *
 * 单开一个块而不是并进上面那个，是因为这一组钉的是「单视频与合集口径不同的那几处」：
 * 默认值不同（不归集 vs 新建）、文案不同（这条视频 vs 这些视频），
 * 而它们最容易在「顺手复用合集那边的代码」时被抹平。
 */
describe("App · B站 上传表单（单视频的归集方式）", () => {
  test("offers 归集方式 on a single video, defaulting to 不归集", async () => {
    const user = userEvent.setup();
    mockState.jobs = [downloadedJob()];
    mockState.biliPrefill = biliPrefillSinglePayload;
    render(<App />);

    await user.click(await screen.findByRole("button", { name: /上传到 B 站/ }));
    const dialog = await screen.findByRole("dialog");

    // 单视频也必须有这个控件：只给 playlist 开放的话，这个能力要先下载一整个合集
    // 才够得着 —— 本机 12 个任务全是单视频时，界面上永远看不到它（用户实际报的就是这个）。
    expect(within(dialog).getByRole("radiogroup", { name: "归集方式" })).toBeInTheDocument();
    // 默认必须是「不归集」：改动前单视频从不进任何合集，默认成「新建」会让此后
    // 每条单视频投稿都在账号里多出一个只含它自己的合集。
    expect(within(dialog).getByRole("radio", { name: "不归集" })).toBeChecked();
    expect(within(dialog).getByRole("radio", { name: "新建合集" })).not.toBeChecked();
    // 默认那两项都不需要合集列表，所以它没回来也不该挡住提交。
    expect(within(dialog).getByRole("button", { name: "开始上传" })).toBeEnabled();
  });

  test("sends the chosen existing collection as a season id", async () => {
    const user = userEvent.setup();
    mockState.jobs = [downloadedJob()];
    mockState.biliPrefill = biliPrefillSinglePayload;
    mockState.biliSubmitResult = jobUploads();
    mockState.biliSeasonList = {
      seasons: [
        { season_id: 111, title: "C++ Weekly", desc: "讲义", cover_url: "", total: 24 },
        { season_id: 222, title: "数据库系统", desc: "", cover_url: "", total: 0 }
      ],
      error: "",
      advice: ""
    };
    render(<App />);
    await user.click(await screen.findByRole("button", { name: /上传到 B 站 Running video/ }));
    const dialog = await screen.findByRole("dialog");

    await user.click(within(dialog).getByRole("radio", { name: "加入已有合集" }));
    await user.selectOptions(await within(dialog).findByLabelText("加进哪个合集"), "222");
    await user.click(within(dialog).getByRole("button", { name: "开始上传" }));

    await waitFor(() => expect(mockState.biliUploadBodies).toHaveLength(1));
    expect(mockState.biliUploadBodies[0].group_mode).toBe("existing");
    // 编号按 `Number()` 提交 —— 下拉的 `value` 是字符串，直接传会变成 `"222"`，
    // 而服务端那个字段是 `int | None`（Pydantic 会转，但别指望它）。
    expect(mockState.biliUploadBodies[0].season_id).toBe(222);
  });

  test("blocks the submit on a single video when 加入已有合集 is picked but nothing is chosen", async () => {
    const user = userEvent.setup();
    mockState.jobs = [downloadedJob()];
    mockState.biliPrefill = biliPrefillSinglePayload;
    mockState.biliSeasonList = {
      seasons: [{ season_id: 111, title: "C++ Weekly", desc: "", cover_url: "", total: 24 }],
      error: "",
      advice: ""
    };
    render(<App />);
    await user.click(await screen.findByRole("button", { name: /上传到 B 站 Running video/ }));
    const dialog = await screen.findByRole("dialog");

    await user.click(within(dialog).getByRole("radio", { name: "加入已有合集" }));

    // 单视频与合集**同一条规则**：控件在两处都可见，判据就不能只在一处成立 ——
    // 否则单视频用户只会吃到一个服务端 400（`missing_season_id`），
    // 而这句话明明可以就在下拉旁边说出来。
    expect(await within(dialog).findByText("选了「加入已有合集」但还没选是哪个合集")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "开始上传" })).toBeDisabled();

    // 真的选一个之后就该放行（否则测的只是「按钮会灰」而不是「规则成立」）。
    await user.selectOptions(await within(dialog).findByLabelText("加进哪个合集"), "111");
    await waitFor(() => expect(within(dialog).getByRole("button", { name: "开始上传" })).toBeEnabled());
  });

  test("never sends a stale season id alongside 新建合集 on a single video", async () => {
    const user = userEvent.setup();
    mockState.jobs = [downloadedJob()];
    mockState.biliPrefill = biliPrefillSinglePayload;
    mockState.biliSubmitResult = jobUploads();
    mockState.biliSeasonList = {
      seasons: [{ season_id: 111, title: "C++ Weekly", desc: "", cover_url: "", total: 24 }],
      error: "",
      advice: ""
    };
    render(<App />);
    await user.click(await screen.findByRole("button", { name: /上传到 B 站 Running video/ }));
    const dialog = await screen.findByRole("dialog");

    // 先选一个已有合集，再改选「新建」—— 残留的编号必须被撤掉，否则服务端
    // 会按「有 season_id 就不建合集」处理，于是「新建」变成了往 111 里塞视频。
    await user.click(within(dialog).getByRole("radio", { name: "加入已有合集" }));
    await user.selectOptions(await within(dialog).findByLabelText("加进哪个合集"), "111");
    await user.click(within(dialog).getByRole("radio", { name: "新建合集" }));

    const name = await within(dialog).findByLabelText("合集名");
    await user.clear(name);
    await user.type(name, "我自己的合集");
    await user.click(within(dialog).getByRole("button", { name: "开始上传" }));

    await waitFor(() => expect(mockState.biliUploadBodies).toHaveLength(1));
    expect(mockState.biliUploadBodies[0].group_mode).toBe("new");
    expect(mockState.biliUploadBodies[0]).not.toHaveProperty("season_id");
    // 填了合集名就必须真的提交它（否则测的只是「控件存在」而不是「控件生效」）。
    expect(mockState.biliUploadBodies[0].season_title).toBe("我自己的合集");
  });

  test("does not read the collection list until 加入已有合集 is chosen", async () => {
    const user = userEvent.setup();
    mockState.jobs = [downloadedJob()];
    mockState.biliPrefill = biliPrefillSinglePayload;
    render(<App />);
    await user.click(await screen.findByRole("button", { name: /上传到 B 站 Running video/ }));
    const dialog = await screen.findByRole("dialog");

    // 开窗时**一次都不读**。默认那两个选项（单视频「不归集」/ 合集「新建」）都不看这份列表，
    // 而读它是一次真实的网络往返 —— 本机直连 B站 域名时 DNS 解析能卡上好几分钟
    // （`socket` 超时**不覆盖 DNS**），放在开窗路径上就是「一打开上传窗口先转好几圈」。
    await waitFor(() => expect(within(dialog).getByRole("button", { name: "开始上传" })).toBeEnabled());
    expect(mockState.biliSeasonReads).toBe(0);

    // 真的选中它才读，且**只读一次** —— 弹窗每收到一条 SSE 事件就重渲染一次，
    // 若去重失效，这里会随着重渲染反复发请求。
    await user.click(within(dialog).getByRole("radio", { name: "加入已有合集" }));
    await waitFor(() => expect(mockState.biliSeasonReads).toBe(1));
    await user.click(within(dialog).getByRole("radio", { name: "新建合集" }));
    await user.click(within(dialog).getByRole("radio", { name: "加入已有合集" }));
    expect(mockState.biliSeasonReads).toBe(1);
  });

  test("still uploads when the collection list cannot be read", async () => {
    const user = userEvent.setup();
    mockState.jobs = [downloadedJob()];
    mockState.biliPrefill = biliPrefillSinglePayload;
    mockState.biliSubmitResult = jobUploads();
    // `null` = 让 `/api/bilibili/seasons` 直接失败。
    mockState.biliSeasonList = null;
    render(<App />);
    await user.click(await screen.findByRole("button", { name: /上传到 B 站 Running video/ }));
    const dialog = await screen.findByRole("dialog");

    // 读列表**不是整张表单的前置条件**：默认那两项都不需要它。
    // 若把它当成门槛，一次读失败就等于「视频传不上去」，而用户可能压根不打算归集。
    await waitFor(() => expect(within(dialog).getByRole("button", { name: "开始上传" })).toBeEnabled());

    // 但要点「加入已有」时必须把失败原因说出来，且**不是「正在读取」** ——
    // 停在「正在读取」会让用户等一个永远不会结束的加载。
    // 提示取的是 `advice`（可操作的那句）而不是 `error`，与既有合集/未登录用例同一口径。
    await user.click(within(dialog).getByRole("radio", { name: "加入已有合集" }));
    expect(
      await within(dialog).findByText("可能是登录已失效或网络不通。改选「新建合集」或「不归集」照样能投稿。")
    ).toBeInTheDocument();
    expect(within(dialog).queryByText(/正在读取账号里的合集/)).not.toBeInTheDocument();

    // 说得出原因，用户就能退回「不归集」把视频投出去（这才是这个兜底的意义）。
    await user.click(within(dialog).getByRole("radio", { name: "不归集" }));
    await user.click(within(dialog).getByRole("button", { name: "开始上传" }));
    await waitFor(() => expect(mockState.biliUploadBodies).toHaveLength(1));
    expect(mockState.biliUploadBodies[0].group_mode).toBe("none");
  });
});

describe("App · B站 稿件明细与重试", () => {
  test("shows the failure reason, the advice and a retry button", async () => {
    const user = userEvent.setup();
    mockState.jobs = [downloadedJob()];
    mockState.biliUploads = [jobUploads([biliUploadFailedPayload], { badge: "failed", badge_text: "上传失败", badge_tone: "bad" })];
    render(<App />);

    expect(await screen.findByText(/上传中断：投稿工具返回了错误（退出码 1）/)).toBeInTheDocument();
    expect(screen.getByText("先确认网络与登录状态，再点「重试」。")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "重试" }));
    await waitFor(() => expect(mockState.biliRetryUploadIds).toEqual(["bili-2"]));
    await waitFor(() => expect(notice()).toHaveTextContent("已重新排队。"));
  });

  test("calls a pending check '重新核对' and never offers a plain retry for it", async () => {
    const user = userEvent.setup();
    mockState.jobs = [downloadedJob()];
    mockState.biliRetryAction = "reverify";
    mockState.biliUploads = [
      jobUploads(
        [
          {
            ...biliUploadFailedPayload,
            id: "bili-3",
            status: "unverified",
            status_text: "待核对",
            badge: "unverified",
            badge_text: "待核对",
            badge_tone: "warn",
            error: "已提交，但还没在 B站 看到它",
            advice: "B站 的稿件列表有几十秒到几分钟的延迟。稍后点「重新核对」再查一次。"
          }
        ],
        { badge: "unverified", badge_text: "待核对", badge_tone: "warn" }
      )
    ];
    render(<App />);

    // 「待核对」不是失败：措辞必须分开，否则用户会去重投一条**已经投上去**的稿件。
    const button = await screen.findByRole("button", { name: "重新核对" });
    expect(screen.queryByRole("button", { name: "重试" })).not.toBeInTheDocument();

    await user.click(button);
    await waitFor(() => expect(mockState.biliRetryUploadIds).toEqual(["bili-3"]));
    await waitFor(() => expect(notice()).toHaveTextContent("已重新核对。"));
  });

  test("offers no retry for a published item, only a link to it", async () => {
    const user = userEvent.setup();
    const open = vi.spyOn(window, "open").mockImplementation(() => null);
    mockState.jobs = [downloadedJob()];
    mockState.biliUploads = [jobUploads()];
    render(<App />);

    await user.click(await screen.findByRole("button", { name: "在 B 站打开 Unsupported resolution" }));
    expect(open).toHaveBeenCalledWith(
      "https://www.bilibili.com/video/BV1xx411c7mD",
      "_blank",
      "noopener,noreferrer"
    );
    // 已经成功的条目不给重试按钮：按钮摆在那就是在暗示用户去点。
    expect(screen.queryByRole("button", { name: "重试" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "重新核对" })).not.toBeInTheDocument();
  });

  test("lets a failed collection be regrouped on its own", async () => {
    const user = userEvent.setup();
    mockState.jobs = [downloadedPlaylistJob()];
    mockState.biliUploads = [
      jobUploads([], {
        job_id: "job-playlist",
        badge: "uploaded",
        badge_text: "已上传",
        badge_tone: "ok",
        season: {
          id: "season-1",
          job_id: "job-playlist",
          title: "Playlist batch",
          desc: "",
          season_id: 9001,
          status: "failed",
          status_text: "归集失败",
          upload_ids: ["bili-1"],
          error: "合集建好了，但视频没能加进去",
          advice: "点「重试归集」再试 —— 合集已经存在，重试只会补加视频，不会重复建合集"
        }
      })
    ];
    render(<App />);

    // 徽标仍是「已上传」（视频本身都投上去了），失败的是归集这一步 —— 两者分开记。
    expect(await screen.findByRole("button", { name: /B站：已上传 Playlist batch/ })).toBeInTheDocument();
    // 正则匹配：这一行是 `合集：{status_text}` 两个文本节点，整段比字符串更稳。
    expect(await screen.findByText(/合集：归集失败/)).toBeInTheDocument();
    expect(screen.getByText("合集建好了，但视频没能加进去")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "重试归集" }));
    await waitFor(() => expect(mockState.biliRetrySeasonIds).toEqual(["season-1"]));
    await waitFor(() => expect(notice()).toHaveTextContent("已重新归集。"));
  });

  test("「重试归集」在前提不满足时也要在原地留下痕迹：加载态 + 落款 + 说实话的回执", async () => {
    // 这一条钉的是**用户报的那句话**：「点了没有任何反应」。
    // 视频还在审核时，那一下其实连归集请求都不会发（服务端 `action=waiting`），
    // 行里的文案因此一个像素都不变；而唯一的回执在页顶，卡片在视口里的时候它在一千多像素外。
    const user = userEvent.setup();
    mockState.jobs = [downloadedJob()];
    mockState.biliSeasonRetryAction = "waiting";
    mockState.biliSeasonRetryDelayMs = 400;
    mockState.biliUploads = [
      jobUploads(
        [
          {
            ...biliUploadPayload,
            status: "reviewing",
            status_text: "审核中",
            progress: 0.95,
            started_at: new Date(Date.now() - 600_000).toISOString(),
            finished_at: new Date(Date.now() - 540_000).toISOString()
          }
        ],
        {
          job_id: "job-running",
          season: {
            id: "season-1",
            job_id: "job-running",
            title: "合集",
            desc: "",
            season_id: 9001,
            status: "pending",
            status_text: "等待归集",
            upload_ids: [biliUploadPayload.id],
            error: "视频还在 B站 排队审核，暂时没法加进合集",
            advice: "等 B站 审核通过后点「重试归集」—— 它会先去平台核对分P编号，再归集"
          }
        }
      )
    ];
    render(<App />);

    const button = await screen.findByRole("button", { name: "重试归集" });
    expect(button).toBeEnabled();
    // 点之前没有落款：它是「上一次真的跑过」的凭据，不能一进来就有。
    expect(document.querySelector(".bili-retry-stamp")).toBeNull();

    await user.click(button);
    // 过程中：文案换成「归集中…」并禁用（真实后端要回读平台，这一下要等）
    await waitFor(() => expect(screen.getByRole("button", { name: "归集中…" })).toBeDisabled());
    // 结束后：落款出现、按钮恢复可点
    await waitFor(
      () => expect(document.querySelector(".bili-retry-stamp")).toHaveTextContent(/^上次重试 \d\d:\d\d:\d\d$/),
      { timeout: 4000 }
    );
    expect(screen.getByRole("button", { name: "重试归集" })).toBeEnabled();
    // 回执说的是实话，而且**不是绿色对勾**：「什么都没发」不能长得像「做成了」。
    await waitFor(() => expect(document.querySelector(".notice.is-info")).toHaveTextContent("这次没有发起归集"));
    expect(mockState.biliRetrySeasonIds).toEqual(["season-1"]);
  });

  test("announces only a real transition into a settled badge", async () => {
    mockState.jobs = [downloadedJob()];
    mockState.biliUploads = [jobUploads()];
    render(<App />);

    const settled = { name: /B 站已上传；点击进入 B 站 Running video/ };
    await screen.findByRole("button", settled);
    const source = mockState.eventSources[0];
    expect(source, "App 必须订阅 /api/events 才能知道后台跑完了").toBeDefined();

    // 1) 首次拉到状态**不播报**：否则每次打开页面都会把历史结果重播一遍。
    source.onmessage?.(new MessageEvent("message", { data: "{}" }));
    await waitFor(() => expect(screen.getByRole("button", settled)).toBeInTheDocument());
    expect(notice()).toBeNull();

    // 2) 徽标没变也不播报：`biliUploads` 在每条 SSE 事件后都会被重拉，没变就是没变化。
    source.onmessage?.(new MessageEvent("message", { data: "{}" }));
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    expect(notice()).toBeNull();

    // 3) 真的变了、且落到终态，才播报 —— 并且用的是**最新**那一档徽标。
    mockState.biliUploads = [jobUploads([], { badge: "failed", badge_text: "上传失败", badge_tone: "bad" })];
    source.onmessage?.(new MessageEvent("message", { data: "{}" }));
    await waitFor(() => expect(notice()).toHaveTextContent("《Running video》上传失败"));
  });
});

describe("App · B站 设置里的体检与登录", () => {
  test("重新体检真的会再查一次，并留下体检时间", async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText("biliup 0.2.4");
    // 首次挂载体检一次（带账号探测）。
    expect(mockState.biliStatusQueries).toEqual([true]);

    await user.click(screen.getByRole("button", { name: "重新体检" }));

    await waitFor(() => expect(mockState.biliStatusQueries).toEqual([true, true]));
    // 结论没变时，「上次体检 11:58:03」是唯一能看出这一下真的跑了的证据 ——
    // 原先这个按钮没有它、也没有加载态，界面一个像素都不动，用户以为它没实现。
    expect(await screen.findByText(/上次体检 \d{2}:\d{2}:\d{2}/)).toBeInTheDocument();
  });

  test("体检进行中按钮不可点，并写明「体检中…」", async () => {
    mockState.biliStatusDelayMs = 60;
    render(<App />);

    // 先拿到**稳定的元素句柄**再断言状态：按名字查会把「体检中…」那一刻的元素抓成快照，
    // 等断言跑到时它已经换回「重新体检」了（60 毫秒的窗口，跑整包时尤其容易踩）。
    const button = await screen.findByRole("button", { name: "重新体检" });
    expect(button).toBeEnabled();
    await userEvent.setup().click(button);

    await waitFor(() => expect(button).toBeDisabled());
    expect(button).toHaveTextContent("体检中…");
    // 结束之后回到可点状态，落款出现。
    await waitFor(() => expect(button).toBeEnabled());
    expect(screen.getByText(/上次体检/)).toBeInTheDocument();
  });

  test("账号探测失败时仍给离线结论，并说明原因（不是只剩一条红条）", async () => {
    // 复现本机的真实形态：B站 连不上 → 后端 502 `upstream connect failed`。
    // 原先这条路径直接抛：页顶一条红条，**面板还是上一次的旧结论** —— 用户既不知道
    // 投稿工具现在还在不在，也不知道那条红条指哪件事。
    mockState.biliStatusFails = true;
    render(<App />);

    expect(await screen.findByText(/账号探测失败/)).toBeInTheDocument();
    // 原因要带出来：只说「失败」等于把问题丢回给用户。
    expect(screen.getByText(/upstream connect failed/)).toBeInTheDocument();
    // 离线那一半照旧有结论（它与网络无关）。
    expect(screen.getByText("ffmpeg 可用")).toBeInTheDocument();
  });

  test("点「扫码登录」时，页顶那条与它无关的旧错误会消失", async () => {
    // 错误横幅是页面级的：上一条别的动作遗留的红条会一直挂着，用户把它读成
    // 「我刚点的这个失败了」—— 实测有人把「重试投稿超时」读成「扫码登录失败」。
    mockState.biliStatusAllFails = true;
    const user = userEvent.setup();
    render(<App />);

    const alert = await screen.findByRole("alert");
    // 内容就是后端给的那句（`errorMessage` 优先用 `err.message`）——本用例只关心它**会不会被清掉**。
    expect(alert).toHaveTextContent(/upstream connect failed/);

    mockState.biliStatusAllFails = false;
    await user.click(screen.getByRole("button", { name: "扫码登录" }));

    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
    // 二维码照常出来 —— 清错误不能把正常流程一起清掉。
    await waitFor(() => expect(document.querySelector(".bili-qr-image svg")).not.toBeNull());
  });

  test("lists every prerequisite with the next step attached", async () => {
    render(<App />);

    expect(await screen.findByText("biliup 0.2.4")).toBeInTheDocument();
    expect(screen.getByText("还没有登录")).toBeInTheDocument();
    expect(screen.getByText("ffmpeg 可用")).toBeInTheDocument();
    // 每条提示都要带上「要么一条命令、要么一个按钮」——只报结论等于把问题丢回给用户。
    expect(screen.getByText("还没有登录 B站 —— 在下面点「扫码登录」，或者导入一份已有的 cookies.json。")).toBeInTheDocument();
  });

  test("explains a zero-byte stub and offers to repair it", async () => {
    const user = userEvent.setup();
    mockState.biliStatus = {
      ...biliStatusPayload,
      biliup: {
        found: true,
        path: String.raw`C:\Users\user\AppData\Local\Microsoft\WinGet\Links\biliup.EXE`,
        version: null,
        problem: "找到的 biliup.exe 是个 0 字节的空壳，无法运行",
        advice: "点「修复」由本应用重建它"
      }
    };
    render(<App />);

    expect(await screen.findByText("找到的 biliup.exe 是个 0 字节的空壳，无法运行")).toBeInTheDocument();
    // 空壳值得单独一个按钮：报错（「不是此 OS 平台的有效应用」）与真实病因毫无关系。
    await user.click(screen.getByRole("button", { name: "修复" }));
    // 统一封装会给它加 `Content-Type`，所以只钉住 URL 与 POST。
    await waitFor(() =>
      expect(fetch).toHaveBeenCalledWith("/api/bilibili/tools/repair", expect.objectContaining({ method: "POST" }))
    );
  });

  test("shows the credential count only, never a value or a name", async () => {
    mockState.biliStatus = biliStatusLoggedInPayload;
    render(<App />);

    expect(await screen.findByText("已登录：长沙的 HX")).toBeInTheDocument();
    expect(screen.getByText("登录文件里有 9 项凭据（只显示数量，不显示内容）。")).toBeInTheDocument();
    // 连凭据**名字**都不列 —— 摘要只给数量与账号名。
    expect(screen.queryByText(/SESSDATA/)).not.toBeInTheDocument();
  });

  test("calls out a login file that git already tracks", async () => {
    mockState.biliStatus = {
      ...biliStatusLoggedInPayload,
      login: { ...biliStatusLoggedInPayload.login, tracked_by_git: true }
    };
    render(<App />);

    // 这比「没登录」严重得多，且用户通常完全不知道。
    expect(await screen.findByText(/登录文件已经被 git 跟踪，等于把账号密码提交到了仓库里/)).toBeInTheDocument();
    expect(screen.getByText("git rm --cached <该文件>")).toBeInTheDocument();
  });

  test("says which fields a half-written login file is missing", async () => {
    mockState.biliStatus = {
      ...biliStatusPayload,
      login: { ...biliStatusPayload.login, present: true, cookie_count: 1, names: ["DedeUserID"] }
    };
    render(<App />);

    expect(await screen.findByText("登录信息不完整，缺 SESSDATA、bili_jct、DedeUserID")).toBeInTheDocument();
  });

  test("renders the QR code inline and stops once the login is confirmed", async () => {
    const user = userEvent.setup();
    mockState.biliLoginPollState = "confirmed";
    mockState.biliLoginPollMessage = "确认成功";
    render(<App />);

    await screen.findByText("还没有登录");
    await user.click(screen.getByRole("button", { name: "扫码登录" }));

    // 二维码是服务端生成的 SVG 文本，直接内联 —— 不牵入位图依赖。
    expect(await screen.findByText("用 B站 App 扫描这个二维码，并在手机上确认登录。")).toBeInTheDocument();
    expect(document.querySelector(".bili-qr-image svg")).not.toBeNull();
    // `confirmed` 之前不落盘、不刷新；确认之后体检结果换成已登录，二维码收起来。
    mockState.biliStatus = biliStatusLoggedInPayload;
    expect(await screen.findByText("已登录：长沙的 HX", {}, { timeout: 4000 })).toBeInTheDocument();
    await waitFor(() => expect(document.querySelector(".bili-qr-image")).toBeNull());
  }, 10_000);

  test("keeps waiting while the code has only been scanned", async () => {
    const user = userEvent.setup();
    mockState.biliLoginPollState = "scanned";
    mockState.biliLoginPollMessage = "已扫码，请在手机上确认";
    render(<App />);

    await screen.findByText("还没有登录");
    await user.click(screen.getByRole("button", { name: "扫码登录" }));

    expect(await screen.findByText("已扫码，请在手机上确认", {}, { timeout: 4000 })).toBeInTheDocument();
    // 手机确认之前不重新体检 —— 登录态还没落盘。
    expect(screen.getByText("还没有登录")).toBeInTheDocument();
  }, 10_000);

  test("hides the clear button until there is a login to clear", async () => {
    render(<App />);

    await screen.findByText("还没有登录");
    expect(screen.queryByRole("button", { name: "清除登录" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "导入 cookies.json" })).toBeInTheDocument();
  });
});
