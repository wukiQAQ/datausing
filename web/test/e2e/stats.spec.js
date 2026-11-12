// 统计看板端到端验收：真实后端 + 已构建前端（/app/）。
// 覆盖：指标口径、等级/类型筛选联动、检测记录抽屉、脱敏边界、处置队列认领、风险洞察导航。
// 数据来自真实 API：beforeAll 通过 /api/v1/tasks/text 提交一条文本任务，不伪造统计值。
import { test, expect } from "@playwright/test";

const BASE_URL = process.env.FLOWMASK_TEST_URL || "http://127.0.0.1:5173/app/";
const ORIGIN = new URL(BASE_URL).origin;

// 种子文本：手机号 + 身份证号，用于验证脱敏预览与 L3 下钻
const SEED_PHONE = "13900139000";
const SEED_ID_CARD = "11010519491231002X";
const SEED_TEXT = `统计看板端到端种子数据：客户手机 ${SEED_PHONE}，身份证 ${SEED_ID_CARD}。`;

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function tzOffset() {
  return String(-new Date().getTimezoneOffset());
}

// 与 web/src/stats.js formatPercent 同一口径，用来复算页面文案
function percent(value, digits = 1) {
  const number = Number(value);
  if (!Number.isFinite(number) || number < 0) return "—";
  const factor = 10 ** digits;
  const result = Math.round(number * 100 * factor) / factor;
  return `${Number.isInteger(result) ? result : result.toFixed(digits)}%`;
}

async function waitForStats(page, action, predicate = () => true) {
  const [response] = await Promise.all([
    page.waitForResponse((item) => item.url().includes("/api/v1/stats") && item.request().method() === "GET"),
    action(),
  ]);
  const body = await response.json();
  expect(predicate(response.url(), body), `stats 请求不符合预期：${response.url()}`).toBeTruthy();
  return { url: response.url(), body };
}

// 处置队列的状态切换是 el-radio-button：真实可点击的是内部 span，隐藏的 input 点击不会生效
function queueRadio(page, label) {
  return page.locator("#stats-risk-queue .el-radio-button__inner").filter({ hasText: label });
}

async function gotoStats(page) {
  await page.goto("#stats");
  await expect(page.getByRole("heading", { name: "统计看板", exact: true })).toBeVisible();
  // 指标卡加载完成后才会是数字；加载中显示「…」
  await expect(page.locator(".stat-value").first()).toHaveText(/\d/);
}

// 通过真实接口取一份与页面同一筛选口径的统计
async function apiStats(page, extra = {}) {
  const query = new URLSearchParams({ days: "30", tz_offset: tzOffset(), ...extra });
  const response = await page.request.get(`${ORIGIN}/api/v1/stats?${query}`);
  expect(response.ok()).toBeTruthy();
  return response.json();
}

test.describe.configure({ mode: "serial" });

test.describe("统计看板", () => {
  test.beforeAll(async ({ request }) => {
    const created = await request.post(`${ORIGIN}/api/v1/tasks/text`, { data: { text: SEED_TEXT } });
    expect(created.ok()).toBeTruthy();
    const { task_id: taskId } = await created.json();
    expect(taskId).toBeTruthy();

    // 等待后台检测完成，保证后续统计能覆盖到这条记录
    let task = null;
    for (let index = 0; index < 80; index += 1) {
      const response = await request.get(`${ORIGIN}/api/v1/tasks/${taskId}`);
      task = await response.json();
      if (task.status === "done" || task.status === "failed") break;
      await sleep(250);
    }
    expect(task?.status, "种子任务未在超时内完成检测").toBe("done");

    const findings = await request.get(`${ORIGIN}/api/v1/tasks/${taskId}/findings?page_size=50`);
    const body = await findings.json();
    expect(body.items.length, "种子任务没有产生检测记录").toBeGreaterThan(0);
    expect(body.items.some((item) => item.level === "L3"), "种子任务没有产生 L3 记录").toBeTruthy();
  });

  test.beforeEach(async ({ page }) => {
    await gotoStats(page);
  });

  test("四个核心指标与等级分布使用真实 stats 数据", async ({ page }) => {
    const stats = await apiStats(page);
    const cards = page.locator(".stat-card");
    await expect(cards).toHaveCount(4);

    await expect(cards.nth(0).locator(".stat-label")).toHaveText("检测数据总量");
    await expect(cards.nth(0).locator(".stat-value")).toContainText(String(stats.total_findings));
    await expect(cards.nth(0).locator(".stat-sub")).toContainText(`${stats.total_hits} 次敏感命中`);

    await expect(cards.nth(1).locator(".stat-value")).toContainText(String(stats.by_level.L3));
    await expect(cards.nth(1).locator(".stat-sub")).toContainText("点击筛选 L3");

    await expect(cards.nth(2).locator(".stat-value")).toContainText(String(stats.risk.open));
    await expect(cards.nth(2).locator(".stat-sub")).toContainText("点击打开队列");

    await expect(cards.nth(3).locator(".stat-value")).toHaveText(percent(stats.risk.handled_ratio));
    await expect(cards.nth(3).locator(".stat-sub")).toContainText(
      stats.risk.total ? `已处理 ${stats.risk.handled} / 告警 ${stats.risk.total} 条` : "当前筛选下暂无告警",
    );

    // 等级列表的数量与接口一致，且四级都有真实业务定义
    for (const level of ["L3", "L2", "L1", "SAFE"]) {
      const row = page.locator(".level-button").filter({ hasText: level }).first();
      await expect(row).toContainText(String(stats.by_level[level]));
    }
    await expect(page.locator(".level-defs")).toContainText("0.85");
    await expect(page.locator(".level-defs")).toContainText("0.25");
    await expect(page.locator(".level-defs")).toContainText("不产生告警");
  });

  test("点击等级同时筛统计与处置队列，并可重置", async ({ page }) => {
    const l3Button = page.locator(".level-button").filter({ hasText: "L3" }).first();
    const { url, body } = await waitForStats(
      page,
      () => l3Button.click(),
      (requestUrl) => requestUrl.includes("level=L3"),
    );
    expect(url).toContain("level=L3");
    expect(body.by_level.L3).toBeGreaterThan(0);
    expect(body.by_level.L2).toBe(0);
    expect(body.by_level.L1).toBe(0);

    await expect(page.locator(".active-filters .el-tag").filter({ hasText: "等级：" })).toContainText(
      "等级：L3 高危",
    );

    // 队列默认「待处理」，L3 可能已无待处理；切到「全部」验证筛选联动到告警接口
    const [queueResponse] = await Promise.all([
      page.waitForResponse((item) => item.url().includes("/api/v1/alerts")),
      queueRadio(page, "全部").click(),
    ]);
    const queue = await queueResponse.json();
    expect(queueResponse.url()).toContain("level=L3");
    expect(queue.total).toBeGreaterThan(0);

    const rows = page.locator("#stats-risk-queue .el-table__row");
    await expect(rows.first()).toBeVisible();
    const rowCount = await rows.count();
    expect(rowCount).toBe(Math.min(queue.total, 10));
    for (let index = 0; index < rowCount; index += 1) {
      await expect(rows.nth(index)).toContainText("L3");
    }

    // 重置：筛选条件与队列状态回到默认，统计重新回到全量
    const reset = await waitForStats(
      page,
      () => page.getByRole("button", { name: "重置", exact: true }).click(),
      (requestUrl) => !requestUrl.includes("level="),
    );
    expect(reset.body.by_level.L2).toBeGreaterThan(0);
    await expect(page.locator(".active-filters .el-tag")).toHaveCount(1);
    await expect(page.locator(".active-filters .el-tag").first()).toContainText("时间：近 30 天");
  });

  test("敏感类型多选筛选会同步到统计、队列与筛选标签", async ({ page }) => {
    const typeSelect = page
      .locator(".filter-item")
      .filter({ hasText: "敏感类型" })
      .locator(".el-select");
    await typeSelect.click();
    const option = page
      .locator(".el-select-dropdown:visible .el-select-dropdown__item")
      .filter({ hasText: "身份证号" })
      .first();
    const { url, body } = await waitForStats(
      page,
      () => option.click(),
      (requestUrl) => requestUrl.includes("types=id_card"),
    );
    expect(url).toContain("types=id_card");
    expect(body.by_type.length).toBeGreaterThan(0);
    // 筛选口径是“记录含该类型”，同一记录里的其他类型仍会一并统计
    const idCardRows = body.by_type.filter((row) => row.type === "id_card");
    expect(idCardRows).toHaveLength(1);
    expect(idCardRows[0].count).toBe(body.total_findings);

    const filtered = await (
      await page.request.get(
        `${ORIGIN}/api/v1/findings?days=30&tz_offset=${tzOffset()}&types=id_card&page_size=50`,
      )
    ).json();
    expect(filtered.total).toBe(body.total_findings);
    expect(filtered.items.every((item) => item.types.includes("id_card"))).toBeTruthy();
    await page.keyboard.press("Escape");

    await expect(page.locator(".active-filters .el-tag").filter({ hasText: "类型：" })).toContainText(
      "类型：身份证号",
    );

    await waitForStats(
      page,
      () => page.getByRole("button", { name: "重置", exact: true }).click(),
      (requestUrl) => !requestUrl.includes("types="),
    );
    await expect(page.locator(".active-filters .el-tag")).toHaveCount(1);
  });

  test("检测数据总量卡片打开记录抽屉，脱敏预览不含原文", async ({ page }) => {
    const [findingsResponse] = await Promise.all([
      page.waitForResponse((item) => item.url().includes("/api/v1/findings")),
      page.locator(".stat-card").first().click(),
    ]);
    const findings = await findingsResponse.json();
    const drawer = page.locator(".el-drawer:visible");
    await expect(drawer.getByText("检测记录（当前筛选）")).toBeVisible();
    await expect(drawer.locator(".hint-line").first()).toContainText(`共 ${findings.total} 条检测记录`);
    await expect(drawer.locator(".el-table__row").first()).toBeVisible();

    const text = await drawer.innerText();
    expect(text, "看板泄露了手机号原文").not.toContain(SEED_PHONE);
    expect(text, "看板泄露了身份证原文").not.toContain(SEED_ID_CARD);
    expect(text, "脱敏预览没有显示屏蔽标记").toContain("[已屏蔽:");

    await drawer.locator(".el-drawer__close-btn").click();
    await expect(drawer).toBeHidden();
  });

  test("处置队列认领写入真实状态并刷新指标", async ({ page }) => {
    const before = await apiStats(page);
    test.skip(before.risk.open === 0, "当前筛选下没有待处理告警，跳过认领用例");

    const ackButton = page
      .locator("#stats-risk-queue .el-table__row")
      .filter({ has: page.getByRole("button", { name: "认领" }) })
      .first()
      .getByRole("button", { name: "认领" });
    await expect(ackButton).toBeVisible();

    const statsWatcher = page.waitForResponse(
      (item) => item.url().includes("/api/v1/stats") && item.request().method() === "GET",
    );
    const queueWatcher = page.waitForResponse((item) => item.url().includes("/api/v1/alerts"));
    const [ackResponse] = await Promise.all([
      page.waitForResponse(
        (item) => item.url().includes("/ack") && item.request().method() === "POST",
      ),
      ackButton.click(),
    ]);

    expect(ackResponse.ok(), "认领接口失败").toBeTruthy();
    expect(ackResponse.request().headers()["x-operator-id"], "认领请求没有携带操作人").toBeTruthy();
    const updated = await ackResponse.json();
    expect(updated.status).toBe("ack");
    expect(updated.handled_by).toBeTruthy();

    const after = await (await statsWatcher).json();
    await queueWatcher;
    expect(after.risk.open).toBe(before.risk.open - 1);
    expect(after.risk.handled).toBe(before.risk.handled + 1);

    const cards = page.locator(".stat-card");
    await expect(cards.nth(2).locator(".stat-value")).toContainText(String(after.risk.open));
    await expect(cards.nth(3).locator(".stat-value")).toHaveText(percent(after.risk.handled_ratio));
  });

  test("风险洞察标明来源、结论非空且导航入口真实可用", async ({ page }) => {
    const badge = page.locator(".source-badge");
    await expect(badge).toBeVisible();
    await expect(badge).toHaveText(/规则分析|本机模型解读|模型不可用/);
    await expect(page.locator(".advise-text")).not.toBeEmpty();
    await expect(page.locator(".advise-text")).not.toHaveText("暂无结论");

    await page.getByRole("button", { name: "打开待处理队列" }).click();
    await expect(
      page.locator("#stats-risk-queue .el-radio-button.is-active .el-radio-button__inner"),
    ).toHaveText("待处理");

    await waitForStats(
      page,
      () => page.getByRole("button", { name: "查看 L3 记录" }).click(),
      (requestUrl) => requestUrl.includes("level=L3"),
    );
    await expect(page.locator(".active-filters .el-tag").filter({ hasText: "等级：" })).toContainText(
      "等级：L3 高危",
    );
  });

  test("风险详情抽屉展示处理信息且不泄露原文", async ({ page }) => {
    // 队列切到全部，保证有行可点
    await Promise.all([
      page.waitForResponse((item) => item.url().includes("/api/v1/alerts")),
      queueRadio(page, "全部").click(),
    ]);
    const rows = page.locator("#stats-risk-queue .el-table__row");
    await expect(rows.first()).toBeVisible();

    await rows.first().getByRole("button", { name: "详情" }).click();
    const drawer = page.locator(".el-drawer:visible");
    await expect(drawer.getByText("风险详情")).toBeVisible();
    await expect(drawer.locator(".detail-content")).toBeVisible({ timeout: 10000 });

    const content = await drawer.locator(".detail-content").innerText();
    expect(content, "详情抽屉泄露了手机号原文").not.toContain(SEED_PHONE);
    expect(content, "详情抽屉泄露了身份证原文").not.toContain(SEED_ID_CARD);

    const drawerText = await drawer.innerText();
    expect(drawerText).toContain("检测链路");
    expect(drawerText).toContain("处理信息");
    expect(drawerText).toContain("状态");
  });
});
