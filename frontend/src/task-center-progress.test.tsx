import { render, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import App from "./App";
import { jobPayload } from "./test/appFixtures";
import { installAppHarness, mockState } from "./test/appHarness";

installAppHarness();

describe("App · 任务中心：进度与详情呈现", () => {
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

  test("does not repeat single video item details in task center", async () => {
    render(<App />);

    expect(await screen.findByText("Running video")).toBeInTheDocument();
    expect(screen.queryByText("1. Running video · running")).not.toBeInTheDocument();
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
    mockState.jobs = [
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
    mockState.jobs = [
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
});
