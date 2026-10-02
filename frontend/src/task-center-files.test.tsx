import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, test, vi } from "vitest";
import App from "./App";
import { jobPayload, playlistJobPayload } from "./test/appFixtures";
import { installAppHarness, mockState } from "./test/appHarness";

installAppHarness();

describe("App · 任务中心：本地文件操作与链接", () => {
  test("plays downloaded single videos and playlist items from task center", async () => {
    mockState.jobs = [
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
    mockState.jobs = [
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
    mockState.jobs = [
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
    mockState.jobs = [
      {
        ...jobPayload,
        items: [{ ...jobPayload.items[0], output_path: "D:\\Videos\\missing.mp4" }]
      }
    ];
    mockState.localFileActionFailure = "视频文件不存在。";
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
    mockState.jobs = [
      {
        ...playlistJobPayload,
        items: [
          { ...playlistJobPayload.items[0], output_path: "D:\\Videos\\Playlist\\missing.mp4" },
          playlistJobPayload.items[1]
        ]
      }
    ];
    mockState.localFileActionFailure = "视频文件不存在。";
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
    mockState.jobs = [
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
});
