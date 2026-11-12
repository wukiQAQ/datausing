import test from 'node:test';
import assert from 'node:assert/strict';
import { prepareComparison, mapLine, needsScroll, estimateWrappedHeight } from '../src/comparison.js';

test('码点定位、重复值、跨行与删除映射不使用字符串搜索', () => {
  const model = prepareComparison({ status: 'done', hits: 2, coverage: 'complete', segments: [{ path: '$', original: '😀张三\n张三', masked: '😀NAME\n', changes: [
    { start: 1, end: 3, masked_start: 1, masked_end: 5 },
    { start: 4, end: 6, masked_start: 6, masked_end: 6 },
  ] }] });
  assert.equal(model.status, 'done');
  assert.equal(model.hits, 2);
  assert.equal(model.original[0].parts[0].text, '😀');
  assert.equal(model.original[0].parts[1].text, '张三');
  assert.equal(model.masked[0].parts[1].text, 'NAME');
  assert.equal(model.masked[1].parts[1].id, 1);
  assert.equal(model.changes[1].originalLine, 1);
});
test('根据差异锚点映射不同文档行数', () => {
  const anchors = [{ originalLine: 0, maskedLine: 0 }, { originalLine: 10, maskedLine: 5 }, { originalLine: 20, maskedLine: 30 }];
  assert.equal(mapLine(anchors, 'original', 15), 17.5);
  assert.equal(mapLine(anchors, 'masked', 5), 10);
});
test('长文档行构建不超过 JS 函数参数上限', () => {
  const text = '\n'.repeat(140000);
  const model = prepareComparison({ segments: [{ path: '$', original: text, masked: text, changes: [] }] });
  assert.equal(model.original.length, 140001);
  assert.equal(model.changes.length, 0);
});
test('点击定位只在目标贴近边缘或不可见时滚动', () => {
  // 视口 0..500、行高 26、上下各留 2 行缓冲：目标在缓冲区外可见时保持原视口。
  assert.equal(needsScroll(100, 26, 0, 500), false);
  assert.equal(needsScroll(20, 26, 0, 500), true);
  assert.equal(needsScroll(480, 26, 0, 500), true);
  assert.equal(needsScroll(600, 26, 0, 500), true);
  assert.equal(needsScroll(26 * 5, 26, 26 * 2, 500), false);
  assert.equal(needsScroll(26 * 3, 26, 26 * 2, 500), true);
});
test('乱序 changes 仍按位置渲染，不复制正文', () => {
  const model = prepareComparison({ segments: [{ path: '$', original: 'abcdefghij', masked: 'a1****g12j', changes: [
    { start: 7, end: 9, masked_start: 7, masked_end: 9 },
    { start: 1, end: 6, masked_start: 1, masked_end: 6 },
  ] }] });
  assert.equal(model.original[0].parts.map((part) => part.text).join(''), 'abcdefghij');
  assert.equal(model.masked[0].parts.map((part) => part.text).join(''), 'a1****g12j');
  assert.deepEqual(model.original[0].parts.filter((part) => part.id !== undefined).map((part) => part.text), ['bcdef', 'hi']);
});
test('重叠 changes 不复制文本', () => {
  const model = prepareComparison({ segments: [{ path: '$', original: 'abcdefghij', masked: 'abcdefghij', changes: [
    { start: 3, end: 8, masked_start: 3, masked_end: 8 },
    { start: 1, end: 6, masked_start: 1, masked_end: 6 },
  ] }] });
  assert.equal(model.original[0].parts.map((part) => part.text).join(''), 'abcdefghij');
  assert.equal(model.masked[0].parts.map((part) => part.text).join(''), 'abcdefghij');
});
test('锚点映射对负值/重复行稳定，不产生 NaN', () => {
  const linear = [{ originalLine: 0, maskedLine: 0 }, { originalLine: 4, maskedLine: 2 }];
  assert.equal(mapLine(linear, 'original', -0.5), 0);
  assert.equal(Number.isFinite(mapLine(linear, 'masked', 3)), true);
  // 整段屏蔽：多行原文落在同一脱敏行，同一行取行首而不是末行。
  const wrapped = [
    { originalLine: 0, maskedLine: 0 }, { originalLine: 0, maskedLine: 0 },
    { originalLine: 3, maskedLine: 0 }, { originalLine: 4, maskedLine: 1 },
    { originalLine: 103, maskedLine: 100 },
  ];
  assert.equal(mapLine(wrapped, 'masked', 0), 0);
  assert.equal(mapLine(wrapped, 'masked', 0.5), 3.5);
  assert.equal(mapLine(wrapped, 'masked', 100), 103);
  assert.equal(mapLine([], 'original', 3), 0);
});
test('自动换行行高按可视宽度与全角字符估算', () => {
  assert.equal(estimateWrappedHeight('', 600), 26);
  assert.equal(estimateWrappedHeight('abc', 600), 26);
  // 600 / 7.8 ≈ 76 列：152 个半角字符折成 2 行，80 个全角字符折成 3 行。
  assert.equal(estimateWrappedHeight('a'.repeat(152), 600), 52);
  assert.equal(estimateWrappedHeight('中'.repeat(80), 600), 78);
});
