"""从同一任务快照输出已验证的片段及字符映射；不读取持久化原文。"""
from ..security import SecurityError, protected_text
from ..identify.types import Level
from ..identify.snapshots import fingerprint

MAX_COMPARE_CHARS = 1_000_000


def comparison_payload(snapshot):
    document, report = snapshot.document, snapshot.report
    if sum(len(s.text) for s in document.segments) > MAX_COMPARE_CHARS:
        raise SecurityError('COMPARISON_TOO_LARGE', '文档过大，请缩小文件后重新检测', 413)
    findings = {f.path: f for f in report.all_findings or report.findings}
    requested = snapshot.options.get('mask_level')
    requested = Level.parse(requested) if requested else None
    segments = []
    for segment in document.segments:
        finding = findings.get(segment.path)
        mappings = []
        masked = segment.text
        if finding:
            if fingerprint(segment.text) != finding.original_hash:
                raise SecurityError('MASK_LOCATION_INVALID', '任务与原文不一致')
            masked = protected_text(segment.text, finding, requested, mappings)
            if masked != finding.masked:
                raise SecurityError('SNAPSHOT_POLICY_CHANGED', '任务脱敏副本已失效', 409)
            for mapping in mappings:
                mapping.update(level=finding.level.value, status='已脱敏')
        segments.append(dict(path=segment.path, original=segment.text, masked=masked, changes=mappings))
    return dict(filename=document.source, status='done', coverage=report.coverage,
                hits=sum(f.hits for f in findings.values()),
                representation='parsed-segments', offset_unit='unicode-codepoint', segments=segments)
