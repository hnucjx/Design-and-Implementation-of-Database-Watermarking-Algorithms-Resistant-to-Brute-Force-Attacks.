import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, test, vi } from "vitest";
import App from "./App";
import { analyzePayload, settingsPayload } from "./test/appFixtures";
import { installAppHarness, mockState } from "./test/appHarness";

installAppHarness();

describe("App · 解析链接与下载选项", () => {
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

  // 触发器标签是**数据驱动**的（「已选 N 项：en, zh-Hans, …」），选中几种就逐个列出几种。
  // 这一条盯住「不许改成 JS 侧截断」：标签太长时，正确的解法是让它换行（见 styles.test.ts 里的
  // CSS 不变式），而不是少显示几个 —— 后者会把刚修好的缺陷换个形式放回来（ai/ui/003）。
  test("lists every selected subtitle language in the trigger label", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.type(screen.getByLabelText("视频或 playlist 链接"), "https://youtube.com/watch?v=abc");
    await user.click(screen.getByRole("button", { name: "解析链接" }));

    // 「en」来自 settings 的 default_subtitle_languages，解析后出现在标签里。
    await user.click(await screen.findByRole("button", { name: /已选 1 项：en/ }));
    await user.click(screen.getByLabelText("字幕 zh-Hans"));

    expect(screen.getByRole("button", { name: "已选 2 项：en, zh-Hans" })).toBeInTheDocument();
  });

  test("warns when high resolution downloads cannot be merged without ffmpeg", async () => {
    mockState.settings = { ...settingsPayload, ffmpeg: { ffmpeg: false, ffprobe: false } };
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

  test("saves speed limit and retries as runtime settings before creating jobs", async () => {
    const user = userEvent.setup();
    render(<App />);

    const speedLimit = screen.getByLabelText("限速 KB/s（清空：不限速）");
    const retries = screen.getByLabelText("重试次数");
    await user.type(speedLimit, "512");
    await user.clear(retries);
    await user.type(retries, "6");

    await waitFor(() => {
      const settingsCalls = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.filter(
        ([url, init]) => String(url).endsWith("/api/settings") && init?.method === "PUT"
      );
      expect(settingsCalls.some(([, init]) => JSON.parse(String(init?.body)).default_speed_limit_kbps === 512)).toBe(true);
      expect(settingsCalls.some(([, init]) => JSON.parse(String(init?.body)).default_retries === 6)).toBe(true);
    });

    await user.type(screen.getByLabelText("视频或 playlist 链接"), "https://youtube.com/playlist?list=abc");
    await user.click(screen.getByRole("button", { name: "解析链接" }));
    await screen.findByText("Batch");
    await user.click(screen.getByRole("button", { name: "加入下载队列" }));

    await waitFor(() => {
      const createJobCall = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.find(
        ([url, init]) => String(url).endsWith("/api/jobs") && init?.method === "POST"
      );
      const submittedBody = JSON.parse(String(createJobCall?.[1]?.body));
      expect(submittedBody.options.speed_limit_kbps).toBe(512);
      expect(submittedBody.options.retries).toBe(6);
    });
  });

  test("keeps the default 1440p selection so the backend can explain any fallback", async () => {
    mockState.analyze = {
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
    mockState.analyze = {
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
    mockState.analyze = {
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
});
