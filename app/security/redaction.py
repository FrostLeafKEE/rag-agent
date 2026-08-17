"""PII 脱敏引擎（FR-36，P2 W4）：摄入与回答两个环节生效。

规则类型：
- pattern：正则模式，保留首尾 N 字符（掩码中间），如手机号 138****5678
- word：敏感词表，整词替换为 replace_with

内置 PII 规则（手机号/身份证/邮箱/银行卡/密钥/IP），config/redaction.json 可追加
自定义敏感词与规则。命中（hits）供审计记录（pipeline 层写 audit_logs）。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_RULES: list[dict] = [
    # 手机号：保留前 3 后 4（(?<!\d)...(?!\d) 防止长数字串内部误伤）
    {
        "type": "pattern",
        "name": "phone",
        "pattern": r"(?<!\d)1[3-9]\d{9}(?!\d)",
        "keep_left": 3,
        "keep_right": 4,
    },
    # 身份证 15/18 位：保留前 4 后 4（独立数字串，避免误伤）
    {
        "type": "pattern",
        "name": "id_card",
        "pattern": r"(?<!\d)(?:\d{17}[\dXx]|\d{15})(?!\d)",
        "keep_left": 4,
        "keep_right": 4,
    },
    # 银行卡 16-19 位：保留前 4 后 4
    {
        "type": "pattern",
        "name": "bank_card",
        "pattern": r"(?<!\d)\d{16,19}(?!\d)",
        "keep_left": 4,
        "keep_right": 4,
    },
    # 邮箱：掩码本地部分，保留域名（a****@company.com）
    {
        "type": "pattern",
        "name": "email",
        "pattern": r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
        "keep_left": 1,
        "keep_right": 0,
        "email_keep_domain": True,
    },
    # API 密钥（sk-/pk-/ak- 前缀）：保留前缀与尾部 4 位
    {
        "type": "pattern",
        "name": "api_key",
        "pattern": r"\b(?:sk|pk|ak)-[A-Za-z0-9_-]{8,}\b",
        "keep_left": 4,
        "keep_right": 4,
    },
    # 内网 IP
    {
        "type": "pattern",
        "name": "ipv4",
        "pattern": r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b",
        "keep_left": 0,
        "keep_right": 0,
    },
]

# 正则模式每条最多生成掩码长度上限（超长文本不回显）
_MASK_CHAR = "*"
_TAIL_MAX = 8


def _mask_with_keep(match: re.Match, rule: dict) -> str:
    """掩码中间部分：保留首尾 keep_left/keep_right 字符；不足时全掩码。
    邮箱规则（email_keep_domain）掩码本地部分、保留域名。
    """
    text = match.group(0)
    if rule.get("email_keep_domain") and "@" in text:
        local, _, domain = text.partition("@")
        return local[:1] + _MASK_CHAR * 4 + "@" + domain
    keep_left = rule.get("keep_left", 0)
    keep_right = min(rule.get("keep_right", 0), _TAIL_MAX)
    if keep_left + keep_right >= len(text):
        return _MASK_CHAR * len(text)
    kept_left = text[:keep_left]
    kept_right = text[-keep_right:] if keep_right else ""
    return kept_left + _MASK_CHAR * (len(text) - keep_left - keep_right) + kept_right


@dataclass
class RedactionEngine:
    """规则引擎：redact(text) -> (脱敏文本, 命中规则名列表)。"""

    rules: list[dict] = field(default_factory=lambda: [dict(r) for r in DEFAULT_RULES])
    enabled: bool = True
    replace_with: str = "****"

    def __post_init__(self) -> None:
        self._patterns: list[tuple[dict, re.Pattern]] = []
        self._words: list[tuple[dict, list[str]]] = []
        for rule in self.rules:
            if rule.get("type") == "pattern" and rule.get("pattern"):
                try:
                    self._patterns.append((rule, re.compile(rule["pattern"])))
                except re.error:
                    logger.warning("脱敏规则正则非法，已跳过：%s", rule.get("name"))
            elif rule.get("type") == "word" and rule.get("words"):
                self._words.append((rule, [w for w in rule["words"] if w]))

    def redact(self, text: str) -> tuple[str, list[str]]:
        """返回 (脱敏后文本, 命中的规则名列表)；未启用或空文本原样返回。"""
        if not self.enabled or not text:
            return text, []
        masked = text
        hits: set[str] = set()
        for rule, pattern in self._patterns:
            if not pattern.search(masked):
                continue
            hits.add(rule["name"])

            def _apply(m: re.Match, r: dict = rule) -> str:
                return _mask_with_keep(m, r)

            masked = pattern.sub(_apply, masked)
        for rule, words in self._words:
            for word in words:
                if word in masked:
                    masked = masked.replace(word, self.replace_with)
                    hits.add(rule["name"])
        return masked, sorted(hits)


@lru_cache(maxsize=1)
def get_engine(
    config_path: str = "config/redaction.json",
    enabled: bool | None = None,
) -> RedactionEngine:
    """构建全局脱敏引擎：内置 PII 规则 + 配置文件自定义规则。

    enabled 显式传入时优先（env 开关）；否则读取配置文件 enabled（默认 True）。
    """
    from app.config import get_settings

    if enabled is None:
        enabled = get_settings().redaction_enabled
    path = Path(config_path)
    file_rules: list[dict] = []
    file_enabled: bool = True
    replace_with = "****"
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            file_rules = data.get("rules") or []
            file_enabled = data.get("enabled", True)
            replace_with = data.get("replace_with") or replace_with
        except json.JSONDecodeError:
            logger.warning("redaction.json 解析失败：%s", path)
    rules = [dict(r) for r in DEFAULT_RULES] + file_rules
    return RedactionEngine(rules=rules, enabled=enabled and file_enabled, replace_with=replace_with)
