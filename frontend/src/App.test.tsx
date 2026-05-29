import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import App from "./App";
import {
  analyzePayload,
  automaticResolutionFallback,
  jobPayload,
  lockedEdgeCookieDetail,
  pausedJobPayload,
  playlistFallbackJobPayload,
  playlistJobPayload,
  resolutionFallback,
  settingsPayload,
  singleFallbackJobPayload,
  unselectableResolutionFallback
} from "./test/appFixtures";
import type { Job } from "./types";

let currentAnalyzePayload = analyzePayload;
let currentJobsPayload: Job[] = [jobPayload, pausedJobPayload, playlistJobPayload];
let currentSettingsPayload = settingsPayload;
let browserCookieImportLocked = false;
let analyzeLockedByEdgeCookies = false;
let localFileActionFailure: string | null = null;

describe("App", () => {
  beforeEach(() => {
    currentAnalyzePayload = analyzePayload;
    currentJobsPayload = [jobPayload, pausedJobPayload, playlistJobPayload];
    currentSettingsPayload = settingsPayload;
    browserCookieImportLocked = false;
    analyzeLockedByEdgeCookies = false;
    localFileActionFailure = null;
    vi.stubGlobal("confirm", vi.fn(() => true));
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: {
        writeText: vi.fn().mockResolvedValue(undefined)
      }
    });
    vi.stubGlobal("EventSource", class {
      onmessage: ((event: MessageEvent) => void) | null = null;
      close = vi.fn();
      constructor(public url: string) {}
    });

    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/api/settings") && (!init?.method || init.method === "GET")) {
          return Response.json(currentSettingsPayload);
        }
        if (url.endsWith("/api/settings") && init?.method === "PUT") {
          return Response.json({ ...currentSettingsPayload, ...JSON.parse(String(init.body)) });
        }
        if (url.endsWith("/api/settings/download-dir/select")) {
          return Response.json({ ...currentSettingsPayload, download_dir: "D:\\Videos" });
        }
        if (url.endsWith("/api/cookies") && init?.method === "POST") {
          currentSettingsPayload = { ...currentSettingsPayload, cookies_enabled: true };
          return Response.json({ enabled: true, filename: "cookies.txt" });
        }
        if (url.endsWith("/api/cookies/from-browser") && init?.method === "POST") {
          const body = JSON.parse(String(init.body));
          if (browserCookieImportLocked && !body.close_browser_if_locked) {
            return Response.json({ detail: lockedEdgeCookieDetail }, { status: 409 });
          }
          currentSettingsPayload = { ...currentSettingsPayload, cookies_enabled: true };
          return Response.json({
            enabled: true,
            filename: "cookies.txt",
            source: "browser",
            browser: "edge",
            imported_count: 4
          });
        }
        if (url.endsWith("/api/cookies") && init?.method === "DELETE") {
          currentSettingsPayload = { ...currentSettingsPayload, cookies_enabled: false };
          return Response.json({ enabled: false, filename: null });
        }
        if (url.endsWith("/api/jobs")) {
          if (init?.method === "POST") {
            return Response.json({ id: "job-1", status: "queued", total_items: 1, items: [] }, { status: 201 });
          }
          return Response.json(currentJobsPayload);
        }
        if (url.endsWith("/api/jobs/batch")) {
          return Response.json({ affected_job_ids: ["job-running", "job-paused"], jobs: [] });
        }
        if (url.endsWith("/api/jobs/job-running/pause")) {
          return Response.json({ ...jobPayload, status: "paused" });
        }
        if (url.endsWith("/api/jobs/job-paused/restart")) {
          return Response.json({ ...pausedJobPayload, status: "queued" });
        }
        if (url.endsWith("/api/jobs/job-format-failed/restart")) {
          return Response.json({ ...singleFallbackJobPayload, status: "queued" });
        }
        if (
          (url.endsWith("/api/jobs/job-running/play") ||
            url.endsWith("/api/jobs/job-running/open-folder") ||
            url.endsWith("/api/jobs/job-playlist/open-folder") ||
            url.endsWith("/api/jobs/job-playlist/items/item-playlist-1/open-folder") ||
            url.endsWith("/api/jobs/job-playlist/items/item-playlist-1/play")) &&
          init?.method === "POST"
        ) {
          if (localFileActionFailure) {
            return Response.json({ detail: localFileActionFailure }, { status: 409 });
          }
          return new Response(null, { status: 204 });
        }
        if (url.endsWith("/api/jobs/job-playlist/items/delete")) {
          const body = JSON.parse(String(init?.body ?? "{}"));
          const deleted = new Set<string>(body.item_ids ?? []);
          const remainingItems = playlistJobPayload.items.filter((item) => !deleted.has(item.id));
          return Response.json({
            deleted_item_ids: Array.from(deleted),
            job_deleted: remainingItems.length === 0,
            job: remainingItems.length
              ? {
                  ...playlistJobPayload,
                  total_items: remainingItems.length,
                  items: remainingItems
                }
              : null
          });
        }
        if (url.endsWith("/api/jobs/job-playlist/items/item-playlist-2/restart")) {
          return Response.json({
            ...playlistJobPayload,
            status: "queued",
            items: [
              playlistJobPayload.items[0],
              { ...playlistJobPayload.items[1], status: "queued", progress: 0, error: null }
            ]
          });
        }
        if ((url.endsWith("/api/jobs/job-running") || url.endsWith("/api/jobs/job-running?delete_files=true")) && init?.method === "DELETE") {
          return new Response(null, { status: 204 });
        }
        if (url.endsWith("/api/analyze")) {
          if (analyzeLockedByEdgeCookies && !currentSettingsPayload.cookies_enabled) {
            return Response.json({ detail: lockedEdgeCookieDetail }, { status: 409 });
          }
          analyzeLockedByEdgeCookies = false;
          return Response.json(currentAnalyzePayload);
        }
        return Response.json({});
      })
    );
  });

  afterEach(() => {
    cleanup();
    localStorage.clear();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  test("analyzes a playlist and submits selected rows with subtitle options", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.type(screen.getByLabelText("视频或 playlist 链接"), "https://youtube.com/playlist?list=abc");
    await user.click(screen.getByRole("button", { name: "解析链接" }));

    expect(await screen.findByText("Batch")).toBeInTheDocument();
    expect(screen.getByText("One")).toBeInTheDocument();
    expect(screen.getByText("Two")).toBeInTheDocument();
    expect(screen.getByLabelText("清晰度")).toHaveValue("resolution:1440p");
    expect(screen.getByText("字幕：来源 两者都要（人工字幕 + 自动字幕） · 格式 最佳")).toBeInTheDocument();

    await user.click(screen.getByLabelText("选择 One"));
    await user.selectOptions(screen.getByLabelText("下载模式"), "subtitles_only");
    expect(screen.queryByRole("option", { name: "22 · 720p · mp4 · 10.0 MB" })).not.toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "137 · 1080p · 30fps · mp4 · 大小未知" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /已选 1 项：en/ }));
    const search = screen.getByLabelText("搜索字幕语言");
    await user.type(search, "zh");
    expect(screen.queryByLabelText("字幕 en")).not.toBeInTheDocument();
    await user.click(screen.getByLabelText("字幕 zh-Hans"));
    await user.clear(search);
    await user.type(search, "en");
    await user.click(screen.getByRole("button", { name: "加入下载队列" }));

    await waitFor(() => {
      expect(fetch).toHaveBeenCalledWith(
        "/api/jobs",
        expect.objectContaining({
          method: "POST",
          body: expect.stringContaining('"playlist_items":[2]')
        })
      );
    });

    expect(String((fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.at(-1)?.[1]?.body)).toContain(
      '"mode":"subtitles_only"'
    );
    const submittedBody = JSON.parse(
      String((fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.at(-1)?.[1]?.body)
    );
    expect(submittedBody.options.subtitle_languages).toEqual(expect.arrayContaining(["en", "zh-Hans"]));
    expect(submittedBody.options.subtitle_source).toBe("both");
  }, 10_000);

  test("omits redundant panel subtitle text", async () => {
    render(<App />);

    expect(await screen.findByRole("heading", { name: "解析链接" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "下载选项" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "设置" })).toBeInTheDocument();
    expect(screen.queryByText("支持单视频和 playlist")).not.toBeInTheDocument();
    expect(screen.queryByText("视频、字幕和批量策略")).not.toBeInTheDocument();
    expect(screen.queryByText("本机下载默认值")).not.toBeInTheDocument();
  });

  test("integrates cookies controls into the analyzer panel", async () => {
    const user = userEvent.setup();
    render(<App />);

    const analyzer = (await screen.findByRole("heading", { name: "解析链接" })).closest("form");
    expect(analyzer).toBeInTheDocument();
    expect(within(analyzer as HTMLElement).getByText("未上传 cookies")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Cookies" })).not.toBeInTheDocument();

    const file = new File(["cookie"], "cookies.txt", { type: "text/plain" });
    expect(within(analyzer as HTMLElement).getByText("选择")).toBeInTheDocument();
    expect(within(analyzer as HTMLElement).getByText("cookies")).toBeInTheDocument();
    expect(within(analyzer as HTMLElement).queryByText("选择 cookies.txt")).not.toBeInTheDocument();
    await user.upload(within(analyzer as HTMLElement).getByLabelText("选择 cookies"), file);

    await waitFor(() => {
      expect(fetch).toHaveBeenCalledWith(
        "/api/cookies",
        expect.objectContaining({ method: "POST", body: expect.any(FormData) })
      );
    });
    expect(await within(analyzer as HTMLElement).findByText("已启用 cookies")).toBeInTheDocument();

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
    browserCookieImportLocked = true;
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
    analyzeLockedByEdgeCookies = true;
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

  test("autosaves concurrency without a save settings button", async () => {
    const user = userEvent.setup();
    render(<App />);

    expect(await screen.findByRole("heading", { name: "设置" })).toBeInTheDocument();
    expect(screen.getByLabelText("下载目录")).toBeInTheDocument();
    const concurrency = screen.getByLabelText("并发（若追求稳定，可设为 1）");
    expect(concurrency).toBeInTheDocument();
    await waitFor(() => expect(concurrency).toHaveValue(2));
    expect(screen.queryByText("默认跟随 CPU core 数量，可按需覆盖。")).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/默认清晰度/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "保存设置" })).not.toBeInTheDocument();

    await user.clear(concurrency);
    await user.type(concurrency, "4");
    await user.tab();

    await waitFor(() => {
      expect(fetch).toHaveBeenCalledWith(
        "/api/settings",
        expect.objectContaining({
          method: "PUT",
          body: JSON.stringify({ default_concurrency: 4 })
        })
      );
    });
  });

  test("selects download directory with a folder dialog", async () => {
    const user = userEvent.setup();
    render(<App />);

    expect(await screen.findByDisplayValue("downloads")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "选择文件夹" }));

    await waitFor(() => {
      expect(fetch).toHaveBeenCalledWith(
        "/api/settings/download-dir/select",
        expect.objectContaining({ method: "POST" })
      );
    });
    expect(screen.getByDisplayValue("D:\\Videos")).toBeInTheDocument();
  });

  test("warns when high resolution downloads cannot be merged without ffmpeg", async () => {
    currentSettingsPayload = { ...settingsPayload, ffmpeg: { ffmpeg: false, ffprobe: false } };
    const user = userEvent.setup();
    render(<App />);

    await user.type(screen.getByLabelText("视频或 playlist 链接"), "https://youtube.com/playlist?list=abc");
    await user.click(screen.getByRole("button", { name: "解析链接" }));

    expect(
      await screen.findByText("高分辨率 YouTube 视频需要 ffmpeg 合并音视频；当前环境不可用时任务会失败。")
    ).toBeInTheDocument();
  });

  test("shows only resolution choices in the quality selector", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.type(screen.getByLabelText("视频或 playlist 链接"), "https://youtube.com/playlist?list=abc");
    await user.click(screen.getByRole("button", { name: "解析链接" }));

    await screen.findByText("Batch");
    const quality = screen.getByLabelText("清晰度") as HTMLSelectElement;

    expect(Array.from(quality.options).map((option) => option.value)).not.toContain("format:22");
    expect(screen.queryByText(/已选格式/)).not.toBeInTheDocument();
  });

  test("shows selected quality filesize beside the analyzed video title", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.type(screen.getByLabelText("视频或 playlist 链接"), "https://youtube.com/playlist?list=abc");
    await user.click(screen.getByRole("button", { name: "解析链接" }));

    await screen.findByText("Batch");
    expect(screen.getByText("当前选择：1440p · 大小未知")).toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText("清晰度"), "resolution:720p");
    expect(screen.getByText("当前选择：720p · 10.0 MB")).toBeInTheDocument();
  });

  test("defaults speed limit to unlimited and submits null", async () => {
    const user = userEvent.setup();
    render(<App />);

    const speedLimit = screen.getByLabelText("限速 KB/s（清空：不限速）");
    expect(screen.getByLabelText("重试次数")).toHaveValue(10);
    expect(speedLimit).toHaveValue(null);
    expect(screen.queryByText("清空表示不限速")).not.toBeInTheDocument();

    await user.type(screen.getByLabelText("视频或 playlist 链接"), "https://youtube.com/playlist?list=abc");
    await user.click(screen.getByRole("button", { name: "解析链接" }));
    await screen.findByText("Batch");
    await user.click(screen.getByRole("button", { name: "加入下载队列" }));

    await waitFor(() => {
      const createJobCall = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.find(
        ([url, init]) => String(url).endsWith("/api/jobs") && init?.method === "POST"
      );
      const submittedBody = JSON.parse(
        String(createJobCall?.[1]?.body)
      );
      expect(submittedBody.options.speed_limit_kbps).toBeNull();
    });
  });

  test("keeps the default 1440p selection so the backend can explain any fallback", async () => {
    currentAnalyzePayload = {
      ...analyzePayload,
      formats: [
        { format_id: "22", label: "720p mp4", height: 720, ext: "mp4", filesize: 10_485_760, fps: null },
        { format_id: "135", label: "480p mp4", height: 480, ext: "mp4", filesize: 5_242_880, fps: null }
      ]
    };
    const user = userEvent.setup();
    render(<App />);

    await user.type(screen.getByLabelText("视频或 playlist 链接"), "https://youtube.com/playlist?list=abc");
    await user.click(screen.getByRole("button", { name: "解析链接" }));

    await screen.findByText("Batch");
    expect(screen.getByLabelText("清晰度")).toHaveValue("resolution:1440p");
  });

  test("shows subtitle fallback information and submits the available source", async () => {
    currentAnalyzePayload = {
      ...analyzePayload,
      subtitles: [],
      automatic_subtitles: [{ language: "zh-Hans", name: null, formats: ["vtt"] }]
    };
    const user = userEvent.setup();
    render(<App />);

    await user.type(screen.getByLabelText("视频或 playlist 链接"), "https://youtube.com/playlist?list=abc");
    await user.click(screen.getByRole("button", { name: "解析链接" }));

    await screen.findByText("Batch");
    expect(screen.getByText("字幕：来源 自动字幕（人工字幕缺失，已 fallback） · 格式 最佳")).toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText("字幕来源"), "human");
    expect(screen.getByText("字幕：来源 自动字幕（人工字幕缺失，已 fallback） · 格式 最佳")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "加入下载队列" }));

    await waitFor(() => {
      const createJobCall = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.find(
        ([url, init]) => String(url).endsWith("/api/jobs") && init?.method === "POST"
      );
      const submittedBody = JSON.parse(String(createJobCall?.[1]?.body));
      expect(submittedBody.options.subtitle_source).toBe("auto");
    });
  });

  test("shows no subtitles when neither human nor automatic captions are available", async () => {
    currentAnalyzePayload = {
      ...analyzePayload,
      subtitles: [],
      automatic_subtitles: []
    };
    const user = userEvent.setup();
    render(<App />);

    await user.type(screen.getByLabelText("视频或 playlist 链接"), "https://youtube.com/playlist?list=abc");
    await user.click(screen.getByRole("button", { name: "解析链接" }));

    await screen.findByText("Batch");
    expect(screen.getByText("字幕：无字幕")).toBeInTheDocument();
  });

  test("submits selected resolution with null concrete format", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.type(screen.getByLabelText("视频或 playlist 链接"), "https://youtube.com/playlist?list=abc");
    await user.click(screen.getByRole("button", { name: "解析链接" }));

    await screen.findByText("Batch");
    await user.selectOptions(screen.getByLabelText("清晰度"), "resolution:720p");
    await user.click(screen.getByRole("button", { name: "加入下载队列" }));

    await waitFor(() => {
      const submittedBody = JSON.parse(
        String((fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.at(-1)?.[1]?.body)
      );
      expect(submittedBody.options.format_id).toBeNull();
      expect(submittedBody.options.resolution).toBe("720p");
    });
  });

  test("controls single and batch jobs from task center", async () => {
    const user = userEvent.setup();
    render(<App />);

    expect(await screen.findByText("Running video")).toBeInTheDocument();
    expect(screen.getAllByText("Paused video").length).toBeGreaterThan(0);

    await user.click(screen.getByRole("button", { name: "暂停 Running video" }));
    await user.click(screen.getByRole("button", { name: "重启 Paused video" }));

    await user.click(screen.getByLabelText("选择任务 Running video"));
    await user.click(screen.getByLabelText("选择任务 Paused video"));
    await user.click(screen.getByRole("button", { name: "批量暂停" }));
    await user.click(screen.getByRole("button", { name: "删除任务和已下载文件 Running video" }));

    expect(fetch).toHaveBeenCalledWith("/api/jobs/job-running/pause", expect.objectContaining({ method: "POST" }));
    expect(fetch).toHaveBeenCalledWith("/api/jobs/job-paused/restart", expect.objectContaining({ method: "POST" }));
    expect(fetch).toHaveBeenCalledWith("/api/jobs/job-running?delete_files=true", expect.objectContaining({ method: "DELETE" }));
    expect(fetch).toHaveBeenCalledWith(
      "/api/jobs/batch",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ action: "pause", job_ids: ["job-running", "job-paused"], delete_files: false })
      })
    );
  });

  test("plays downloaded single videos and playlist items from task center", async () => {
    currentJobsPayload = [
      {
        ...jobPayload,
        items: [{ ...jobPayload.items[0], output_path: "D:\\Videos\\running.mp4" }]
      },
      {
        ...playlistJobPayload,
        items: [
          { ...playlistJobPayload.items[0], output_path: "D:\\Videos\\Playlist\\one.mp4" },
          playlistJobPayload.items[1]
        ]
      }
    ];
    const user = userEvent.setup();
    render(<App />);

    expect(await screen.findByText("Running video")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "播放 Running video" }));
    await user.click(screen.getByRole("button", { name: "播放 Part one" }));

    expect(fetch).toHaveBeenCalledWith("/api/jobs/job-running/play", expect.objectContaining({ method: "POST" }));
    expect(fetch).toHaveBeenCalledWith(
      "/api/jobs/job-playlist/items/item-playlist-1/play",
      expect.objectContaining({ method: "POST" })
    );
  });

  test("disables play buttons before downloaded files are known", async () => {
    render(<App />);

    expect(await screen.findByText("Running video")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "播放 Running video" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "播放 Part one" })).toBeDisabled();
  });

  test("opens downloaded single video and playlist item folders from task center", async () => {
    currentJobsPayload = [
      {
        ...jobPayload,
        items: [{ ...jobPayload.items[0], output_path: "D:\\Videos\\running.mp4" }]
      },
      {
        ...playlistJobPayload,
        items: [
          { ...playlistJobPayload.items[0], output_path: "D:\\Videos\\Playlist\\one.mp4" },
          playlistJobPayload.items[1]
        ]
      }
    ];
    const user = userEvent.setup();
    render(<App />);

    expect(await screen.findByText("Running video")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "打开视频文件夹 Running video" }));
    await user.click(screen.getByRole("button", { name: "打开视频文件夹 Part one" }));

    expect(fetch).toHaveBeenCalledWith("/api/jobs/job-running/open-folder", expect.objectContaining({ method: "POST" }));
    expect(fetch).toHaveBeenCalledWith(
      "/api/jobs/job-playlist/items/item-playlist-1/open-folder",
      expect.objectContaining({ method: "POST" })
    );
  });

  test("opens a task folder even before a final output path is known", async () => {
    currentJobsPayload = [
      {
        ...jobPayload,
        download_dir: "D:\\Videos",
        items: [{ ...jobPayload.items[0], output_path: null }]
      }
    ];
    const user = userEvent.setup();
    render(<App />);

    expect(await screen.findByText("Running video")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "打开视频文件夹 Running video" }));

    expect(fetch).toHaveBeenCalledWith("/api/jobs/job-running/open-folder", expect.objectContaining({ method: "POST" }));
  });

  test("shows local file action failures beside the affected task", async () => {
    currentJobsPayload = [
      {
        ...jobPayload,
        items: [{ ...jobPayload.items[0], output_path: "D:\\Videos\\missing.mp4" }]
      }
    ];
    localFileActionFailure = "视频文件不存在。";
    const user = userEvent.setup();
    render(<App />);

    expect(await screen.findByText("Running video")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "播放 Running video" }));

    const jobCard = screen.getByText("Running video").closest(".job-card");
    expect(jobCard).toBeInTheDocument();
    const localAlert = within(jobCard as HTMLElement).getByRole("alert");
    expect(localAlert).toHaveClass("local-action-error");
    expect(localAlert).toHaveTextContent("视频文件不存在。");
  });

  test("shows playlist item local file action failures beside the affected item", async () => {
    currentJobsPayload = [
      {
        ...playlistJobPayload,
        items: [
          { ...playlistJobPayload.items[0], output_path: "D:\\Videos\\Playlist\\missing.mp4" },
          playlistJobPayload.items[1]
        ]
      }
    ];
    localFileActionFailure = "视频文件不存在。";
    const user = userEvent.setup();
    render(<App />);

    expect(await screen.findByText("Playlist batch")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "打开视频文件夹 Part one" }));

    const itemDetail = screen.getByText("1. Part one · running").closest(".job-item-detail");
    expect(itemDetail).toBeInTheDocument();
    const localAlert = within(itemDetail as HTMLElement).getByRole("alert");
    expect(localAlert).toHaveClass("local-action-error");
    expect(localAlert).toHaveTextContent("视频文件不存在。");
  });

  test("opens playlist folders from task center", async () => {
    currentJobsPayload = [
      {
        ...playlistJobPayload,
        download_dir: "D:\\Videos\\Playlist batch"
      }
    ];
    const user = userEvent.setup();
    render(<App />);

    expect(await screen.findByText("Playlist batch")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "打开合集文件夹 Playlist batch" }));

    expect(fetch).toHaveBeenCalledWith("/api/jobs/job-playlist/open-folder", expect.objectContaining({ method: "POST" }));
  });

  test("copies single video playlist and playlist item source links", async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText }
    });
    render(<App />);

    expect(await screen.findByText("Running video")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "复制链接 Running video" }));
    expect(writeText).toHaveBeenCalledWith("https://youtu.be/running");
    expect(screen.getByRole("button", { name: "已复制 Running video" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "复制链接 Playlist batch" }));
    expect(writeText).toHaveBeenCalledWith("https://youtube.com/playlist?list=abc");

    await user.click(screen.getByRole("button", { name: "复制链接 Part one" }));
    expect(writeText).toHaveBeenCalledWith("https://youtu.be/one");
  });

  test("opens YouTube pages for single video playlist and playlist items", async () => {
    const user = userEvent.setup();
    const openSpy = vi.spyOn(window, "open").mockImplementation(() => null);
    render(<App />);

    expect(await screen.findByText("Running video")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "打开 YouTube 页面 Running video" }));
    expect(openSpy).toHaveBeenCalledWith("https://youtu.be/running", "_blank", "noopener,noreferrer");

    await user.click(screen.getByRole("button", { name: "打开 YouTube 页面 Playlist batch" }));
    expect(openSpy).toHaveBeenCalledWith("https://youtube.com/playlist?list=abc", "_blank", "noopener,noreferrer");

    await user.click(screen.getByRole("button", { name: "打开 YouTube 页面 Part one" }));
    expect(openSpy).toHaveBeenCalledWith("https://youtu.be/one", "_blank", "noopener,noreferrer");
  });

  test("passes delete files option to batch delete", async () => {
    const user = userEvent.setup();
    render(<App />);

    expect(await screen.findByText("Running video")).toBeInTheDocument();
    await user.click(screen.getByLabelText("选择任务 Running video"));
    await user.click(screen.getByLabelText("选择任务 Paused video"));
    await user.click(screen.getByRole("button", { name: "批量删除任务和已下载文件" }));

    expect(fetch).toHaveBeenCalledWith(
      "/api/jobs/batch",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ action: "delete", job_ids: ["job-running", "job-paused"], delete_files: true })
      })
    );
  });

  test("uses distinct icons for task-only and task-plus-files deletion", async () => {
    render(<App />);

    expect(await screen.findByText("Running video")).toBeInTheDocument();
    const taskOnlyButton = screen.getByRole("button", { name: "仅删除任务 Running video" });
    const withFilesButton = screen.getByRole("button", { name: "删除任务和已下载文件 Running video" });

    const taskOnlyIcon = taskOnlyButton.querySelector("svg");
    const withFilesIcon = withFilesButton.querySelector("svg");
    expect(taskOnlyIcon).toBeInTheDocument();
    expect(withFilesIcon).toBeInTheDocument();
    expect(taskOnlyIcon?.outerHTML).not.toEqual(withFilesIcon?.outerHTML);
  });

  test("does not show the legacy global delete-files checkbox", async () => {
    render(<App />);

    expect(await screen.findByText("Running video")).toBeInTheDocument();
    expect(screen.queryByLabelText("删除任务时同时删除已下载视频")).not.toBeInTheDocument();
  });

  test("does not delete files when confirmation is cancelled", async () => {
    vi.mocked(window.confirm).mockReturnValueOnce(false);
    const user = userEvent.setup();
    render(<App />);

    expect(await screen.findByText("Running video")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "删除任务和已下载文件 Running video" }));

    expect(window.confirm).toHaveBeenCalled();
    expect(fetch).not.toHaveBeenCalledWith(
      "/api/jobs/job-running?delete_files=true",
      expect.objectContaining({ method: "DELETE" })
    );
  });

  test("shows live progress percentage elapsed time and eta in task center", async () => {
    render(<App />);

    expect((await screen.findAllByText("34.0%")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("已用 00:42").length).toBeGreaterThan(0);
    expect(screen.getAllByText("剩余 00:10").length).toBeGreaterThan(0);
    expect(screen.getAllByText("2.0 KB/s").length).toBeGreaterThan(0);
  });

  test("shows task start time end time and actual resolution in task center", async () => {
    render(<App />);

    expect(await screen.findByText("Running video")).toBeInTheDocument();
    expect(screen.getAllByText(/2026-05-15 10:00:00/).length).toBeGreaterThan(0);
    expect(screen.getByText(/2026-05-15 10:05:00/)).toBeInTheDocument();
    expect(screen.getAllByText(/1920x1080/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/1280x720/).length).toBeGreaterThan(0);
    expect(screen.getByText(/混合分辨率/)).toBeInTheDocument();
    expect(screen.getAllByText(/格式 mp4 · avc1 \+ mp4a/).length).toBeGreaterThan(0);
    expect(screen.getByText(/格式 混合格式/)).toBeInTheDocument();
  });

  test("does not show optional ffprobe status in the topbar", async () => {
    currentSettingsPayload = { ...settingsPayload, ffmpeg: { ffmpeg: true, ffprobe: false } };

    render(<App />);

    expect(await screen.findByText("ffmpeg")).toBeInTheDocument();
    expect(screen.queryByText("ffprobe")).not.toBeInTheDocument();
  });

  test("places task count on the task center title row", async () => {
    render(<App />);

    const heading = await screen.findByRole("heading", { name: "任务中心" });
    const titleRow = heading.closest(".job-title-row");
    expect(titleRow).toBeInTheDocument();
    expect(titleRow?.querySelector(".job-count-badge")).toHaveTextContent("3 个任务");
  });

  test("does not repeat single video item details in task center", async () => {
    render(<App />);

    expect(await screen.findByText("Running video")).toBeInTheDocument();
    expect(screen.queryByText("1. Running video · running")).not.toBeInTheDocument();
    expect(screen.getByText("1. Part one · running")).toBeInTheDocument();
  });

  test("expands and collapses playlist jobs in task center", async () => {
    const user = userEvent.setup();
    render(<App />);

    expect(await screen.findByText("Playlist batch")).toBeInTheDocument();
    const collapseButton = screen.getByRole("button", { name: "折叠 Playlist batch" });
    expect(collapseButton).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("1. Part one · running")).toBeInTheDocument();

    await user.click(collapseButton);
    expect(screen.queryByText("1. Part one · running")).not.toBeInTheDocument();
    const expandButton = screen.getByRole("button", { name: "展开 Playlist batch" });
    expect(expandButton).toHaveAttribute("aria-expanded", "false");

    await user.click(expandButton);
    expect(screen.getByText("1. Part one · running")).toBeInTheDocument();
  });

  test("shows playlist item size progress timing and speed details", async () => {
    render(<App />);

    expect(await screen.findByText("Playlist batch")).toBeInTheDocument();
    expect(screen.getByText("1. Part one · running")).toBeInTheDocument();
    expect(screen.getByText("50.0%")).toBeInTheDocument();
    expect(screen.getByText("大小 已知 10.0 MB")).toBeInTheDocument();
    expect(screen.getByText("大小 10.0 MB")).toBeInTheDocument();
    expect(screen.getByText("已下载 5.0 MB")).toBeInTheDocument();
    expect(screen.getAllByText("已用 00:42").length).toBeGreaterThan(0);
    expect(screen.getByText("剩余 00:20")).toBeInTheDocument();
    expect(screen.getAllByText("2.0 KB/s").length).toBeGreaterThan(0);
    expect(screen.getByText("大小 未知")).toBeInTheDocument();
    expect(screen.getByText("已下载 未知")).toBeInTheDocument();
    expect(screen.getAllByText("分辨率 1920x1080").length).toBeGreaterThan(0);
    expect(screen.getAllByText("格式 mp4 · avc1 + mp4a").length).toBeGreaterThan(0);
  });

  test("keeps average speed visible after a job completes", async () => {
    currentJobsPayload = [
      {
        ...jobPayload,
        status: "succeeded",
        progress: 100,
        speed: 1024,
        eta: null,
        completed_items: 1,
        finished_at: "2026-05-15T10:01:00Z",
        items: []
      }
    ];
    render(<App />);

    expect(await screen.findByText("Running video")).toBeInTheDocument();
    expect(screen.getByText("1.0 KB/s")).toBeInTheDocument();
  });

  test("shows concrete single video failure reason in task center", async () => {
    currentJobsPayload = [
      {
        ...jobPayload,
        status: "failed",
        progress: 40,
        error: "YouTube 媒体流连接中断，请重新导入 cookies 后重试。",
        failed_items: 1,
        speed: 1536,
        eta: null,
        items: []
      }
    ];
    render(<App />);

    expect(await screen.findByText("Running video")).toBeInTheDocument();
    expect(screen.getByText(/YouTube 媒体流连接中断/)).toBeInTheDocument();
    expect(screen.getByText("1.5 KB/s")).toBeInTheDocument();
  });

  test("restarts a single playlist item from task center", async () => {
    const user = userEvent.setup();
    render(<App />);

    expect(await screen.findByText("Playlist batch")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "重启 Part two" }));

    expect(fetch).toHaveBeenCalledWith(
      "/api/jobs/job-playlist/items/item-playlist-2/restart",
      expect.objectContaining({ method: "POST" })
    );
  });

  test("deletes a single playlist item from task center", async () => {
    const user = userEvent.setup();
    render(<App />);

    expect(await screen.findByText("Playlist batch")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "仅删除视频任务 Part two" }));

    expect(fetch).toHaveBeenCalledWith(
      "/api/jobs/job-playlist/items/delete",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ item_ids: ["item-playlist-2"], delete_files: false })
      })
    );
  });

  test("confirms before deleting playlist item files", async () => {
    const user = userEvent.setup();
    render(<App />);

    expect(await screen.findByText("Playlist batch")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "删除视频任务和已下载文件 Part two" }));

    expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining("将删除所选视频任务记录"));
    expect(fetch).toHaveBeenCalledWith(
      "/api/jobs/job-playlist/items/delete",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ item_ids: ["item-playlist-2"], delete_files: true })
      })
    );
  });

  test("deletes selected playlist items from task center", async () => {
    const user = userEvent.setup();
    render(<App />);

    expect(await screen.findByText("Playlist batch")).toBeInTheDocument();
    await user.click(screen.getByLabelText("选择视频任务 Part one"));
    await user.click(screen.getByLabelText("选择视频任务 Part two"));
    await user.click(screen.getByRole("button", { name: "删除已选任务和已下载文件" }));

    expect(fetch).toHaveBeenCalledWith(
      "/api/jobs/job-playlist/items/delete",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ item_ids: ["item-playlist-1", "item-playlist-2"], delete_files: true })
      })
    );
  });

  test("shows single video resolution fallback and restarts with suggested resolution", async () => {
    currentJobsPayload = [singleFallbackJobPayload];
    const user = userEvent.setup();
    render(<App />);

    expect(await screen.findByText("Unsupported resolution")).toBeInTheDocument();
    expect(screen.getByText(resolutionFallback.message)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "以 720p 重启任务 Unsupported resolution" }));

    expect(fetch).toHaveBeenCalledWith(
      "/api/jobs/job-format-failed/restart",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ resolution: "720p" })
      })
    );
  });

  test("shows playlist item resolution fallback and restarts item with suggested resolution", async () => {
    currentJobsPayload = [playlistFallbackJobPayload];
    const user = userEvent.setup();
    render(<App />);

    expect(await screen.findByText("Playlist batch")).toBeInTheDocument();
    expect(screen.getByText(resolutionFallback.message)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "以 720p 重启 Part two" }));

    expect(fetch).toHaveBeenCalledWith(
      "/api/jobs/job-playlist/items/item-playlist-2/restart",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ resolution: "720p" })
      })
    );
  });

  test("shows automatic fallback without restart action for succeeded playlist item", async () => {
    currentJobsPayload = [
      {
        ...playlistJobPayload,
        items: [
          {
            ...playlistJobPayload.items[0],
            requested_resolution: "1080p",
            fallback_resolution: "720p",
            fallback_reason: "requested_resolution_missing",
            resolution_fallback: automaticResolutionFallback
          },
          playlistJobPayload.items[1]
        ]
      }
    ];
    render(<App />);

    expect(await screen.findByText("Playlist batch")).toBeInTheDocument();
    expect(screen.getByText(automaticResolutionFallback.message)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "以 720p 重启 Part one" })).not.toBeInTheDocument();
  });

  test("shows original resolution retry for succeeded unselectable fallback", async () => {
    currentJobsPayload = [
      {
        ...playlistJobPayload,
        items: [
          {
            ...playlistJobPayload.items[0],
            requested_resolution: "1080p",
            fallback_resolution: "720p",
            fallback_reason: "requested_resolution_unselectable",
            resolution_fallback: unselectableResolutionFallback
          },
          playlistJobPayload.items[1]
        ]
      }
    ];
    const user = userEvent.setup();
    render(<App />);

    expect(await screen.findByText("Playlist batch")).toBeInTheDocument();
    expect(screen.getByText(unselectableResolutionFallback.message)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "以 1080p 重试 Part one" }));

    expect(fetch).toHaveBeenCalledWith(
      "/api/jobs/job-playlist/items/item-playlist-1/restart",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ resolution: "1080p" })
      })
    );
  });
});
