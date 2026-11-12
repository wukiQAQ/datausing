import { test, expect } from "@playwright/test";
import { readFile } from "node:fs/promises";

const sample = "😀客户张三，手机 13800138000，身份证 11010519491231002X。";
const input = (page) =>
  page.getByRole("textbox", { name: "待检测文本", exact: true });
async function detect(page, text = sample) {
  await input(page).fill(text);
  const response = page.waitForResponse(
    (r) =>
      r.url().endsWith("/api/v1/detect") && r.request().method() === "POST",
  );
  await page.getByRole("button", { name: /开始检测/ }).click();
  const body = await (await response).json();
  await expect(page.locator(".editor-code").last()).not.toBeEmpty();
  return body;
}
test.beforeEach(async ({ page }) => {
  await page.goto("?theme=dark");
  await expect(
    page.getByRole("heading", { name: "实时检测", exact: true }),
  ).toBeVisible();
});

test('snapshot export survives rule changes and matches encrypted strategy preview', async ({ page }) => {
  const ruleId = 'snapshot_e2e';
  const created = await page.request.post('/api/v1/rules', { data: {
    id: ruleId, name: 'snapshot e2e', type: 'secret', pattern: 'SEC_(\\d{6})', weight: .9, confidence: .99,
  } });
  expect(created.status()).toBe(201);
  try {
    const result = await detect(page, 'SEC_123456');
    expect(result.result_id).toBeTruthy();
    await expect(page.getByRole('button', { name: /开始检测/ })).toBeEnabled();
    await page.request.patch(`/api/v1/rules/${ruleId}`, { data: { enabled: false } });
    const previewResponse = page.waitForResponse(r => r.url().endsWith(`/results/${result.result_id}/preview`));
    await page.getByRole('combobox', { name: '导出脱敏策略', exact: true }).focus();
    await page.keyboard.press('Enter');
    await page.getByRole('option', { name: /加密/ }).click();
    const preview = await (await previewResponse).json();
    const request = page.waitForRequest(r => r.url().endsWith('/detect/export'));
    const download = page.waitForEvent('download');
    await page.getByRole('button', { name: '导出脱敏文件', exact: true }).click();
    const payload = (await request).postDataJSON();
    expect(payload.result_id).toBe(result.result_id);
    expect(payload).not.toHaveProperty('text');
    const content = await readFile(await (await download).path(), 'utf8');
    expect(content).toBe(preview.masked_document.masked);
    expect(content).not.toContain('123456');
  } finally {
    await page.request.delete(`/api/v1/rules/${ruleId}`);
  }
});

test("real detection, risk summary, Unicode highlight, details and themes preserve input", async ({
  page,
}) => {
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const body = await detect(page);
  expect(body.findings.length).toBeGreaterThan(0);
  await expect(page.getByRole("region", { name: "输入原文", exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "核验输入原文" }).click();
  const originalHit = page.getByRole("region", { name: "输入原文", exact: true }).locator("mark.hit").first();
  await expect(originalHit).toBeVisible();
  const hits = body.findings.reduce((sum, row) => sum + row.hits, 0);
  await expect(page.locator(".risk-summary strong")).toHaveText(String(hits));
  await originalHit.click();
  await expect(page.getByRole("dialog", { name: "命中详情" })).toBeVisible();
  await page.locator(".el-dialog__headerbtn").last().click();
  await page.getByRole("button", { name: "切换浅色主题" }).click();
  await expect(input(page)).toHaveValue(sample);
  await expect(page.locator("html")).not.toHaveClass(/dark/);
  await expect(originalHit).toBeVisible();
  expect(errors).toEqual([]);
});
test("JSON fields align by path and exported file comes from real API", async ({
  page,
}) => {
  await detect(page, '{"phone":"13800138000","email":"test@example.com"}');
  await page.getByRole("button", { name: "核验输入原文" }).click();
  await expect(page.locator(".editor-code").first()).toContainText("$.phone");
  await expect(page.locator("mark.hit").first()).toHaveText("13800138000");
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "导出脱敏文件", exact: true }).click();
  expect((await download).suggestedFilename()).toBeTruthy();
});
test("advanced controls, safe clear cancellation and confirmation", async ({
  page,
}) => {
  await input(page).fill(sample);
  await page.getByRole("button", { name: "高级检测选项" }).click();
  await expect(
    page.getByRole("checkbox", { name: /自由文本 NER/ }),
  ).toBeDisabled();
  await expect(
    page.getByRole("combobox", { name: "脱敏级别", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "清空工作区" }).click();
  await page.getByRole("button", { name: "保留", exact: true }).click();
  await expect(input(page)).toHaveValue(sample);
  await page.getByRole("button", { name: "清空工作区" }).click();
  await page.getByRole("button", { name: "清空", exact: true }).click();
  await expect(input(page)).toBeEmpty();
});
test("file upload returns masked copy and a failed replacement invalidates the prior snapshot", async ({
  page,
}) => {
  await input(page).fill("保留的文本草稿");
  await page.locator("input[type=file]").setInputFiles({
    name: "sample.txt",
    mimeType: "text/plain",
    buffer: Buffer.from(sample),
  });
  await expect(page.locator(".input-state")).toContainText("sample.txt");
  await expect(page.getByText("文件检测不回传原文")).toBeVisible();
  await expect(page.getByRole("button", { name: "核验输入原文" })).toHaveCount(0);
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "导出脱敏文件", exact: true }).click();
  expect((await download).suggestedFilename()).toContain(".txt");
  await page.locator("input[type=file]").setInputFiles({
    name: "invalid.json",
    mimeType: "application/json",
    buffer: Buffer.from("{invalid"),
  });
  await expect(page.locator(".detect-error")).toBeVisible();
  await expect(page.locator(".input-state")).toContainText("invalid.json");
  await expect(page.getByRole("button", { name: "导出脱敏文件", exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "改用文本" }).click();
  await expect(input(page)).toHaveValue("保留的文本草稿");
  await expect(page.getByRole("button", { name: /开始检测/ })).toBeEnabled();
});
test("real loading, safe exception and retry", async ({ page }) => {
  await page.route("**/api/v1/detect", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 450));
    await route.fulfill({
      status: 422,
      json: { detail: [{ input: "SECRET_SENTINEL" }] },
    });
  });
  await input(page).fill(sample);
  await page.getByRole("button", { name: /开始检测/ }).click();
  await expect(page.getByRole("button", { name: /正在检测/ })).toBeDisabled();
  await expect(page.locator(".detect-error")).toBeVisible();
  await expect(page.locator(".detect-error")).not.toContainText(
    "SECRET_SENTINEL",
  );
  await page.unroute("**/api/v1/detect");
  await detect(page);
});
test("all eight pages and desktop widths render with no page overflow or runtime errors", async ({
  page,
}) => {
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const names = [
    "批量检测",
    "流量导入",
    "任务与审计",
    "统计看板",
    "识别规则",
    "动态脱敏与授权",
    "设置与策略",
    "实时检测",
  ];
  for (const name of names) {
    await page.getByRole("link", { name, exact: true }).click();
    await expect(
      page.getByRole("heading", { name, exact: true }),
    ).toBeVisible();
    for (const width of [1000, 1280, 1440, 1920]) {
      await page.setViewportSize({ width, height: 900 });
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= window.innerWidth,
        ),
      ).toBe(true);
    }
  }
  await page.getByRole("button", { name: "折叠导航栏" }).click();
  await expect(page.locator(".workspace-shell")).toHaveClass(
    /sidebar-collapsed/,
  );
  await page.getByRole("button", { name: "展开导航栏" }).click();
  expect(errors).toEqual([]);
});

test("input and option changes hide the prior preview and revoke output", async ({ page }) => {
  await detect(page);
  await page.getByRole("button", { name: "核验输入原文" }).click();
  await input(page).fill("新的待检测草稿");
  await expect(page.getByText("结果已失效", { exact: true })).toBeVisible();
  await expect(page.locator(".editor-code")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "复制脱敏副本" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "导出脱敏文件" })).toHaveCount(0);
  await detect(page, "今天讨论活动时间。");
  await expect(page.getByText("未发现命中", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "高级检测选项" }).click();
  await page.getByText("请求 L2 语义", { exact: true }).click();
  await expect(page.getByRole("checkbox", { name: "请求 L2 语义" })).toBeChecked();
  await expect(page.getByText("结果已失效", { exact: true })).toBeVisible();
});

test("oversize paste is rejected and an IME Enter shortcut does not submit", async ({ page }) => {
  await input(page).fill(sample);
  const eventResult = await input(page).evaluate((editor) => {
    const data = new DataTransfer();
    data.setData("text/plain", "字".repeat(200001));
    const paste = new ClipboardEvent("paste", { clipboardData: data, bubbles: true, cancelable: true });
    editor.dispatchEvent(paste);
    const composing = new KeyboardEvent("keydown", {
      key: "Enter", ctrlKey: true, isComposing: true, bubbles: true, cancelable: true,
    });
    editor.dispatchEvent(composing);
    return { paste: paste.defaultPrevented, composing: composing.defaultPrevented };
  });
  expect(eventResult).toEqual({ paste: true, composing: false });
  await expect(page.getByText("粘贴内容超出上限", { exact: false })).toBeVisible();
  await expect(input(page)).toHaveValue(sample);
  await expect(page.locator(".editor-code")).toHaveCount(0);
});

test("incomplete coverage and dropped output suppress preview, clipboard and export", async ({ page }) => {
  for (const [change, message] of [
    [(body) => { body.coverage = "partial"; }, "检测覆盖不完整"],
    [(body) => { body.masked_document.segments = []; }, "可能存在截断或丢段"],
  ]) {
    await page.route("**/api/v1/detect", async (route) => {
      const response = await route.fetch();
      const body = await response.json();
      change(body);
      await route.fulfill({ response, json: body });
    });
    await input(page).fill(sample);
    await page.getByRole("button", { name: /开始检测/ }).click();
    await expect(page.getByText(message, { exact: false })).toBeVisible();
    await expect(page.locator(".editor-code")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "导出脱敏文件" })).toHaveCount(0);
    await page.unroute("**/api/v1/detect");
  }
});

test("copy revalidates authorization and an expired preview remains locked", async ({ page }) => {
  for (const [status, message] of [[403, "权限不足"], [410, "结果已失效"]]) {
    await detect(page);
    await page.route("**/api/v1/detect/results/*/preview", (route) =>
      route.fulfill({ status, json: { detail: "SECRET_SENTINEL" } }));
    await page.getByRole("button", { name: "复制脱敏副本" }).click();
    await expect(page.getByText(message, { exact: true })).toBeVisible();
    await expect(page.locator(".result-workspace")).not.toContainText("SECRET_SENTINEL");
    await expect(page.locator(".editor-code")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "导出脱敏文件" })).toHaveCount(0);
    await page.unroute("**/api/v1/detect/results/*/preview");
  }
});

test("leaving detection cancels a pending snapshot download", async ({ page }) => {
  await detect(page);
  let release;
  let seen;
  const waitForNavigation = new Promise((resolve) => { release = resolve; });
  const requested = new Promise((resolve) => { seen = resolve; });
  await page.route("**/api/v1/detect/export", async (route) => {
    const response = await route.fetch();
    seen();
    await waitForNavigation;
    await route.fulfill({ response });
  });
  try {
    await page.getByRole("button", { name: "导出脱敏文件", exact: true }).click();
    await requested;
    await page.getByRole("link", { name: "统计看板", exact: true }).click();
    await expect(page.getByRole("heading", { name: "统计看板", exact: true })).toBeVisible();
    const download = page.waitForEvent("download", { timeout: 1000 }).catch(() => null);
    release();
    expect(await download).toBeNull();
  } finally {
    release();
  }
});

test("B workspace stays within 320–430px and has two desktop columns", async ({ page }) => {
  await page.goto("?theme=light");
  await detect(page);
  const source = page.locator(".input-workspace");
  const result = page.locator(".result-workspace");
  const screenshotDir = process.env.FLOWMASK_QA_SCREENSHOTS;
  await page.setViewportSize({ width: 1440, height: 900 });
  expect((await result.boundingBox()).x).toBeGreaterThan((await source.boundingBox()).x);
  if (screenshotDir) await page.screenshot({ path: `${screenshotDir}/FlowMask-B-desktop.png` });
  for (const width of [320, 390, 430]) {
    await page.setViewportSize({ width, height: 844 });
    const box = await result.boundingBox();
    expect(box.y).toBeGreaterThan((await source.boundingBox()).y);
    expect(box.x + box.width).toBeLessThanOrEqual(width);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    if (screenshotDir && width === 390) await page.screenshot({ path: `${screenshotDir}/FlowMask-B-mobile.png`, fullPage: true });
  }
  await page.setViewportSize({ width: 390, height: 844 });
  const menu = page.getByRole("button", { name: "导航", exact: true });
  for (const name of ["批量检测", "流量导入", "任务与审计", "统计看板", "识别规则", "动态脱敏与授权", "设置与策略"]) {
    await menu.click();
    await page.getByRole("link", { name, exact: true }).click();
    await expect(page.getByRole("heading", { name, exact: true })).toBeVisible();
    await expect(menu).toHaveAttribute("aria-expanded", "false");
    expect(await page.evaluate(() => document.documentElement.scrollWidth), name).toBeLessThanOrEqual(390);
  }
});

test("batch submits real files, isolates failure, exports and clears upload state", async ({
  page,
}) => {
  await page.getByRole("link", { name: "批量检测", exact: true }).click();
  await page.locator("input[type=file]").setInputFiles([
    { name: "batch.txt", mimeType: "text/plain", buffer: Buffer.from(sample) },
    {
      name: "broken.json",
      mimeType: "application/json",
      buffer: Buffer.from("{invalid"),
    },
  ]);
  await page.getByRole("button", { name: "开始批量检测", exact: true }).click();
  await expect(page.getByText("解析失败", { exact: true })).toBeVisible();
  const good = page.locator(".el-table__row").filter({ hasText: "batch.txt" });
  await expect(good).toContainText("done", { timeout: 15000 });
  await good.getByRole("button", { name: "查看", exact: true }).click();
  await expect(
    page.locator(".panel-title").filter({ hasText: "结果明细" }),
  ).toBeVisible();
  const downloaded = page.waitForEvent("download");
  await good.getByRole("button", { name: "明细", exact: true }).click();
  expect((await downloaded).suggestedFilename()).toContain("jsonl");
  await page.getByRole("button", { name: "清空文件", exact: true }).click();
  await expect(page.locator(".el-upload-list__item")).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "开始批量检测", exact: true }),
  ).toBeDisabled();
});

test("tasks, audit verification and privacy export preserve live contracts", async ({
  page,
}) => {
  await page.getByRole("link", { name: "任务与审计", exact: true }).click();
  await page
    .getByRole("textbox", { name: "粘贴文本创建检测任务" })
    .fill(sample);
  await page
    .getByRole("button", { name: "提交任务（文本）", exact: true })
    .click();
  await page.getByRole("button", { name: "刷新", exact: true }).click();
  await expect(page.locator(".el-table__row").first()).toContainText("done", {
    timeout: 10000,
  });
  await page.locator(".el-table__row").first().click();
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "隐私导出", exact: true }).click();
  expect((await download).suggestedFilename()).toContain("privacy");
  await page.getByRole("tab", { name: "审计链与验签" }).click();
  await page.getByRole("button", { name: "刷新并校验" }).click();
  await expect(page.locator(".panel:visible")).toContainText("通过");
});

test("rule CRUD uses backend and search; builtin delete stays disabled", async ({
  page,
}) => {
  const ruleName = `ui_qa_employee_${Date.now()}`;
  await page.getByRole("link", { name: "识别规则", exact: true }).click();
  await expect(
    page
      .locator(".el-table__row")
      .first()
      .getByRole("button", { name: "删除" }),
  ).toBeDisabled();
  await page.getByRole("button", { name: "新增规则", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "新增识别规则" });
  await dialog
    .getByRole("textbox", { name: "规则名", exact: true })
    .fill(ruleName);
  await dialog
    .getByRole("textbox", { name: "正则", exact: true })
    .fill("(UIQA[0-9]{6})");
  await dialog.getByRole("button", { name: "保存并生效" }).click();
  await page
    .getByRole("textbox", { name: "搜索识别规则", exact: true })
    .fill(ruleName);
  const row = page.locator(".el-table__row").filter({ hasText: ruleName });
  await expect(row).toHaveCount(1);
  await row.locator(".el-switch").click();
  await expect(row.getByRole("switch")).not.toBeChecked();
  await row.getByRole("button", { name: "删除", exact: true }).click();
  await page.getByRole("button", { name: "删除规则", exact: true }).click();
  await expect(page.locator(".el-table__row")).toHaveCount(0);
});

test("dynamic authorization follows actual response and settings retain all sections", async ({
  page,
}) => {
  await page.getByRole("link", { name: "动态脱敏与授权", exact: true }).click();
  await expect(page.locator(".pane-body").nth(1)).toContainText("未授权");
  await page.locator(".el-switch").click();
  await expect(page.locator(".inline-notice")).toContainText("上次响应");
  await page.getByRole("button", { name: "渲染", exact: true }).click();
  await expect(page.locator(".inline-notice")).toContainText("服务端未授予");
  await expect(page.locator(".pane-body").nth(1)).not.toContainText(
    "13800138000",
  );
  await page.getByPlaceholder("操作人", { exact: true }).fill("auditor-qa");
  await page.getByRole("button", { name: "渲染", exact: true }).click();
  await expect(page.locator(".pane-body").nth(1)).toContainText("13800138000");
  await page.locator(".el-switch").click();
  await expect(page.locator(".pane-body").nth(1)).not.toContainText(
    "13800138000",
  );
  await page.getByRole("link", { name: "设置与策略", exact: true }).click();
  await expect(
    page.getByText("只读状态：模型与助手都由服务端环境变量配置"),
  ).toBeVisible();
  await page.getByRole("tab", { name: "策略与场景库" }).click();
  await expect(page.getByText("默认策略：", { exact: false })).toBeVisible();
  await page.getByRole("tab", { name: "使用与安全说明" }).click();
  await expect(page.locator(".faq-item").first()).toBeVisible();
});

test("traffic sample produces real task and masked findings; live entry stays available", async ({
  page,
}) => {
  await page.getByRole("link", { name: "流量导入", exact: true }).click();
  await page.getByRole("button", { name: "导入自带示例", exact: true }).click();
  await expect(page.locator(".result-head")).toBeVisible({ timeout: 15000 });
  await expect(page.locator(".el-table__row").first()).toBeVisible();
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "导出 CSV", exact: true }).click();
  expect((await download).suggestedFilename()).toContain("csv");
  await page.getByRole("tab", { name: "实时旁路" }).click();
  await expect(
    page.getByRole("button", { name: "开始跟随", exact: true }),
  ).toBeVisible();
});

test("large structured results paginate without losing full masked copy and keyboard skip retains page", async ({
  page,
  context,
}) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await detect(
    page,
    JSON.stringify(
      Array.from({ length: 120 }, () => ({ phone: "13800138000" })),
    ),
  );
  await expect(
    page.locator(".details-panel .el-table__body .el-table__row"),
  ).toHaveCount(50);
  const pagination = page.locator(".table-footer").first();
  await pagination.locator(".btn-next").click();
  await expect(page.locator(".editor-code").first()).toContainText(
    "$[50].phone",
  );
  await page.getByRole("button", { name: "复制脱敏副本", exact: true }).click();
  await expect.poll(() => page.evaluate(async () => (await navigator.clipboard.readText()).split("\n").length)).toBe(120);
  const copied = await page.evaluate(() => navigator.clipboard.readText());
  expect(copied).not.toContain("$[119].phone");
  expect(copied).not.toContain("13800138000");
  await page.getByRole("link", { name: "统计看板", exact: true }).click();
  await page.locator(".skip-link").focus();
  await page.keyboard.press("Enter");
  await expect(page.locator("#main-content")).toBeFocused();
  await expect(
    page.getByRole("heading", { name: "统计看板", exact: true }),
  ).toBeVisible();
});

test("assistant sends aggregate data only; encrypted export can be restored by real backend", async ({
  page,
}) => {
  await detect(page);
  await page.getByRole("button", { name: "AI 推荐方案", exact: true }).click();
  const suggestion = page.waitForRequest((r) =>
    r.url().endsWith("/assistant/suggest"),
  );
  await page.getByRole("button", { name: "获取推荐", exact: true }).click();
  const payload = (await suggestion).postDataJSON();
  expect(payload).not.toHaveProperty("text");
  expect(JSON.stringify(payload)).not.toContain("13800138000");
  await expect(
    page.getByRole("button", { name: "应用到当前设置", exact: true }),
  ).toBeEnabled();
  await page.getByRole("button", { name: "关闭", exact: true }).click();
  await expect(
    page.getByRole("dialog", { name: "AI 推荐脱敏方案" }),
  ).not.toBeVisible();
  await page
    .getByRole("combobox", { name: "导出脱敏策略", exact: true })
    .focus();
  await page.keyboard.press("Enter");
  await page.getByRole("option", { name: /加密/ }).click();
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "导出脱敏文件", exact: true }).click();
  const ciphertext = await readFile(await (await download).path(), "utf8");
  expect(ciphertext).toContain("enc:");
  await page.getByRole("button", { name: "还原加密内容", exact: true }).click();
  await page.getByPlaceholder("粘贴含 enc: 密文的脱敏文本").fill(ciphertext);
  await page.getByRole("button", { name: "还原", exact: true }).click();
  await expect(
    page.getByRole("dialog", { name: "还原加密内容" }).locator(".pane-body"),
  ).toContainText("13800138000");
});
