import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import App from "./App";

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

const jobPayload = {
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
  speed: 2048,
  eta: 10,
  total_items: 1,
  completed_items: 0,
  failed_items: 0,
  current_item_title: "Running video",
  error: null,
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
      downloaded_bytes: 34,
      total_bytes: 100,
      speed: 2048,
      eta: 10,
      output_path: null,
      error: null
    }
  ]
};

const pausedJobPayload = {
  ...jobPayload,
  id: "job-paused",
  title: "Paused video",
  status: "paused",
  progress: 34,
  items: [{ ...jobPayload.items[0], id: "item-paused", job_id: "job-paused", title: "Paused video", status: "paused" }]
};

const playlistJobPayload = {
  ...jobPayload,
  id: "job-playlist",
  title: "Playlist batch",
  total_items: 2,
  completed_items: 0,
  failed_items: 0,
  items: [
    { ...jobPayload.items[0], id: "item-playlist-1", job_id: "job-playlist", title: "Part one", index: 1 },
    { ...jobPayload.items[0], id: "item-playlist-2", job_id: "job-playlist", title: "Part two", index: 2, progress: 0, status: "queued" }
  ]
};

describe("App", () => {
  beforeEach(() => {
    currentAnalyzePayload = analyzePayload;
    vi.stubGlobal("EventSource", class {
      onmessage: ((event: MessageEvent) => void) | null = null;
      close = vi.fn();
      constructor(public url: string) {}
    });

    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/api/settings") && (!init || init.method === "GET")) {
          return Response.json({
            download_dir: "downloads",
            default_concurrency: 2,
            default_subtitle_languages: ["en"],
            default_resolution: "1080p",
            cookies_enabled: false,
            ffmpeg: { ffmpeg: true, ffprobe: true }
          });
        }
        if (url.endsWith("/api/jobs")) {
          if (init?.method === "POST") {
            return Response.json({ id: "job-1", status: "queued", total_items: 1, items: [] }, { status: 201 });
          }
          return Response.json([jobPayload, pausedJobPayload, playlistJobPayload]);
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
        if (url.endsWith("/api/jobs/job-running") && init?.method === "DELETE") {
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
    await user.click(screen.getByRole("button", { name: /选择字幕语言/ }));
    const search = screen.getByLabelText("搜索字幕语言");
    await user.type(search, "zh");
    expect(screen.queryByLabelText("字幕 en")).not.toBeInTheDocument();
    await user.click(screen.getByLabelText("字幕 zh-Hans"));
    await user.clear(search);
    await user.type(search, "en");
    await user.click(screen.getByLabelText("字幕 en"));
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
    await user.click(screen.getByRole("button", { name: "批量暂停" }));
    await user.click(screen.getByRole("button", { name: "删除 Running video" }));

    expect(fetch).toHaveBeenCalledWith("/api/jobs/job-running/pause", expect.objectContaining({ method: "POST" }));
    expect(fetch).toHaveBeenCalledWith("/api/jobs/job-paused/restart", expect.objectContaining({ method: "POST" }));
    expect(fetch).toHaveBeenCalledWith("/api/jobs/job-running", expect.objectContaining({ method: "DELETE" }));
    expect(fetch).toHaveBeenCalledWith(
      "/api/jobs/batch",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ action: "pause", job_ids: ["job-running", "job-paused"] })
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
});
