import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import App from "./App";
import type { Job } from "./types";

const analyzePayload = {
  url: "https://youtube.com/playlist?list=abc",
  title: "Batch",
  is_playlist: true,
  duration: null,
  thumbnail: null,
  entries: [
    { index: 1, id: "one", title: "One", url: "https://youtu.be/one", duration: 60, thumbnail: null },
    { index: 2, id: "two", title: "Two", url: "https://youtu.be/two", duration: 90, thumbnail: null }
  ],
  formats: [
    { format_id: "22", label: "720p mp4", height: 720, ext: "mp4", filesize: 10_485_760, fps: null },
    { format_id: "137", label: "1080p mp4", height: 1080, ext: "mp4", filesize: null, fps: 30 }
  ],
  subtitles: [{ language: "en", name: null, formats: ["vtt"] }],
  automatic_subtitles: [{ language: "zh-Hans", name: null, formats: ["vtt"] }],
  ffmpeg: { ffmpeg: true, ffprobe: true }
};

let currentAnalyzePayload = analyzePayload;

const jobPayload: Job = {
  id: "job-running",
  url: "https://youtu.be/running",
  title: "Running video",
  status: "running",
  progress: 34,
  created_at: "2026-05-15T10:00:00Z",
  updated_at: "2026-05-15T10:00:42Z",
  started_at: "2026-05-15T10:00:00Z",
  finished_at: null,
  elapsed_seconds: 42,
  actual_resolution: "1920x1080",
  resolution_fallback: null,
  speed: 2048,
  eta: 10,
  total_items: 1,
  completed_items: 0,
  failed_items: 0,
  current_item_title: "Running video",
  error: null,
  download_dir: null,
  items: [
    {
      id: "item-running",
      job_id: "job-running",
      source_url: "https://youtu.be/running",
      title: "Running video",
      index: 1,
      status: "running",
      progress: 34,
      created_at: "2026-05-15T10:00:00Z",
      updated_at: "2026-05-15T10:00:42Z",
      started_at: "2026-05-15T10:00:00Z",
      finished_at: null,
      elapsed_seconds: 42,
      actual_width: 1920,
      actual_height: 1080,
      requested_resolution: null,
      fallback_resolution: null,
      resolution_fallback: null,
      downloaded_bytes: 34,
      total_bytes: 100,
      speed: 2048,
      eta: 10,
      output_path: null,
      error: null
    }
  ]
};

const pausedJobPayload: Job = {
  ...jobPayload,
  id: "job-paused",
  title: "Paused video",
  status: "paused",
  progress: 34,
  finished_at: "2026-05-15T10:05:00Z",
  actual_resolution: "1280x720",
  items: [{ ...jobPayload.items[0], id: "item-paused", job_id: "job-paused", title: "Paused video", status: "paused" }]
};

const playlistJobPayload: Job = {
  ...jobPayload,
  id: "job-playlist",
  title: "Playlist batch",
  actual_resolution: "混合分辨率",
  total_items: 2,
  completed_items: 0,
  failed_items: 0,
  items: [
    {
      ...jobPayload.items[0],
      id: "item-playlist-1",
      job_id: "job-playlist",
      title: "Part one",
      index: 1,
      progress: 50,
      downloaded_bytes: 5_242_880,
      total_bytes: 10_485_760,
      speed: 2048,
      eta: 20,
      elapsed_seconds: 42,
      actual_width: 1920,
      actual_height: 1080
    },
    {
      ...jobPayload.items[0],
      id: "item-playlist-2",
      job_id: "job-playlist",
      title: "Part two",
      index: 2,
      progress: 0,
      status: "queued",
      downloaded_bytes: null,
      total_bytes: null,
      speed: null,
      eta: null,
      elapsed_seconds: 0,
      actual_width: null,
      actual_height: null
    }
  ]
};

const resolutionFallback = {
  requested_resolution: "1080p",
  fallback_resolution: "720p",
  message: "当前没有 1080p 的视频，低于选定分辨率的最高可用分辨率是 720p。"
};

const singleFallbackJobPayload: Job = {
  ...jobPayload,
  id: "job-format-failed",
  title: "Unsupported resolution",
  status: "failed",
  progress: 0,
  error: "1 item(s) failed.",
  resolution_fallback: resolutionFallback,
  items: [
    {
      ...jobPayload.items[0],
      id: "item-format-failed",
      job_id: "job-format-failed",
      title: "Unsupported resolution",
      status: "failed",
      progress: 0,
      error: resolutionFallback.message,
      requested_resolution: "1080p",
      fallback_resolution: "720p",
      resolution_fallback: resolutionFallback
    }
  ]
};

const playlistFallbackJobPayload: Job = {
  ...playlistJobPayload,
  status: "failed",
  failed_items: 1,
  items: [
    playlistJobPayload.items[0],
    {
      ...playlistJobPayload.items[1],
      status: "failed",
      error: resolutionFallback.message,
      requested_resolution: "1080p",
      fallback_resolution: "720p",
      resolution_fallback: resolutionFallback
    }
  ]
};

const settingsPayload = {
  download_dir: "downloads",
  default_concurrency: 2,
  default_subtitle_languages: ["en"],
  default_resolution: "1080p",
  cookies_enabled: false,
  ffmpeg: { ffmpeg: true, ffprobe: true }
};

let currentJobsPayload: Job[] = [jobPayload, pausedJobPayload, playlistJobPayload];
let currentSettingsPayload = settingsPayload;

describe("App", () => {
  beforeEach(() => {
    currentAnalyzePayload = analyzePayload;
    currentJobsPayload = [jobPayload, pausedJobPayload, playlistJobPayload];
    currentSettingsPayload = settingsPayload;
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
    expect(screen.getByLabelText("清晰度 / 格式")).toHaveValue("resolution:1080p");

    await user.click(screen.getByLabelText("选择 One"));
    await user.selectOptions(screen.getByLabelText("下载模式"), "subtitles_only");
    expect(screen.getByRole("option", { name: "22 · 720p · mp4 · 10.0 MB" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "137 · 1080p · 30fps · mp4 · 大小未知" })).toBeInTheDocument();
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
    await user.upload(within(analyzer as HTMLElement).getByLabelText("选择 cookies.txt"), file);

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
    await user.selectOptions(within(analyzer).getByLabelText("浏览器 cookies 来源"), "edge");
    await user.click(within(analyzer).getByRole("button", { name: "从浏览器导入" }));

    await waitFor(() => {
      expect(fetch).toHaveBeenCalledWith(
        "/api/cookies/from-browser",
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({ browser: "edge" })
        })
      );
    });
    expect(await within(analyzer).findByText("已启用 cookies")).toBeInTheDocument();
  });

  test("autosaves concurrency without a save settings button", async () => {
    const user = userEvent.setup();
    render(<App />);

    expect(await screen.findByRole("heading", { name: "设置" })).toBeInTheDocument();
    expect(screen.getByLabelText("下载目录")).toBeInTheDocument();
    const concurrency = screen.getByLabelText("并发 (默认跟随 CPU Core 数量，可按需调整。)");
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

  test("shows selected format resolution and filesize details", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.type(screen.getByLabelText("视频或 playlist 链接"), "https://youtube.com/playlist?list=abc");
    await user.click(screen.getByRole("button", { name: "解析链接" }));

    await screen.findByText("Batch");
    await user.selectOptions(screen.getByLabelText("清晰度 / 格式"), "format:22");

    expect(screen.getByText("已选格式：22 · 720p · mp4 · 10.0 MB")).toBeInTheDocument();
  });

  test("shows selected quality filesize beside the analyzed video title", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.type(screen.getByLabelText("视频或 playlist 链接"), "https://youtube.com/playlist?list=abc");
    await user.click(screen.getByRole("button", { name: "解析链接" }));

    await screen.findByText("Batch");
    expect(screen.getByText("当前选择：1080p · 大小未知")).toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText("清晰度 / 格式"), "resolution:720p");
    expect(screen.getByText("当前选择：720p · 10.0 MB")).toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText("清晰度 / 格式"), "format:22");
    expect(screen.getByText("当前选择：22 · 720p · 10.0 MB")).toBeInTheDocument();
  });

  test("defaults speed limit to 2048 and submits null when cleared", async () => {
    const user = userEvent.setup();
    render(<App />);

    const speedLimit = screen.getByLabelText("限速 KB/s");
    expect(speedLimit).toHaveValue(2048);
    expect(screen.getByText("清空表示不限速")).toBeInTheDocument();

    await user.clear(speedLimit);
    await user.type(screen.getByLabelText("视频或 playlist 链接"), "https://youtube.com/playlist?list=abc");
    await user.click(screen.getByRole("button", { name: "解析链接" }));
    await screen.findByText("Batch");
    await user.click(screen.getByRole("button", { name: "加入下载队列" }));

    await waitFor(() => {
      const submittedBody = JSON.parse(
        String((fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.at(-1)?.[1]?.body)
      );
      expect(submittedBody.options.speed_limit_kbps).toBeNull();
    });
  });

  test("falls back to highest available resolution when 1080p is unsupported", async () => {
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
    expect(screen.getByLabelText("清晰度 / 格式")).toHaveValue("resolution:720p");
  });

  test("submits a concrete format selected from the unified quality selector", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.type(screen.getByLabelText("视频或 playlist 链接"), "https://youtube.com/playlist?list=abc");
    await user.click(screen.getByRole("button", { name: "解析链接" }));

    await screen.findByText("Batch");
    await user.selectOptions(screen.getByLabelText("清晰度 / 格式"), "format:22");
    await user.click(screen.getByRole("button", { name: "加入下载队列" }));

    await waitFor(() => {
      const submittedBody = JSON.parse(
        String((fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.at(-1)?.[1]?.body)
      );
      expect(submittedBody.options.format_id).toBe("22");
      expect(submittedBody.options.resolution).toBe("1080p");
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
    await user.click(screen.getByLabelText("删除任务时同时删除已下载视频"));
    await user.click(screen.getByRole("button", { name: "批量暂停" }));
    await user.click(screen.getByRole("button", { name: "删除 Running video" }));

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

  test("passes delete files option to batch delete", async () => {
    const user = userEvent.setup();
    render(<App />);

    expect(await screen.findByText("Running video")).toBeInTheDocument();
    await user.click(screen.getByLabelText("选择任务 Running video"));
    await user.click(screen.getByLabelText("选择任务 Paused video"));
    await user.click(screen.getByLabelText("删除任务时同时删除已下载视频"));
    await user.click(screen.getByRole("button", { name: "批量删除" }));

    expect(fetch).toHaveBeenCalledWith(
      "/api/jobs/batch",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ action: "delete", job_ids: ["job-running", "job-paused"], delete_files: true })
      })
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
    expect(screen.getByText(/1920x1080/)).toBeInTheDocument();
    expect(screen.getByText(/1280x720/)).toBeInTheDocument();
    expect(screen.getByText(/混合分辨率/)).toBeInTheDocument();
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
    expect(screen.getByText("5.0 MB / 10.0 MB")).toBeInTheDocument();
    expect(screen.getAllByText("已用 00:42").length).toBeGreaterThan(0);
    expect(screen.getByText("剩余 00:20")).toBeInTheDocument();
    expect(screen.getAllByText("2.0 KB/s").length).toBeGreaterThan(0);
    expect(screen.getByText("大小未知 / 大小未知")).toBeInTheDocument();
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
});
