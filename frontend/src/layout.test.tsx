import { render, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import App from "./App";
import { settingsPayload } from "./test/appFixtures";
import { installAppHarness, mockState } from "./test/appHarness";

installAppHarness();

describe("App · 版面与文案不变式", () => {
  test("omits redundant panel subtitle text", async () => {
    render(<App />);

    expect(await screen.findByRole("heading", { name: "解析链接" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "下载选项" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "设置" })).toBeInTheDocument();
    expect(screen.queryByText("支持单视频和 playlist")).not.toBeInTheDocument();
    expect(screen.queryByText("视频、字幕和批量策略")).not.toBeInTheDocument();
    expect(screen.queryByText("本机下载默认值")).not.toBeInTheDocument();
  });

  test("does not show optional ffprobe status in the topbar", async () => {
    mockState.settings = { ...settingsPayload, ffmpeg: { ffmpeg: true, ffprobe: false } };

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
});
