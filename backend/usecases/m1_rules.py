"""Deterministic rule baseline for the M1 vertical loop.

The rules are intentionally simple and readable: they give M1 a reproducible
floor and expose where rules fail. They are not a quality claim; the labeled set
is small and was written alongside these rules, which the report must state.
Every piece of evidence is the verbatim span taken from the original text.
"""
import re
from dataclasses import dataclass, field

INJECTION = [
    ("忽略以上", "忽略指令"),
    ("ignore previous instructions", "忽略指令"),
    ("系统提示", "伪装系统提示"),
    ("system prompt", "伪装系统提示"),
    ("导出所有邮件", "批量导出请求"),
    ("管理员密码", "索取凭据"),
    ("密钥贴", "索取凭据"),
    ("reply with your password", "索取凭据"),
    ("忽略安全策略", "绕过安全策略"),
    ("without approval", "绕过审批"),
    ("execute tools without approval", "绕过审批"),
    ("把上面的系统提示", "诱导泄露提示"),
    ("customer list to", "外传客户数据"),
    ("attacker@", "外部投递地址"),
]
NOISE = [
    ("退订", "订阅退订"),
    ("unsubscribe", "订阅退订"),
    ("限时优惠", "促销"),
    ("预购", "促销"),
    ("满减", "促销"),
    ("weekly digest", "摘要通知"),
    ("digest:", "摘要通知"),
    ("满意度调查", "调查邀请"),
    ("自动发送", "自动通知"),
]
ASK = [
    "能否", "能不能", "请问", "请把", "请确认", "麻烦", "可以吗", "是否可以",
    "有没有人", "需要你确认", "?", "？",
    "could you", "can you", "anyone able", "anyone know", "please confirm",
]
TASK = [
    "前完成", "前提交", "前给", "归档", "更新", "补齐", "清理", "开好", "跑一遍",
    "提交", "发我", "补一份", "发一份", "schedule", "update", "submit", "rotate",
    "before ", " by ", "reminder:", "通知前端", "贴结果",
]
SEQUENCE = ["然后"]
VAGUE = ["有空", "下次", "回头聊", "不急", "sometime", "no rush", "catch up", "喝咖啡"]
NO_ACTION = ["no action needed", "no reply needed", "无需回复", "不用回复", "仅供参考"]
PAST = ["已清理", "已修复", "已上线", "已归档", "记录："]
URGENT = ["今天", "明天", "asap", "紧急", "urgent", "18:00", "eod", "本周五", "周五前",
          "by tomorrow", "before friday"]
NEAR_DEADLINE = re.compile(r"(下周一|下周三|下周五|周五)前")

CLASSES = ("needs_reply", "action", "fyi", "noise", "injection")
PRIORITIES = ("low", "normal", "high")


@dataclass
class Suggestion:
    classification: str
    priority: str
    wants_todo: bool
    wants_draft: bool
    confidence: float
    evidence: list = field(default_factory=list)


def find_span(text, needle):
    """Return the verbatim span from ``text`` for a case-insensitive needle."""
    if not text or not needle:
        return None
    index = text.lower().find(needle.lower())
    if index < 0:
        return None
    return text[index:index + len(needle)]


def _matches(text, table):
    """Return [(verbatim span, reason)] for every matching needle."""
    hits = []
    for pattern in table:
        needle, reason = pattern if isinstance(pattern, tuple) else (pattern, pattern)
        span = find_span(text, needle)
        if span is not None:
            hits.append((span, reason))
    return hits


def suggest(text, *, kind="email"):
    """Return a rule-based suggestion whose evidence is always verbatim."""
    text = text or ""
    evidence = []

    injection = _matches(text, INJECTION)
    if injection:
        evidence = [{"span": span, "kind": "injection", "reason": reason} for span, reason in injection[:3]]
        return Suggestion("injection", "high", False, False, 0.95, evidence)

    noise = _matches(text, NOISE)
    if noise and not _matches(text, ASK):
        evidence = [{"span": span, "kind": "noise", "reason": reason} for span, reason in noise[:2]]
        return Suggestion("noise", "low", False, False, 0.85, evidence)

    asks = _matches(text, ASK)
    tasks = _matches(text, TASK)
    no_action = _matches(text, NO_ACTION)
    past = _matches(text, PAST)
    vague = _matches(text, VAGUE)
    sequence = _matches(text, SEQUENCE)
    urgent = _matches(text, URGENT)
    near = NEAR_DEADLINE.search(text)
    high = bool(urgent) or near is not None

    if no_action and not asks:
        evidence = [{"span": span, "kind": "no_action", "reason": reason} for span, reason in no_action[:2]]
        return Suggestion("fyi", "low", False, False, 0.8, evidence)

    if past and not asks:
        evidence = [{"span": span, "kind": "past_tense", "reason": "already done"} for span, reason in past[:2]]
        return Suggestion("fyi", "low", False, False, 0.75, evidence)

    if asks and not sequence:
        meaningful = sorted((item for item in asks if len(item[0].strip(" ?？。，,")) >= 2), key=lambda item: len(item[0]), reverse=True)
        evidence = [{"span": span, "kind": "ask", "reason": "request"} for span, _ in (meaningful or asks)[:2]]
        wants_todo = bool(tasks or high)
        if tasks:
            evidence += [{"span": span, "kind": "task", "reason": reason} for span, reason in tasks[:2]]
        return Suggestion("needs_reply", "high" if high else "normal", wants_todo, True, 0.8, evidence)

    if tasks:
        evidence = [{"span": span, "kind": "task", "reason": reason} for span, reason in tasks[:3]]
        priority = "high" if high else ("low" if vague else "normal")
        return Suggestion("action", priority, True, False, 0.8 if high else 0.7, evidence)

    if vague:
        evidence = [{"span": span, "kind": "vague", "reason": "no concrete commitment"} for span, _ in vague[:2]]
        return Suggestion("fyi", "low", False, False, 0.7, evidence)

    return Suggestion("fyi", "low", False, False, 0.5, [])


def evidence_supported(text, suggestion):
    """True when every evidence span appears verbatim in the text."""
    if not suggestion.evidence:
        return False
    return all(item["span"] in (text or "") for item in suggestion.evidence)


ENGINE_NAME = "rules-v2"
