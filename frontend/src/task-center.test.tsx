import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, test, vi } from "vitest";
import App from "./App";
import { jobPayload, playlistJobPayload } from "./test/appFixtures";
import { installAppHarness, mockState } from "./test/appHarness";

installAppHarness();

describe("App · 任务中心：控制 / 删除 / 展开", () => {
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

  test("explains that concurrency does not speed up single-video tasks", async () => {
    mockState.jobs = [jobPayload, playlistJobPayload];
    render(<App />);

    expect(await screen.findByText("Running video")).toBeInTheDocument();
    const singleCard = screen.getByText("Running video").closest(".job-card") as HTMLElement;
    expect(within(singleCard).getByText(/单视频任务不受并发设置影响/)).toBeInTheDocument();

    const playlistCard = screen.getByText("Playlist batch").closest(".job-card") as HTMLElement;
    expect(within(playlistCard).queryByText(/单视频任务不受并发设置影响/)).not.toBeInTheDocument();
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
});
