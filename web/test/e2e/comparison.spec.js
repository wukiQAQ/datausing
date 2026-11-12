import { test, expect } from '@playwright/test';
import { readFile } from 'node:fs/promises';
async function submit(page, text) {
  await page.goto('#batch');
  await page.locator('input[type=file]').setInputFiles({ name: 'comparison.txt', mimeType: 'text/plain', buffer: Buffer.from(text) });
  await page.getByRole('button', { name: '开始批量检测' }).click();
  await expect(page.getByRole('button', { name: '对比', exact: true })).toBeEnabled({ timeout: 15000 });
  await page.getByRole('button', { name: '对比', exact: true }).click();
  await expect(page.locator('.compare-body')).toBeVisible();
}
test('真实对比、双向定位、下载、视图和 Escape 清理', async ({ page }) => {
  const errors = []; page.on('pageerror', e => errors.push(e.message));
  await submit(page, '😀电话：13800138000\n正常文字\n第二处：13900139000');
  const dialog = page.getByRole('dialog');
  await expect(dialog.locator('.document-pane:visible')).toHaveCount(2);
  await expect(dialog.locator('.compare-toolbar')).toContainText('检测完成 · 已脱敏');
  await expect(dialog.locator('.original .change').first()).toHaveText('13800138000');
  await dialog.locator('.original .change').first().click();
  await expect(dialog.locator('.masked .change.selected')).toHaveCount(1);
  const info = dialog.getByLabel('字段信息', { exact: true });
  await expect(info).toContainText('实际检测级别');
  // 复用既有 RiskIndicator 组件（级别随真实检测结果变化，不写死某一级）。
  await expect(info.locator('.risk-badge')).toBeVisible();
  await dialog.locator('.masked .change').last().click();
  await expect(dialog.locator('.original .change.selected')).toHaveText('13900139000');
  await dialog.getByRole('button', { name: '下一处', exact: true }).click();
  await expect(dialog.locator('.original .change.selected')).toHaveText('13800138000');
  await page.screenshot({ path: '.qa/comparison-desktop.png' });
  const download = page.waitForEvent('download');
  await dialog.getByRole('button', { name: '下载脱敏文档' }).click();
  const content = await readFile(await (await download).path(), 'utf8');
  expect(content).not.toContain('13800138000'); expect(content).toContain('正常文字');
  await dialog.getByRole('combobox').focus();
  await page.keyboard.press('Enter');
  await page.getByRole('option', { name: '仅查看脱敏文档', exact: true }).click();
  await expect(dialog.locator('.original:visible')).toHaveCount(0);
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(page.locator('.document-scroll')).toHaveCount(0);
  await expect(page.getByRole('button', { name: '对比', exact: true })).toBeFocused();
  expect(errors).toEqual([]);
});
test('虚拟行、同步滚动、换行和窄屏单栏', async ({ page }) => {
  await submit(page, Array.from({ length: 3000 }, (_, i) => i === 2500 ? '电话13800138000' : '正文第' + i + '行').join('\n'));
  const dialog = page.getByRole('dialog');
  expect(await dialog.locator('.document-line').count()).toBeLessThan(160);
  await dialog.getByRole('button', { name: '下一处', exact: true }).click();
  await expect(dialog.locator('.original .change.selected')).toBeVisible();
  await expect(dialog.locator('.masked .change.selected')).toBeVisible();
  await dialog.locator('.original .document-scroll').evaluate(e => { e.scrollTop = 2600; });
  await expect.poll(() => dialog.locator('.masked .document-scroll').evaluate(e => e.scrollTop)).toBeGreaterThan(2500);
  await dialog.getByText('自动换行', { exact: true }).click();
  await expect(dialog.getByRole('checkbox', { name: '自动换行' })).toBeChecked();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(dialog.locator('.document-pane:visible')).toHaveCount(1);
  const viewSelect = dialog.locator('.el-select');
  await expect(viewSelect).toContainText('仅查看原文');
  await expect(viewSelect).not.toContainText('左右对比');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await dialog.getByRole('combobox').focus();
  await page.keyboard.press('Enter');
  await page.getByRole('option', { name: '仅查看脱敏文档', exact: true }).click();
  await expect(dialog.locator('.masked')).toBeVisible();
  await page.screenshot({ path: '.qa/comparison-mobile.png' });
});

test('拒绝读取原文时不渲染内容，关闭取消加载', async ({ page }) => {
  await page.route('**/tasks/*/comparison', route => route.fulfill({ status: 403, contentType: 'application/json', body: '{"detail":"forbidden"}' }));
  await page.goto('#batch');
  await page.locator('input[type=file]').setInputFiles({ name: 'safe.txt', mimeType: 'text/plain', buffer: Buffer.from('hello') });
  await page.getByRole('button', { name: '开始批量检测' }).click();
  const entry = page.getByRole('button', { name: '对比', exact: true });
  await expect(entry).toBeEnabled({ timeout: 15000 });
  await entry.click();
  await expect(page.getByRole('dialog')).toContainText('当前会话无权读取该任务原文');
  await expect(page.locator('.document-pane')).toHaveCount(0);
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await page.unroute('**/tasks/*/comparison');
  let intercepted;
  await page.route('**/tasks/*/comparison', route => { intercepted = route; });
  await entry.click();
  await expect(page.getByRole('dialog')).toContainText('正在加载');
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).toHaveCount(0);
  if (intercepted) await intercepted.abort().catch(() => {});
  await expect(page.locator('.document-pane')).toHaveCount(0);
});

test('点击高亮保留当前视口并联动对侧定位', async ({ page }) => {
  const lines = Array.from({ length: 1000 }, (_, i) => i === 500 ? '电话13800138000' : '正文第' + i + '行');
  await submit(page, lines.join('\n'));
  const dialog = page.getByRole('dialog');
  const original = dialog.locator('.original .document-scroll');
  // 让目标行落在视口中部而非顶部：点击后本侧不应被重新定位（旧实现会顶到行首）。
  await original.evaluate(e => { e.scrollTop = 12900; });
  await expect.poll(() => original.evaluate(e => Math.round(e.scrollTop))).toBe(12900);
  await dialog.locator('.original .change').click();
  await expect(dialog.locator('.masked .change.selected')).toHaveCount(1);
  await expect.poll(() => original.evaluate(e => Math.round(e.scrollTop))).toBe(12900);
  await expect.poll(() => dialog.locator('.masked .document-scroll').evaluate(e => Math.round(e.scrollTop))).toBeGreaterThan(0);
});

test('Escape 先收起视图下拉，再关闭弹窗', async ({ page }) => {
  await submit(page, '电话：13800138000\n正常文字');
  const dialog = page.getByRole('dialog');
  await dialog.getByRole('combobox').focus();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('option', { name: '仅查看原文', exact: true })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).toHaveCount(1);
  await expect(page.getByRole('option', { name: '仅查看原文', exact: true })).toBeHidden();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).toHaveCount(0);
});

test('自动换行长行文档的差异导航仍能定位到可见高亮', async ({ page }) => {
  const long = '长'.repeat(900);
  const lines = Array.from({ length: 1200 }, (_, i) => i === 1100 ? '电话13800138000' : (i >= 1097 ? long : '正文第' + i + '行'));
  await submit(page, lines.join('\n'));
  const dialog = page.getByRole('dialog');
  await dialog.getByText('自动换行', { exact: true }).click();
  await expect(dialog.getByRole('checkbox', { name: '自动换行' })).toBeChecked();
  await dialog.getByRole('button', { name: '下一处', exact: true }).click();
  const change = dialog.locator('.original .change.selected');
  await expect(change).toBeVisible();
  const [box, view] = await Promise.all([
    change.boundingBox(),
    dialog.locator('.original .document-scroll').boundingBox(),
  ]);
  expect(box.y).toBeGreaterThanOrEqual(view.y - 8);
  expect(box.y + box.height).toBeLessThanOrEqual(view.y + view.height + 8);
});

test('单栏与双栏切换后保留选中并恢复同步滚动', async ({ page }) => {
  const lines = Array.from({ length: 1200 }, (_, i) => i === 900 ? '电话13800138000' : '正文第' + i + '行');
  await submit(page, lines.join('\n'));
  const dialog = page.getByRole('dialog');
  await dialog.getByRole('button', { name: '下一处', exact: true }).click();
  await expect(dialog.locator('.original .change.selected')).toBeVisible();
  await dialog.getByRole('combobox').focus();
  await page.keyboard.press('Enter');
  await page.getByRole('option', { name: '仅查看脱敏文档', exact: true }).click();
  await expect(dialog.locator('.document-pane:visible')).toHaveCount(1);
  await dialog.getByRole('combobox').focus();
  await page.keyboard.press('Enter');
  await page.getByRole('option', { name: '左右对比', exact: true }).click();
  await expect(dialog.locator('.document-pane:visible')).toHaveCount(2);
  await expect(dialog.locator('.original .change.selected')).toBeVisible();
  await expect(dialog.locator('.masked .change.selected')).toBeVisible();
  await dialog.locator('.original .document-scroll').evaluate(e => { e.scrollTop = 20000; });
  await expect.poll(() => dialog.locator('.masked .document-scroll').evaluate(e => e.scrollTop)).toBeGreaterThan(19000);
});
